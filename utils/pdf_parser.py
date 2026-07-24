"""
PDF -> Questions extractor.

Strategy:
1. Try direct text extraction with pdfplumber (fast, works for text-based PDFs).
2. If a page has little/no extractable text (i.e. it's a scanned image),
   fall back to OCR using pytesseract on a rasterized image of that page
   (via pdf2image). Supports Hindi (`hin`) + English (`eng`) if the
   corresponding tesseract language packs are installed (see Dockerfile).
3. Run the combined text through a regex-based MCQ parser that looks for:
      Q<number> ... (A) ... (B) ... (C) ... (D) ...
   and an optional "Answer Key" section like:
      Q1  (D)   Q2  (B)  ...
   to auto-mark correct answers when present.

This is heuristic/best-effort. Clean, well-formatted question papers
(numbered questions with A/B/C/D options) parse reliably; unusual layouts
may need manual review.
"""

import re
import pdfplumber

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

MIN_TEXT_CHARS_PER_PAGE = 40  # below this, treat page as "image-only" and OCR it


def _extract_raw_text(pdf_path: str):
    """Extract text page-by-page, OCR-ing pages that have no real text layer."""
    pages_text = []
    ocr_used = False

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if len(text.strip()) < MIN_TEXT_CHARS_PER_PAGE and OCR_AVAILABLE:
                try:
                    images = convert_from_path(pdf_path, first_page=i + 1, last_page=i + 1, dpi=250)
                    if images:
                        ocr_text = pytesseract.image_to_string(images[0], lang="hin+eng")
                        if len(ocr_text.strip()) > len(text.strip()):
                            text = ocr_text
                            ocr_used = True
                except Exception:
                    pass  # keep whatever text we already have
            pages_text.append(text)

    return "\n".join(pages_text), ocr_used


# Matches: Q1  <question text up to first option>
QUESTION_SPLIT_RE = re.compile(r"(?:^|\n)\s*Q\s*(\d{1,3})[.\)]?\s+", re.MULTILINE)

# Matches an option marker: (A) / (B) / (C) / (D)  -- also tolerant of A) B) etc.
OPTION_SPLIT_RE = re.compile(r"\(([A-D])\)\s*")

# Matches the answer key block, e.g. "Q1 (D)" repeated many times
ANSWER_KEY_ENTRY_RE = re.compile(r"Q\s*(\d{1,3})\s*[.\)]?\s*\(([A-D])\)")


def _parse_answer_key(full_text: str):
    """
    Looks for an 'Answer Key' section and extracts {question_number: 'A'/'B'/'C'/'D'}.
    If no explicit Answer Key header is found, we still scan the tail of the
    document for a dense cluster of 'Qn (X)' patterns, which is how most
    answer keys are formatted.
    """
    answer_key = {}

    idx = full_text.lower().rfind("answer key")
    search_zone = full_text[idx:] if idx != -1 else full_text[-4000:]

    for m in ANSWER_KEY_ENTRY_RE.finditer(search_zone):
        qnum, ans = int(m.group(1)), m.group(2)
        answer_key[qnum] = ans

    return answer_key


def _parse_questions(full_text: str):
    """Split full text into individual questions with 4 options each."""
    matches = list(QUESTION_SPLIT_RE.finditer(full_text))
    questions = []

    for idx, m in enumerate(matches):
        qnum = int(m.group(1))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        block = full_text[start:end].strip()

        opt_matches = list(OPTION_SPLIT_RE.finditer(block))
        if len(opt_matches) < 4:
            continue  # can't reliably parse without 4 options; skip malformed block

        question_text = block[:opt_matches[0].start()].strip()
        question_text = re.sub(r"\s+", " ", question_text)

        options = {}
        for oi, om in enumerate(opt_matches[:4]):
            label = om.group(1)
            opt_start = om.end()
            opt_end = opt_matches[oi + 1].start() if oi + 1 < len(opt_matches) else len(block)
            opt_text = block[opt_start:opt_end].strip()
            opt_text = re.sub(r"\s+", " ", opt_text)
            options[label] = opt_text

        if question_text and len(options) == 4:
            questions.append({
                "id": qnum,
                "text": question_text,
                "options": options,
                "correct": None,  # filled in later from answer key if available
            })

    return questions


def extract_questions_from_pdf(pdf_path: str):
    """
    Main entry point.
    Returns (questions_list, meta_dict)
    questions_list item shape:
        {"id": int, "text": str, "options": {"A":..,"B":..,"C":..,"D":..}, "correct": "A"|None}
    """
    full_text, ocr_used = _extract_raw_text(pdf_path)

    questions = _parse_questions(full_text)
    answer_key = _parse_answer_key(full_text)

    for q in questions:
        if q["id"] in answer_key:
            q["correct"] = answer_key[q["id"]]

    # Re-number sequentially 1..N for the quiz UI regardless of original Q numbers
    for i, q in enumerate(questions, start=1):
        q["original_id"] = q["id"]
        q["id"] = i

    meta = {
        "ocr_used": ocr_used,
        "answer_key_found": len(answer_key) > 0,
        "questions_with_known_answer": sum(1 for q in questions if q["correct"]),
    }

    return questions, meta
