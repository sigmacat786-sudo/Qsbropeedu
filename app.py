import os
import base64
import uuid
from datetime import datetime

from flask import Flask, request, render_template, jsonify, redirect, url_for, Response
from werkzeug.utils import secure_filename

from utils.db import get_db
from utils.pdf_parser import extract_questions_from_pdf

# ─── Basic Config ──────────────────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "https://your-app-name.onrender.com").rstrip("/")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/smartyms_uploads")
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_MB", "500")) * 1024 * 1024  # default 500MB safety cap

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

db = get_db()
quizzes_col = db["quizzes"]
attempts_col = db["attempts"]
images_col = db["question_images"]

MARK_CORRECT = 4
MARK_INCORRECT = -1
MARK_SKIPPED = 0


def get_performance_message(pct: float) -> str:
    if pct <= 25:
        return "Every expert was once a beginner—keep learning and never give up! 💪"
    elif pct <= 50:
        return "Good effort! Keep practicing, and you'll see great improvement. 📚"
    elif pct <= 75:
        return "Nice progress! You're getting stronger with every step. 🚀"
    elif pct <= 90:
        return "Excellent work! You're very close to mastering this topic. 🌟"
    else:
        return "Outstanding! You've truly mastered this quiz—keep shining! 🏆"


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

    # The quiz id used in the shareable link is the ORIGINAL filename
    # (without the .pdf extension), exactly as requested — not a random id.
    original_name = file.filename
    quiz_id = original_name.rsplit(".", 1)[0].strip()
    if not quiz_id:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    # Use a safe temp name on disk (this is just for processing, never shown to users)
    temp_name = secure_filename(original_name) or f"{uuid.uuid4().hex}.pdf"
    saved_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}_{temp_name}")
    file.save(saved_path)

    try:
        questions, meta = extract_questions_from_pdf(saved_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Extraction failed: {str(e)}"}), 500
    finally:
        try:
            os.remove(saved_path)
        except OSError:
            pass

    if not questions:
        return jsonify({
            "ok": False,
            "error": "Koi question detect nahi hua. PDF me Q1, Q2... (A)(B)(C)(D) format hona chahiye."
        }), 422

    # Re-uploading the same filename replaces the previous quiz + its images
    images_col.delete_many({"quiz_id": quiz_id})
    quizzes_col.delete_one({"_id": quiz_id})

    question_refs = []
    image_docs = []
    for q in questions:
        image_id = f"{quiz_id}::{q['id']}"
        image_docs.append({
            "_id": image_id,
            "quiz_id": quiz_id,
            "data": q["image_base64"],
            "mime": q["mime"],
        })
        question_refs.append({
            "id": q["id"],
            "image_id": image_id,
            "correct": q.get("correct"),
        })

    if image_docs:
        images_col.insert_many(image_docs)

    quiz_doc = {
        "_id": quiz_id,
        "title": quiz_id,
        "source_filename": original_name,
        "created_at": datetime.utcnow(),
        "total_questions": len(question_refs),
        "questions": question_refs,
        "meta": meta,
    }
    quizzes_col.insert_one(quiz_doc)

    play_link = f"{BASE_URL}/play?v={quiz_id}"

    return jsonify({
        "ok": True,
        "quiz_id": quiz_id,
        "title": quiz_doc["title"],
        "total_questions": len(question_refs),
        "play_link": play_link,
    })


# ─── Page: Generated link result page ──────────────────────────────────────
@app.route("/generated/<path:quiz_id>")
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
    return render_template("play.html", quiz_id=quiz_id, title=quiz["title"],
                            total_questions=quiz["total_questions"])


# ─── API: Fetch quiz questions (image references only, answers hidden) ────
@app.route("/api/quiz/<path:quiz_id>")
def api_quiz(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    safe_questions = [
        {"id": q["id"], "image_url": url_for("api_qimage", image_id=q["image_id"])}
        for q in quiz["questions"]
    ]
    return jsonify({
        "ok": True,
        "title": quiz["title"],
        "total_questions": quiz["total_questions"],
        "total_marks": quiz["total_questions"] * MARK_CORRECT,
        "questions": safe_questions,
    })


# ─── API: Serve a single cropped question image ────────────────────────────
@app.route("/api/qimage/<path:image_id>")
def api_qimage(image_id):
    doc = images_col.find_one({"_id": image_id})
    if not doc:
        return "Image not found", 404
    raw = base64.b64decode(doc["data"])
    return Response(raw, mimetype=doc.get("mime", "image/jpeg"))


# ─── API: Submit answers -> NEET-style marking + solutions ─────────────────
@app.route("/api/submit/<path:quiz_id>", methods=["POST"])
def api_submit(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    data = request.get_json(force=True) or {}
    user_answers = data.get("answers", {})   # {"1": "A", "2": null, ...}
    time_taken_seconds = data.get("time_taken_seconds", 0)
    name = (data.get("name") or "").strip()[:50]
    user_id = data.get("user_id", "anon-" + uuid.uuid4().hex[:8])

    correct = incorrect = not_answered = 0
    solutions = []

    for q in quiz["questions"]:
        qid = str(q["id"])
        chosen = user_answers.get(qid)
        correct_opt = q.get("correct")

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
            "image_url": url_for("api_qimage", image_id=q["image_id"]),
            "chosen": chosen,
            "correct": correct_opt,
            "status": status,
        })

    total = quiz["total_questions"]

    # NEET marking scheme: +4 correct, -1 incorrect, 0 skipped
    marks_obtained = (correct * MARK_CORRECT) + (incorrect * MARK_INCORRECT) + (not_answered * MARK_SKIPPED)
    total_marks = total * MARK_CORRECT
    percentage = round((marks_obtained / total_marks) * 100, 2) if total_marks else 0.0
    message = get_performance_message(percentage)

    attempt_doc = {
        "_id": uuid.uuid4().hex,
        "quiz_id": quiz_id,
        "user_id": user_id,
        "name": name,
        "answers": user_answers,
        "correct": correct,
        "incorrect": incorrect,
        "not_answered": not_answered,
        "marks_obtained": marks_obtained,
        "total_marks": total_marks,
        "percentage": percentage,
        "time_taken_seconds": time_taken_seconds,
        "submitted_at": datetime.utcnow(),
    }
    attempts_col.insert_one(attempt_doc)

    return jsonify({
        "ok": True,
        "name": name,
        "correct": correct,
        "incorrect": incorrect,
        "not_answered": not_answered,
        "total": total,
        "marks_obtained": marks_obtained,
        "total_marks": total_marks,
        "percentage": percentage,
        "message": message,
        "solutions": solutions,
    })


# ─── Health check (keeps Render happy, prevents sleep on ping services) ───
@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
