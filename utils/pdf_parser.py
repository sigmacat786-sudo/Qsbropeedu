"""
PDF -> Questions extractor (v2).

Key behaviours (per product requirements):
1. Scans EVERY page of the PDF, not just the first.
2. Uses the EXACT serial number printed in the PDF (Q1, Q2, ...) as the
   question id — this id is later used to match the Answer Key and to
   build the quiz navigation grid, so option/question mismatches can't
   happen even with 2-column layouts.
3. Because questions can contain diagrams, chemical structures, and
   mathematical notation that plain text extraction destroys, each
   question block (question text + all 4 options, exactly as printed)
   is CROPPED AS AN IMAGE straight from the rendered PDF page and stored
   as base64 JPEG. The quiz UI shows this image and lets the user pick
   A/B/C/D — nothing is re-typed, so nothing can mismatch.
4. The Answer Key section (usually the last 1-3 pages, headed
   "Answer Key") is detected and parsed separately as plain text into
   {question_number: "A"/"B"/"C"/"D"} and is NEVER treated as a
   question page.
5. Falls back to OCR (Tesseract, Hindi+English) automatically for any
   page whose text layer is too sparse (i.e. scanned/image-only pages).
"""

import re
import base64
import io

import pdfplumber

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

DPI = 200
MIN_TEXT_CHARS_PER_PAGE = 40

# Matches a standalone "Q1" / "Q1." / "Q1)" word token as extracted by pdfplumber/pytesseract
QSTART_RE = re.compile(r"^Q\.?(\d{1,3})[.\):]?$", re.IGNORECASE)

# Matches answer-key rows anywhere in plain text, e.g. "Q1  (D)"
ANSWER_KEY_ENTRY_RE = re.compile(r"Q\s*(\d{1,3})\s*[.\)]?\s*\(([A-D])\)")


# ─── Page rasterization ─────────────────────────────────────────────────
def _render_page_image(pdf_path, page_index, dpi=DPI):
    if not OCR_AVAILABLE:
        return None
    try:
        images = convert_from_path(pdf_path, first_page=page_index + 1,
                                    last_page=page_index + 1, dpi=dpi)
        return images[0] if images else None
    except Exception:
        return None


# ─── Text extraction (with OCR fallback) for a single page ────────────
def _page_text(page, pdf_path, page_index, page_img=None):
    text = page.extract_text() or ""
    if len(text.strip()) >= MIN_TEXT_CHARS_PER_PAGE or not OCR_AVAILABLE:
        return text
    try:
        img = page_img or _render_page_image(pdf_path, page_index)
        if img is not None:
            ocr_text = pytesseract.image_to_string(img, lang="hin+eng")
            if len(ocr_text.strip()) > len(text.strip()):
                return ocr_text
    except Exception:
        pass
    return text


# ─── Word-level positions (with OCR fallback) for a single page ───────
def _page_words_pixel_space(page, pdf_path, page_index, page_img):
    """
    Returns list of {"text": str, "x0": float, "top": float} in PIXEL
    coordinates (matching page_img's resolution), so cropping never
    needs an extra unit conversion.
    """
    scale = DPI / 72.0
    pl_words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    total_chars = sum(len(w["text"]) for w in pl_words)

    if total_chars >= MIN_TEXT_CHARS_PER_PAGE or not OCR_AVAILABLE or page_img is None:
        return [{"text": w["text"], "x0": w["x0"] * scale, "top": w["top"] * scale} for w in pl_words]

    # Sparse/no text layer -> OCR word boxes directly in pixel space (same image)
    try:
        data = pytesseract.image_to_data(page_img, lang="hin+eng", output_type=pytesseract.Output.DICT)
        words = []
        for i in range(len(data["text"])):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            words.append({"text": txt, "x0": float(data["left"][i]), "top": float(data["top"][i])})
        if words:
            return words
    except Exception:
        pass
    return [{"text": w["text"], "x0": w["x0"] * scale, "top": w["top"] * scale} for w in pl_words]


# ─── Locate "Qn" markers within a list of words, merging split tokens ──
def _find_question_starts(words):
    starts = []
    sorted_words = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    n = len(sorted_words)
    i = 0
    while i < n:
        w = sorted_words[i]
        m = QSTART_RE.match(w["text"])
        if m:
            starts.append((int(m.group(1)), w["top"]))
            i += 1
            continue
        if w["text"].upper() == "Q" and i + 1 < n:
            nxt_txt = sorted_words[i + 1]["text"].rstrip(".):")
            if nxt_txt.isdigit():
                starts.append((int(nxt_txt), w["top"]))
                i += 2
                continue
        i += 1
    starts.sort(key=lambda s: s[1])
    return starts


# ─── Crop every question block on a page into its own image ───────────
def _extract_questions_from_page(pdf_path, page, page_index):
    page_img = _render_page_image(pdf_path, page_index)
    if page_img is None:
        return []

    img_w, img_h = page_img.size
    words = _page_words_pixel_space(page, pdf_path, page_index, page_img)

    right_words = [w for w in words if w["x0"] >= img_w / 2]
    left_words = [w for w in words if w["x0"] < img_w / 2]

    if len(right_words) > 3:
        columns = [(left_words, 0, img_w / 2 - 6), (right_words, img_w / 2 + 6, img_w)]
    else:
        columns = [(words, 0, img_w)]

    results = []
    for col_words, cx0, cx1 in columns:
        starts = _find_question_starts(col_words)
        for idx, (qnum, top) in enumerate(starts):
            y_top = max(top - 6, 0)
            y_bottom = starts[idx + 1][1] - 4 if idx + 1 < len(starts) else img_h
            box = (max(cx0, 0), y_top, min(cx1, img_w), min(y_bottom, img_h))
            if box[3] - box[1] < 10 or box[2] - box[0] < 10:
                continue
            crop = page_img.crop(box).convert("RGB")
            buf = io.BytesIO()
            crop.save(buf, format="JPEG", quality=72)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            results.append({"id": qnum, "image_base64": b64, "mime": "image/jpeg", "page": page_index + 1})

    return results


# ─── Locate the Answer Key section and parse it ────────────────────────
def _find_answer_key(pdf, pdf_path):
    num_pages = len(pdf.pages)
    ak_start = None
    for i, page in enumerate(pdf.pages):
        text = _page_text(page, pdf_path, i)
        if "answer key" in text.lower():
            ak_start = i
            break

    answer_key = {}
    if ak_start is not None:
        tail_text = "\n".join(_page_text(pdf.pages[i], pdf_path, i) for i in range(ak_start, num_pages))
    else:
        tail_start = max(0, num_pages - 2)
        tail_text = "\n".join(_page_text(pdf.pages[i], pdf_path, i) for i in range(tail_start, num_pages))

    for m in ANSWER_KEY_ENTRY_RE.finditer(tail_text):
        answer_key[int(m.group(1))] = m.group(2)

    question_page_count = ak_start if ak_start is not None else num_pages
    return answer_key, question_page_count


# ─── Main entry point ───────────────────────────────────────────────────
def extract_questions_from_pdf(pdf_path: str):
    """
    Returns (questions, meta)
    questions item shape:
        {"id": int, "image_base64": str, "mime": "image/jpeg", "correct": "A"|None, "page": int}
    """
    questions = []
    with pdfplumber.open(pdf_path) as pdf:
        answer_key, question_page_count = _find_answer_key(pdf, pdf_path)

        for page_index in range(question_page_count):
            page = pdf.pages[page_index]
            page_questions = _extract_questions_from_page(pdf_path, page, page_index)
            questions.extend(page_questions)

    # de-duplicate by id, keep the first occurrence (in natural page order)
    seen = set()
    unique_questions = []
    for q in questions:
        if q["id"] in seen:
            continue
        seen.add(q["id"])
        q["correct"] = answer_key.get(q["id"])
        unique_questions.append(q)

    unique_questions.sort(key=lambda q: (q["page"], q["id"]))

    meta = {
        "answer_key_found": len(answer_key) > 0,
        "questions_with_known_answer": sum(1 for q in unique_questions if q["correct"]),
        "total_pages_scanned": question_page_count,
    }

    return unique_questions, meta
