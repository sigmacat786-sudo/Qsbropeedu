import os
import re
import unicodedata
import base64
import uuid
import functools
from datetime import datetime, timedelta

from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, session
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from utils.db import get_db
from utils.pdf_parser import extract_questions_from_pdf

# ─── Basic Config ──────────────────────────────────────────────────────────
# This is the domain shown/used in every generated quiz link (?v=...).
# It is intentionally hardcoded (not read from env) so the real backend
# domain (wherever this admin/upload service itself is deployed, e.g.
# smartyms-toxic-quiz-system.onrender.com) never leaks into shared links.
# To change it later, edit ONLY this one line:
PUBLIC_PLAY_BASE_URL = "https://smartyms-toxic-live-quiz-challlenge.onrender.com"

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/smartyms_uploads")
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_MB", "500")) * 1024 * 1024  # default 500MB safety cap

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# ─── Server-side Admin Auth (real fix — keys never reach the browser) ─────
# Previously these were hardcoded in static/js/main.js, which meant anyone
# opening DevTools -> Sources/Resources could read them directly. Now the
# check happens ONLY here, server-side, via the /login route below. The
# browser never receives OWNER_NAME / ADMIN_KEYS / VIP_KEYS in any form.
OWNER_NAME = "ViPvxMS10BRO"
ADMIN_KEYS = ["MS#Admin_R4!xQ8Lp7", "Core$MS_N6v!T2Zk9", "mS@Root_P8#Lm5Qx3"]
VIP_KEYS = ["ToXic#ViPR8m!4QxL7", "tOxic@VipN5v!9ZpK2", "ToXic$ViPX7#rT3Lm8"]

# ─── Per-Quiz Owner Dashboard Auth (hardcoded, same pattern as above) ─────
# These gate the "OWNER DASHBOARD" button on every generated quiz link.
# Checked ONLY here server-side — never sent to the browser.
OWNER_DASH_NICKNAME = "MSBrOHU68@YaAR"
OWNER_DASH_KEY = "ToXic#ViPxMSvBRO!9Qx7"
DELETE_CONFIRM_KEY = "Confirm#ViPxMSvBRO$6Kz2"

# SECRET_KEY signs the session cookie. Set a SECRET_KEY env var on Render
# so admin sessions survive restarts/redeploys — without it, a fallback is
# used and everyone is logged out whenever the process restarts.
app.secret_key = os.environ.get("SECRET_KEY", "smartyms-dev-fallback-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)


def admin_required(view):
    """Guards admin-only routes with the server-side session set by /login.
    API routes get a JSON 401 (so existing frontend .json()/data.ok error
    handling still works); page routes get redirected back to the login
    portal at '/'.
    """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            if request.path.startswith("/api/") or request.path == "/upload":
                return jsonify({"ok": False, "error": "Login required"}), 401
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    name_ok = data.get("owner_name") == OWNER_NAME
    admin_ok = data.get("admin_key") in ADMIN_KEYS
    vip_ok = data.get("vip_key") in VIP_KEYS

    if name_ok and admin_ok and vip_ok:
        session.permanent = True
        session["is_admin"] = True
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "Invalid Name / Admin Key / VIP Key. Check karo aur dobara try karo."}), 401


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

db = get_db()
quizzes_col = db["quizzes"]
attempts_col = db["attempts"]
images_col = db["question_images"]
drafts_col = db["drafts"]
# Tombstone collection: holds ONLY the quiz_id + deletion time for quizzes an
# owner has permanently deleted, so /play can show "QUIZ ENDED" instead of a
# generic "not found" — no question/answer/attempt data lives here.
deleted_quizzes_col = db["deleted_quizzes"]

MARK_CORRECT = 4
MARK_INCORRECT = -1
MARK_SKIPPED = 0


def owner_required(view):
    """Guards per-quiz Owner Dashboard routes. Session key is scoped to the
    specific quiz_id (set by /api/owner/<quiz_id>/verify-key), so unlocking
    one quiz's dashboard never unlocks another. The global admin session
    (set by /login) is also accepted, since the admin already proved who
    they are.
    """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        quiz_id = kwargs.get("quiz_id")
        if not (session.get("is_admin") or session.get(f"owner_{quiz_id}")):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Owner login required"}), 401
            return redirect(url_for("play", v=quiz_id))
        return view(*args, **kwargs)
    return wrapped


def _dense_rank_map(marks_list):
    """True/dense rank system: students with equal marks share the same
    rank, and the next distinct (lower) mark takes the very next rank
    number (no gaps) — e.g. 90,90,85,80 -> ranks 1,1,2,3.
    """
    distinct_sorted = sorted(set(marks_list), reverse=True)
    return {m: i + 1 for i, m in enumerate(distinct_sorted)}


# Signed, short-lived download tokens. Some mobile browsers hand file
# downloads (Content-Disposition: attachment) off to a separate native
# download manager that does NOT reliably resend the session cookie, which
# made "Download list" fail with "Owner login required" even though the
# owner was clearly logged in. These tokens let the two download routes
# authenticate the request WITHOUT depending on that cookie.
_download_serializer = URLSafeTimedSerializer(app.secret_key, salt="owner-download-link")


def _make_download_token(quiz_id):
    return _download_serializer.dumps({"quiz_id": quiz_id})


def _verify_download_token(token, quiz_id):
    if not token:
        return False
    try:
        data = _download_serializer.loads(token, max_age=600)  # valid 10 minutes
    except (BadSignature, SignatureExpired):
        return False
    return data.get("quiz_id") == quiz_id


def owner_required_or_token(view):
    """Same guard as owner_required, but also accepts a valid ?t= download
    token in place of the session cookie. Use ONLY for file-download routes.
    """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        quiz_id = kwargs.get("quiz_id")
        token = request.args.get("t")
        if (session.get("is_admin") or session.get(f"owner_{quiz_id}")
                or _verify_download_token(token, quiz_id)):
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Owner login required"}), 401
        return redirect(url_for("play", v=quiz_id))
    return wrapped


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
@admin_required
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
@admin_required
def edit_panel(draft_id):
    draft = drafts_col.find_one({"_id": draft_id}, {"title": 1})
    if not draft:
        return "Draft not found (already generated, or expired). Please upload again.", 404
    return render_template("edit.html", draft_id=draft_id, title=draft["title"])


# ─── API: fetch full draft for the edit panel ──────────────────────────────
@app.route("/api/draft/<path:draft_id>")
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
    deleted_quizzes_col.delete_one({"_id": quiz_id})  # clear any old "QUIZ ENDED" tombstone

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
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1, "questions": 1})
    if not quiz:
        return redirect(url_for("index"))
    play_link = f"{PUBLIC_PLAY_BASE_URL}/play?v={quiz_id}"
    return render_template("generated.html", title=quiz["title"],
                            total_questions=len(quiz["questions"]),
                            play_link=play_link)


# ─── Page: Play Quiz ────────────────────────────────────────────────────────
@app.route("/play")
def play():
    quiz_id = request.args.get("v")
    if not quiz_id:
        return "Missing quiz id (?v=...)", 400
    if deleted_quizzes_col.find_one({"_id": quiz_id}):
        return render_template("quiz_ended.html")
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1, "questions": 1})
    if not quiz:
        return "Quiz not found. Link galat hai ya quiz delete ho gaya.", 404
    scored_total = sum(1 for q in quiz["questions"] if not q.get("options_note"))
    return render_template("play.html", quiz_id=quiz_id, title=quiz["title"],
                            total_questions=len(quiz["questions"]),
                            total_marks=scored_total * MARK_CORRECT)


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
        "total_questions": len(quiz["questions"]),
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

    total = len(quiz["questions"])
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

    # Live Rank: recompute the dense rank across every attempt for this quiz
    # right now, so the student sees their true, up-to-the-second rank.
    all_marks = [a["marks_obtained"] for a in attempts_col.find({"quiz_id": quiz_id}, {"marks_obtained": 1})]
    rank = _dense_rank_map(all_marks).get(marks_obtained)

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
        "rank": rank,
    })


# ══════════════════════════════════════════════════════════════════════════
# ─── Owner Dashboard: per-quiz login, live scorecard, top 5, delete, update ─
# ══════════════════════════════════════════════════════════════════════════

# ─── API: Owner Dashboard step 1 — verify nickname ─────────────────────────
@app.route("/api/owner/<path:quiz_id>/verify-name", methods=["POST"])
def api_owner_verify_name(quiz_id):
    data = request.get_json(silent=True) or {}
    nickname = data.get("nickname") or ""
    if nickname == OWNER_DASH_NICKNAME:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid Owner Nick Name"}), 401


# ─── API: Owner Dashboard step 2 — verify key, unlock this quiz's session ──
@app.route("/api/owner/<path:quiz_id>/verify-key", methods=["POST"])
def api_owner_verify_key(quiz_id):
    data = request.get_json(silent=True) or {}
    nickname = data.get("nickname") or ""
    key = data.get("key") or ""
    if nickname == OWNER_DASH_NICKNAME and key == OWNER_DASH_KEY:
        session.permanent = True
        session[f"owner_{quiz_id}"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid Owner Key"}), 401


# ─── Page: Owner Dashboard hub ──────────────────────────────────────────────
@app.route("/owner/<path:quiz_id>")
@owner_required
def owner_dashboard(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1})
    if not quiz:
        return redirect(url_for("index"))
    play_link = f"{PUBLIC_PLAY_BASE_URL}/play?v={quiz_id}"
    return render_template("owner_dashboard.html", quiz_id=quiz_id, title=quiz["title"], play_link=play_link)


# ─── Page: Owner — Live Students Score card List ───────────────────────────
@app.route("/owner/<path:quiz_id>/scorecard")
@owner_required
def owner_scorecard_page(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1})
    if not quiz:
        return redirect(url_for("index"))
    download_token = _make_download_token(quiz_id)
    return render_template("owner_scorecard.html", quiz_id=quiz_id, title=quiz["title"], download_token=download_token)


# ─── API: live scorecard data (polled every ~2-3s by the frontend) ────────
@app.route("/api/owner/<path:quiz_id>/scorecard")
@owner_required
def api_owner_scorecard(quiz_id):
    attempts = list(attempts_col.find({"quiz_id": quiz_id}).sort("submitted_at", 1))
    rank_map = _dense_rank_map([a["marks_obtained"] for a in attempts])
    # Best-first ordering (highest marks on top); ties keep submission order.
    attempts.sort(key=lambda a: (-a["marks_obtained"], a["submitted_at"]))
    rows = []
    for i, a in enumerate(attempts, start=1):
        rows.append({
            "index": i,
            "rank": rank_map.get(a["marks_obtained"]),
            "name": a.get("name") or "Anonymous",
            "marks_obtained": a["marks_obtained"],
            "total_marks": a["total_marks"],
            "percentage": a["percentage"],
        })
    return jsonify({"ok": True, "rows": rows})


# ─── API: download scorecard as .txt ───────────────────────────────────────
@app.route("/api/owner/<path:quiz_id>/scorecard/download")
@owner_required_or_token
def api_owner_scorecard_download(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1})
    title = quiz["title"] if quiz else quiz_id
    attempts = list(attempts_col.find({"quiz_id": quiz_id}).sort("submitted_at", 1))
    rank_map = _dense_rank_map([a["marks_obtained"] for a in attempts])
    attempts.sort(key=lambda a: (-a["marks_obtained"], a["submitted_at"]))

    lines = [f"Students Score Card List — {title}", "=" * 40, ""]
    for i, a in enumerate(attempts, start=1):
        rank = rank_map.get(a["marks_obtained"])
        name = a.get("name") or "Anonymous"
        lines.append(
            f"{i}. Rank {rank} | {name} | Marks: {a['marks_obtained']}/{a['total_marks']} | {a['percentage']}%"
        )
    if not attempts:
        lines.append("No attempts yet.")

    content = "\n".join(lines)
    return Response(
        content, mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{quiz_id}_scorecard.txt"'}
    )


# ─── Page: Owner — Top 5 Students list ─────────────────────────────────────
@app.route("/owner/<path:quiz_id>/top5")
@owner_required
def owner_top5_page(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1})
    if not quiz:
        return redirect(url_for("index"))
    download_token = _make_download_token(quiz_id)
    return render_template("owner_top5.html", quiz_id=quiz_id, title=quiz["title"], download_token=download_token)


def _top5_rows(quiz_id):
    attempts = list(attempts_col.find({"quiz_id": quiz_id}))
    rank_map = _dense_rank_map([a["marks_obtained"] for a in attempts])
    attempts.sort(key=lambda a: (-a["marks_obtained"], a["submitted_at"]))
    top = attempts[:5]
    rows = []
    for i, a in enumerate(top, start=1):
        rows.append({
            "index": i,
            "name": a.get("name") or "Anonymous",
            "rank": rank_map.get(a["marks_obtained"]),
            "marks_obtained": a["marks_obtained"],
        })
    return rows


@app.route("/api/owner/<path:quiz_id>/top5")
@owner_required
def api_owner_top5(quiz_id):
    return jsonify({"ok": True, "rows": _top5_rows(quiz_id)})


@app.route("/api/owner/<path:quiz_id>/top5/download")
@owner_required_or_token
def api_owner_top5_download(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1})
    title = quiz["title"] if quiz else quiz_id
    rows = _top5_rows(quiz_id)
    lines = [f"Top 5 Students — {title}", "=" * 40, ""]
    for r in rows:
        lines.append(f"{r['index']}. {r['name']} | Rank {r['rank']} | Marks: {r['marks_obtained']}")
    if not rows:
        lines.append("No attempts yet.")
    content = "\n".join(lines)
    return Response(
        content, mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{quiz_id}_top5.txt"'}
    )


# ─── API: permanently delete a quiz link ───────────────────────────────────
@app.route("/api/owner/<path:quiz_id>/delete", methods=["POST"])
@owner_required
def api_owner_delete(quiz_id):
    data = request.get_json(silent=True) or {}
    confirm_key = data.get("confirm_key") or ""
    if confirm_key != DELETE_CONFIRM_KEY:
        return jsonify({"ok": False, "error": "Invalid Confirmation Key"}), 401

    images_col.delete_many({"quiz_id": quiz_id})
    quizzes_col.delete_one({"_id": quiz_id})
    attempts_col.delete_many({"quiz_id": quiz_id})
    deleted_quizzes_col.update_one({"_id": quiz_id}, {"$set": {"deleted_at": datetime.utcnow()}}, upsert=True)
    session.pop(f"owner_{quiz_id}", None)

    return jsonify({"ok": True})


# ─── Page: Owner — Update Quiz (re-edit a LIVE quiz, same UI as Edit Panel) ─
@app.route("/owner/<path:quiz_id>/update")
@owner_required
def owner_update_page(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id}, {"title": 1})
    if not quiz:
        return redirect(url_for("index"))
    return render_template("owner_update.html", quiz_id=quiz_id, title=quiz["title"])


@app.route("/api/quiz-edit/<path:quiz_id>")
@owner_required
def api_quiz_edit_get(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404
    questions = sorted(quiz["questions"], key=lambda q: q["id"])
    out = [{
        "id": q["id"],
        "image_url": url_for("api_qimage", image_id=q["image_id"]),
        "correct": q.get("correct"),
        "options_note": q.get("options_note"),
    } for q in questions]
    return jsonify({"ok": True, "title": quiz["title"], "questions": out})


@app.route("/api/quiz-edit/<path:quiz_id>/question/<int:qid>/reindex", methods=["POST"])
@owner_required
def api_quiz_edit_reindex(quiz_id, qid):
    data = request.get_json(force=True) or {}
    new_id = data.get("new_id")
    if not isinstance(new_id, int) or new_id <= 0:
        return jsonify({"ok": False, "error": "Index must be a positive whole number"}), 400

    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    questions = quiz["questions"]
    if new_id != qid and any(q["id"] == new_id for q in questions):
        return jsonify({"ok": False, "error": f"Question {new_id} already exists"}), 409

    target = next((q for q in questions if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404
    target["id"] = new_id

    quizzes_col.replace_one({"_id": quiz_id}, quiz)
    return jsonify({"ok": True})


@app.route("/api/quiz-edit/<path:quiz_id>/question/<int:qid>/move", methods=["POST"])
@owner_required
def api_quiz_edit_move(quiz_id, qid):
    data = request.get_json(force=True) or {}
    direction = data.get("direction")

    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    questions = sorted(quiz["questions"], key=lambda q: q["id"])
    ids = [q["id"] for q in questions]
    if qid not in ids:
        return jsonify({"ok": False, "error": "Question not found"}), 404

    idx = ids.index(qid)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(questions):
        return jsonify({"ok": False, "error": "Can't move further"}), 400

    questions[idx]["id"], questions[swap_idx]["id"] = questions[swap_idx]["id"], questions[idx]["id"]
    quiz["questions"] = questions
    quizzes_col.replace_one({"_id": quiz_id}, quiz)
    return jsonify({"ok": True})


@app.route("/api/quiz-edit/<path:quiz_id>/question/<int:qid>/image", methods=["POST"])
@owner_required
def api_quiz_edit_update_image(quiz_id, qid):
    if "image" not in request.files:
        return jsonify({"ok": False, "error": "No image received"}), 400
    file = request.files["image"]
    if file.filename == "" or not (file.content_type or "").startswith("image/"):
        return jsonify({"ok": False, "error": "Please upload an image file"}), 400

    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    target = next((q for q in quiz["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404

    raw = file.read()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = file.content_type or "image/jpeg"
    images_col.update_one({"_id": target["image_id"]}, {"$set": {"data": b64, "mime": mime}}, upsert=True)

    return jsonify({"ok": True, "image_url": url_for("api_qimage", image_id=target["image_id"])})


@app.route("/api/quiz-edit/<path:quiz_id>/question/<int:qid>/answer", methods=["POST"])
@owner_required
def api_quiz_edit_update_answer(quiz_id, qid):
    data = request.get_json(force=True) or {}
    correct = data.get("correct")
    if correct not in ("A", "B", "C", "D", None):
        return jsonify({"ok": False, "error": "Invalid answer"}), 400

    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    target = next((q for q in quiz["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404
    target["correct"] = correct

    quizzes_col.replace_one({"_id": quiz_id}, quiz)
    return jsonify({"ok": True})


@app.route("/api/quiz-edit/<path:quiz_id>/question/<int:qid>/options-note", methods=["POST"])
@owner_required
def api_quiz_edit_update_options_note(quiz_id, qid):
    data = request.get_json(force=True) or {}
    note = (data.get("options_note") or "").strip()[:300]

    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    target = next((q for q in quiz["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404
    target["options_note"] = note or None

    quizzes_col.replace_one({"_id": quiz_id}, quiz)
    return jsonify({"ok": True})


@app.route("/api/quiz-edit/<path:quiz_id>/question/<int:qid>", methods=["DELETE"])
@owner_required
def api_quiz_edit_delete_question(quiz_id, qid):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    target = next((q for q in quiz["questions"] if q["id"] == qid), None)
    if not target:
        return jsonify({"ok": False, "error": "Question not found"}), 404

    quiz["questions"] = [q for q in quiz["questions"] if q["id"] != qid]
    quiz["total_questions"] = len(quiz["questions"])
    quizzes_col.replace_one({"_id": quiz_id}, quiz)
    images_col.delete_one({"_id": target["image_id"]})

    return jsonify({"ok": True})


@app.route("/api/quiz-edit/<path:quiz_id>/question", methods=["POST"])
@owner_required
def api_quiz_edit_add_question(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404

    try:
        new_id = int(request.form.get("new_id", ""))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Index must be a whole number"}), 400
    if new_id <= 0:
        return jsonify({"ok": False, "error": "Index must be a positive whole number"}), 400
    if any(q["id"] == new_id for q in quiz["questions"]):
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
    image_id = f"{quiz_id}::{new_id}::{uuid.uuid4().hex[:6]}"
    images_col.insert_one({"_id": image_id, "quiz_id": quiz_id, "data": b64, "mime": mime})

    quiz["questions"].append({
        "id": new_id,
        "image_id": image_id,
        "correct": correct,
        "options_note": options_note,
    })
    quiz["total_questions"] = len(quiz["questions"])
    quizzes_col.replace_one({"_id": quiz_id}, quiz)

    return jsonify({"ok": True, "image_url": url_for("api_qimage", image_id=image_id)})


@app.route("/api/quiz-edit/<path:quiz_id>/update-now", methods=["POST"])
@owner_required
def api_quiz_edit_update_now(quiz_id):
    quiz = quizzes_col.find_one({"_id": quiz_id})
    if not quiz:
        return jsonify({"ok": False, "error": "Quiz not found"}), 404
    if not quiz["questions"]:
        return jsonify({"ok": False, "error": "Quiz me kam se kam ek question hona chahiye"}), 400
    quizzes_col.update_one({"_id": quiz_id}, {"$set": {"total_questions": len(quiz["questions"])}})
    return jsonify({"ok": True})


# ─── Health check (keeps Render happy, prevents sleep on ping services) ───
@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
