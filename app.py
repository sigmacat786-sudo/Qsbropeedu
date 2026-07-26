import os
import re
import unicodedata
import base64
import uuid
from datetime import datetime

from flask import Flask, request, render_template, jsonify, redirect, url_for, Response
from werkzeug.utils import secure_filename

from utils.db import get_db
from utils.pdf_parser import extract_questions_from_pdf

# ─── Basic Config ──────────────────────────────────────────────────────────
# This is the domain shown/used in every generated quiz link (?v=...).
# It is intentionally hardcoded (not read from env) so the real backend
# domain (wherever this admin/upload service itself is deployed, e.g.
# smartyms-toxic-quiz-system.onrender.com) never leaks into shared links.
# To change it later, edit ONLY this one line:
PUBLIC_PLAY_BASE_URL = "https://learnwithpw-recorded.onrender.com"

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/smartyms_uploads")
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_MB", "500")) * 1024 * 1024  # default 500MB safety cap

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

db = get_db()
quizzes_col = db["quizzes"]
attempts_col = db["attempts"]
images_col = db["question_images"]
drafts_col = db["drafts"]

MARK_CORRECT = 4
MARK_INCORRECT = -1
MARK_SKIPPED = 0


def _sanitize_quiz_id(name: str) -> str:
    """
    Turns a filename (or user-edited name) into a URL-safe quiz id:
    spaces become hyphens, unsafe characters are dropped, capped at 100 chars.
    Uses Unicode categories (not \\w) so combining marks used by scripts
    like Hindi (e.g. matras in "उसने") are correctly kept, not stripped.
    """
    name = (name or "").strip()
    name = re.sub(r"\s+", "-", name)
    kept = []
    for ch in name:
        if ch in ("-", "_"):
            kept.append(ch)
            continue
        category = unicodedata.category(ch)  # 'Lx'=letter, 'Mx'=mark, 'Nx'=number
        if category[0] in ("L", "M", "N"):
            kept.append(ch)
        # anything else (currency/other symbols, punctuation, emoji) is dropped
    return "".join(kept)[:100]


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

    # The quiz id used in the shareable link is derived from the filename
    # (without .pdf), OR from the user-edited name if they changed it on
    # the upload page — spaces are auto-converted to hyphens either way
    # since URLs can't contain raw spaces.
    original_name = file.filename
    desired_name = (request.form.get("desired_name") or "").strip()
    if desired_name:
        quiz_id = _sanitize_quiz_id(desired_name)
    else:
        quiz_id = _sanitize_quiz_id(original_name.rsplit(".", 1)[0])
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

    # Store this as a DRAFT (not a live quiz yet) — the admin reviews/edits
    # it on the new Edit Panel before anything becomes a real shareable
    # quiz. quiz_id_hint carries the chosen name forward as the default
    # final quiz id once they hit "Submit & Generate Quiz".
    draft_id = uuid.uuid4().hex[:12]
    question_list = []
    image_docs = []
    for q in questions:
        image_id = f"draft::{draft_id}::{q['id']}"
        image_docs.append({
            "_id": image_id,
            "data": q["image_base64"],
            "mime": q["mime"],
        })
        question_list.append({
            "id": q["id"],
            "image_id": image_id,
            "correct": q.get("correct"),
            "options_note": None,
        })

    if image_docs:
        images_col.insert_many(image_docs)

    draft_doc = {
        "_id": draft_id,
        "quiz_id_hint": quiz_id,
        "title": quiz_id,
        "source_filename": original_name,
        "created_at": datetime.utcnow(),
        "questions": question_list,
    }
    drafts_col.insert_one(draft_doc)

    return jsonify({
        "ok": True,
        "draft_id": draft_id,
        "total_questions": len(question_list),
    })


# ─── Page: Edit Panel (review/fix everything before generating the link) ──
@app.route("/edit/<path:draft_id>")
def edit_panel(draft_id):
    draft = drafts_col.find_one({"_id": draft_id}, {"title": 1})
    if not draft:
        return "Draft not found (already generated, or expired). Please upload again.", 404
    return render_template("edit.html", draft_id=draft_id, title=draft["title"])


# ─── API: fetch full draft for the edit panel ──────────────────────────────
@app.route("/api/draft/<path:draft_id>")
def api_get_draft(draft_id):
    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    questions = sorted(draft["questions"], key=lambda q: q["id"])
    out = [{
        "id": q["id"],
        "image_url": url_for("api_qimage", image_id=q["image_id"]),
        "correct": q.get("correct"),
        "options_note": q.get("options_note"),
    } for q in questions]

    return jsonify({"ok": True, "title": draft["title"], "questions": out})


# ─── API: change a question's index number (with duplicate check) ─────────
@app.route("/api/draft/<path:draft_id>/question/<int:qid>/reindex", methods=["POST"])
def api_draft_reindex(draft_id, qid):
    data = request.get_json(force=True) or {}
    new_id = data.get("new_id")
    if not isinstance(new_id, int) or new_id <= 0:
        return jsonify({"ok": False, "error": "Index must be a positive whole number"}), 400

    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    questions = draft["questions"]
    if new_id != qid and any(q["id"] == new_id for q in questions):
        return jsonify({"ok": False, "error": f"Question {new_id} already exists"}), 409

    target = next((q for q in questions if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404
    target["id"] = new_id

    drafts_col.replace_one({"_id": draft_id}, draft)
    return jsonify({"ok": True})


# ─── API: nudge a question up/down (swaps index with its neighbour) ───────
@app.route("/api/draft/<path:draft_id>/question/<int:qid>/move", methods=["POST"])
def api_draft_move(draft_id, qid):
    data = request.get_json(force=True) or {}
    direction = data.get("direction")

    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    questions = sorted(draft["questions"], key=lambda q: q["id"])
    ids = [q["id"] for q in questions]
    if qid not in ids:
        return jsonify({"ok": False, "error": "Question not found"}), 404

    idx = ids.index(qid)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(questions):
        return jsonify({"ok": False, "error": "Can't move further"}), 400

    questions[idx]["id"], questions[swap_idx]["id"] = questions[swap_idx]["id"], questions[idx]["id"]
    draft["questions"] = questions
    drafts_col.replace_one({"_id": draft_id}, draft)
    return jsonify({"ok": True})


# ─── API: replace a question's image ───────────────────────────────────────
@app.route("/api/draft/<path:draft_id>/question/<int:qid>/image", methods=["POST"])
def api_draft_update_image(draft_id, qid):
    if "image" not in request.files:
        return jsonify({"ok": False, "error": "No image received"}), 400
    file = request.files["image"]
    if file.filename == "" or not (file.content_type or "").startswith("image/"):
        return jsonify({"ok": False, "error": "Please upload an image file"}), 400

    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    target = next((q for q in draft["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404

    raw = file.read()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = file.content_type or "image/jpeg"
    images_col.update_one({"_id": target["image_id"]}, {"$set": {"data": b64, "mime": mime}}, upsert=True)

    return jsonify({"ok": True, "image_url": url_for("api_qimage", image_id=target["image_id"])})


# ─── API: edit the correct-answer key for one question ────────────────────
@app.route("/api/draft/<path:draft_id>/question/<int:qid>/answer", methods=["POST"])
def api_draft_update_answer(draft_id, qid):
    data = request.get_json(force=True) or {}
    correct = data.get("correct")
    if correct not in ("A", "B", "C", "D", None):
        return jsonify({"ok": False, "error": "Invalid answer"}), 400

    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    target = next((q for q in draft["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404
    target["correct"] = correct

    drafts_col.replace_one({"_id": draft_id}, draft)
    return jsonify({"ok": True})


# ─── API: edit the options note (e.g. mark a question as subjective) ──────
@app.route("/api/draft/<path:draft_id>/question/<int:qid>/options-note", methods=["POST"])
def api_draft_update_options_note(draft_id, qid):
    data = request.get_json(force=True) or {}
    note = (data.get("options_note") or "").strip()[:300]

    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    target = next((q for q in draft["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404
    target["options_note"] = note or None

    drafts_col.replace_one({"_id": draft_id}, draft)
    return jsonify({"ok": True})


# ─── API: remove a question from the draft ─────────────────────────────────
@app.route("/api/draft/<path:draft_id>/question/<int:qid>", methods=["DELETE"])
def api_draft_delete_question(draft_id, qid):
    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    target = next((q for q in draft["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404

    draft["questions"] = [q for q in draft["questions"] if q["id"] != qid]
    drafts_col.replace_one({"_id": draft_id}, draft)
    images_col.delete_one({"_id": target["image_id"]})

    return jsonify({"ok": True})


# ─── API: add a brand-new question to the draft ────────────────────────────
@app.route("/api/draft/<path:draft_id>/question", methods=["POST"])
def api_draft_add_question(draft_id):
    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404

    try:
        new_id = int(request.form.get("new_id", ""))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Index must be a whole number"}), 400
    if new_id <= 0:
        return jsonify({"ok": False, "error": "Index must be a positive whole number"}), 400
    if any(q["id"] == new_id for q in draft["questions"]):
        return jsonify({"ok": False, "error": f"Question {new_id} already exists"}), 409

    if "image" not in request.files:
        return jsonify({"ok": False, "error": "Question image required"}), 400
    file = request.files["image"]
    if file.filename == "" or not (file.content_type or "").startswith("image/"):
        return jsonify({"ok": False, "error": "Please upload an image file"}), 400

    correct = request.form.get("correct") or None
    if correct not in ("A", "B", "C", "D", None):
        return jsonify({"ok": False, "error": "Invalid answer"}), 400
    options_note = (request.form.get("options_note") or "").strip()[:300] or None

    raw = file.read()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = file.content_type or "image/jpeg"
    image_id = f"draft::{draft_id}::{new_id}::{uuid.uuid4().hex[:6]}"
    images_col.insert_one({"_id": image_id, "data": b64, "mime": mime})

    draft["questions"].append({
        "id": new_id,
        "image_id": image_id,
        "correct": correct,
        "options_note": options_note,
    })
    drafts_col.replace_one({"_id": draft_id}, draft)

    return jsonify({"ok": True, "image_url": url_for("api_qimage", image_id=image_id)})


# ─── API: "Submit & Generate Quiz" — turn the draft into a real live quiz ──
@app.route("/api/draft/<path:draft_id>/finalize", methods=["POST"])
def api_draft_finalize(draft_id):
    draft = drafts_col.find_one({"_id": draft_id})
    if not draft:
        return jsonify({"ok": False, "error": "Draft not found"}), 404
    if not draft["questions"]:
        return jsonify({"ok": False, "error": "Add at least one question before generating"}), 400

    data = request.get_json(silent=True) or {}
    desired_name = (data.get("desired_name") or "").strip()
    quiz_id = _sanitize_quiz_id(desired_name) if desired_name else draft["quiz_id_hint"]
    if not quiz_id:
        return jsonify({"ok": False, "error": "Invalid quiz name"}), 400

    # Re-generating under the same name replaces the previous live quiz
    images_col.delete_many({"quiz_id": quiz_id})
    quizzes_col.delete_one({"_id": quiz_id})

    question_refs = []
    for q in sorted(draft["questions"], key=lambda x: x["id"]):
        final_image_id = f"{quiz_id}::{q['id']}"
        img_doc = images_col.find_one({"_id": q["image_id"]})
        if img_doc:
            images_col.update_one(
                {"_id": final_image_id},
                {"$set": {"quiz_id": quiz_id, "data": img_doc["data"], "mime": img_doc["mime"]}},
                upsert=True,
            )
        question_refs.append({
            "id": q["id"],
            "image_id": final_image_id,
            "correct": q.get("correct"),
            "options_note": q.get("options_note"),
        })

    quiz_doc = {
        "_id": quiz_id,
        "title": quiz_id,
        "source_filename": draft.get("source_filename"),
        "created_at": datetime.utcnow(),
        "total_questions": len(question_refs),
        "questions": question_refs,
    }
    quizzes_col.insert_one(quiz_doc)

    # Clean up the now-migrated draft images + the draft document itself
    for q in draft["questions"]:
        images_col.delete_one({"_id": q["image_id"]})
    drafts_col.delete_one({"_id": draft_id})

    play_link = f"{PUBLIC_PLAY_BASE_URL}/play?v={quiz_id}"
    return jsonify({"ok": True, "quiz_id": quiz_id, "play_link": play_link})


# ─── Page: Generated link result page ──────────────────────────────────────
@app.route("/generated/<path:quiz_id>")
def generated(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1, "total_questions": 1})
    if not quiz:
        return redirect(url_for("index"))
    play_link = f"{PUBLIC_PLAY_BASE_URL}/play?v={quiz_id}"
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
        {
            "id": q["id"],
            "image_url": url_for("api_qimage", image_id=q["image_id"]),
            "options_note": q.get("options_note"),
        }
        for q in quiz["questions"]
    ]
    scored_total = sum(1 for q in quiz["questions"] if not q.get("options_note"))
    return jsonify({
        "ok": True,
        "title": quiz["title"],
        "total_questions": quiz["total_questions"],
        "total_marks": scored_total * MARK_CORRECT,
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
        options_note = q.get("options_note")
        is_subjective = bool(options_note)

        if is_subjective:
            # Subjective questions have no A/B/C/D to pick, so they can
            # never be marked correct/incorrect — they simply don't
            # contribute to the marks total (but still count in the
            # overall question count).
            status = "subjective"
            not_answered += 1
        elif chosen is None or chosen == "":
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
            "options_note": options_note,
        })

    total = quiz["total_questions"]
    subjective_count = sum(1 for q in quiz["questions"] if q.get("options_note"))
    scored_total = total - subjective_count

    # NEET marking scheme: +4 correct, -1 incorrect, 0 skipped/subjective.
    # total_marks only counts questions that actually have real options —
    # subjective questions can never add or subtract marks.
    marks_obtained = (correct * MARK_CORRECT) + (incorrect * MARK_INCORRECT)
    total_marks = scored_total * MARK_CORRECT
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
        "subjective_count": subjective_count,
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
