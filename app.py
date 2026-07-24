import os
import io
import uuid
import threading
from datetime import datetime

from flask import Flask, request, render_template, jsonify, redirect, url_for
from werkzeug.utils import secure_filename

from utils.db import get_db
from utils.pdf_parser import extract_questions_from_pdf

# ─── Basic Config ──────────────────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "https://your-app-name.onrender.com")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/smartyms_uploads")
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_MB", "500")) * 1024 * 1024  # default 500MB safety cap

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

db = get_db()
quizzes_col = db["quizzes"]
attempts_col = db["attempts"]


# ─── Page: Upload (this is our "index.html" landing page) ─────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ─── API: Upload PDF -> OCR/Extract -> Save to Mongo -> return quiz id ────
@app.route("/upload", methods=["POST"])
def upload():
    if "pdf_file" not in request.files:
        return jsonify({"ok": False, "error": "No file received"}), 400

    file = request.files["pdf_file"]
    if file.filename == "":
        return jsonify({"ok": False, "error": "Empty filename"}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"ok": False, "error": "Only PDF files are supported"}), 400

    filename = secure_filename(file.filename)
    quiz_id = uuid.uuid4().hex[:12]  # short unique id used in ?v= link
    saved_path = os.path.join(UPLOAD_FOLDER, f"{quiz_id}_{filename}")
    file.save(saved_path)

    try:
        questions, meta = extract_questions_from_pdf(saved_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Extraction failed: {str(e)}"}), 500
    finally:
        # cleanup raw pdf, we only need extracted structured data
        try:
            os.remove(saved_path)
        except OSError:
            pass

    if not questions:
        return jsonify({
            "ok": False,
            "error": "Koi question detect nahi hua. PDF me Q1, Q2... (A)(B)(C)(D) format hona chahiye."
        }), 422

    quiz_doc = {
        "_id": quiz_id,
        "title": os.path.splitext(filename)[0],
        "source_filename": filename,
        "created_at": datetime.utcnow(),
        "total_questions": len(questions),
        "questions": questions,   # each: {id, text, options[A-D], correct (or None), image_ref}
        "meta": meta,
    }
    quizzes_col.insert_one(quiz_doc)

    play_link = f"{BASE_URL}/play?v={quiz_id}"

    return jsonify({
        "ok": True,
        "quiz_id": quiz_id,
        "title": quiz_doc["title"],
        "total_questions": len(questions),
        "play_link": play_link,
    })


# ─── Page: Generated link result page ──────────────────────────────────────
@app.route("/generated/<quiz_id>")
def generated(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1, "total_questions": 1})
    if not quiz:
        return redirect(url_for("index"))
    play_link = f"{BASE_URL}/play?v={quiz_id}"
    return render_template("generated.html", title=quiz["title"],
                            total_questions=quiz["total_questions"],
                            play_link=play_link)


# ─── Page: Play Quiz ────────────────────────────────────────────────────────
@app.route("/play")
def play():
    quiz_id = request.args.get("v")
    if not quiz_id:
        return "Missing quiz id (?v=...)", 400
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1, "total_questions": 1})
    if not quiz:
        return "Quiz not found. Link galat hai ya quiz delete ho gaya.", 404
    return render_template("play.html", quiz_id=quiz_id, title=quiz["title"])


# ─── API: Fetch quiz questions (options only, correct answer hidden) ──────
@app.route("/api/quiz/<quiz_id>")
def api_quiz(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    safe_questions = [
        {
            "id": q["id"],
            "text": q["text"],
            "options": q["options"],
        }
        for q in quiz["questions"]
    ]
    return jsonify({
        "ok": True,
        "title": quiz["title"],
        "total_questions": quiz["total_questions"],
        "questions": safe_questions,
    })


# ─── API: Submit answers -> score + solutions ──────────────────────────────
@app.route("/api/submit/<quiz_id>", methods=["POST"])
def api_submit(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    data = request.get_json(force=True) or {}
    user_answers = data.get("answers", {})   # {"1": "A", "2": null, ...}
    time_taken_seconds = data.get("time_taken_seconds", 0)
    user_id = data.get("user_id", "anon-" + uuid.uuid4().hex[:8])

    correct = incorrect = not_answered = 0
    solutions = []

    for q in quiz["questions"]:
        qid = str(q["id"])
        chosen = user_answers.get(qid)
        correct_opt = q.get("correct")  # "A"/"B"/"C"/"D" or None if unknown

        status = "skipped"
        if chosen is None or chosen == "":
            not_answered += 1
            status = "skipped"
        elif correct_opt is not None and chosen == correct_opt:
            correct += 1
            status = "correct"
        else:
            incorrect += 1
            status = "incorrect"

        solutions.append({
            "id": q["id"],
            "text": q["text"],
            "options": q["options"],
            "chosen": chosen,
            "correct": correct_opt,
            "status": status,
        })

    total = quiz["total_questions"]
    attempted = total - not_answered
    accuracy = round((correct / attempted) * 100, 2) if attempted else 0.0

    attempt_doc = {
        "_id": uuid.uuid4().hex,
        "quiz_id": quiz_id,
        "user_id": user_id,
        "answers": user_answers,
        "correct": correct,
        "incorrect": incorrect,
        "not_answered": not_answered,
        "accuracy": accuracy,
        "time_taken_seconds": time_taken_seconds,
        "submitted_at": datetime.utcnow(),
    }
    attempts_col.insert_one(attempt_doc)

    return jsonify({
        "ok": True,
        "correct": correct,
        "incorrect": incorrect,
        "not_answered": not_answered,
        "total": total,
        "accuracy": accuracy,
        "solutions": solutions,
    })


# ─── Health check (keeps Render happy, prevents sleep on ping services) ───
@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
