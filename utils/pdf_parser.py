"""
PDF -> Questions extractor (v3).

This version fixes three things reported against v2:

1. CONTENT-AWARE CROPPING (no more clipped tops/bottoms, no more oversized
   images). Instead of cropping "from one Q-marker to the next", we now:
     - Cluster words into visual text LINES (using bounding boxes).
     - Find the line where the question starts (e.g. "Q6").
     - Pad ~2 line-heights above and below the actual content, but never
       past the previous/next question's own content (so padding can't
       swallow a neighbour's text).

2. MULTI-PART QUESTIONS. If a question's 4 options aren't all found before
   the column/page ends, the question is left "pending": whatever was
   captured becomes piece #1, and the parser keeps reading into the next
   column/page. Once all 4 options are found (or a new "Qn" marker forces
   a stop), all pieces are vertically stitched into ONE final image with a
   small gap between them — no text is re-typed, just images glued
   together in reading order.

3. PROMO-TEXT EXCLUSION. Footer lines like "Master NCERT with PW Books
   APP" are detected and treated as a hard stop — they are never included
   in any cropped/stitched image.

Answer Key detection/parsing (page(s) headed "Answer Key", possibly
spanning multiple pages and 2 columns) is unchanged from v2, since that
part was already working well.
"""

import re
import base64
import io

import pdfplumber
from PIL import Image

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

DPI = 200
MIN_TEXT_CHARS_PER_PAGE = 40
STITCH_GAP_PX = 14          # small gap between stitched multi-part image pieces
LINE_CLUSTER_TOL_RATIO = 0.5

# A line's first token(s) identify a new question, e.g. "Q6", "Q19."
QLINE_START_RE = re.compile(r"^Q\.?(\d{1,3})[.\):]?\b", re.IGNORECASE)

# Any "(A)" / "(B)" / "(C)" / "(D)" marker anywhere in a chunk of text
OPTION_RE = re.compile(r"\(([A-D])\)")

# Answer-key rows anywhere in plain text, e.g. "Q1  (D)"
ANSWER_KEY_ENTRY_RE = re.compile(r"Q\s*(\d{1,3})\s*[.\)]?\s*\(([A-D])\)")

# Footer/promo line that must NEVER appear inside a cropped question image.
# Broad on purpose: the footer is horizontally centered on the page, so a
# strict left/right column split can slice it in two — matching on either
# fragment ("ncert" or "pw books") means we still catch it either side.
PROMO_TEXT_RE = re.compile(r"ncert|pw\s*books", re.IGNORECASE)


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
    Returns list of {"text","x0","top","bottom"} in PIXEL coordinates
    (matching page_img's resolution).
    """
    scale = DPI / 72.0
    pl_words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    total_chars = sum(len(w["text"]) for w in pl_words)

    if total_chars >= MIN_TEXT_CHARS_PER_PAGE or not OCR_AVAILABLE or page_img is None:
        return [
            {"text": w["text"], "x0": w["x0"] * scale, "top": w["top"] * scale, "bottom": w["bottom"] * scale}
            for w in pl_words
        ]

    try:
        data = pytesseract.image_to_data(page_img, lang="hin+eng", output_type=pytesseract.Output.DICT)
        words = []
        for i in range(len(data["text"])):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            top = float(data["top"][i])
            height = float(data["height"][i])
            words.append({"text": txt, "x0": float(data["left"][i]), "top": top, "bottom": top + height})
        if words:
            return words
    except Exception:
        pass
    return [
        {"text": w["text"], "x0": w["x0"] * scale, "top": w["top"] * scale, "bottom": w["bottom"] * scale}
        for w in pl_words
    ]


# ─── Group words into visual lines ──────────────────────────────────────
def _cluster_lines(words):
    """Returns list of {"top","bottom","text"} sorted top-to-bottom."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    heights = [w["bottom"] - w["top"] for w in sorted_words if w["bottom"] > w["top"]]
    median_h = sorted(heights)[len(heights) // 2] if heights else 14.0
    tol = max(median_h * LINE_CLUSTER_TOL_RATIO, 4.0)

    lines_raw = []
    current = [sorted_words[0]]
    cur_top = sorted_words[0]["top"]
    cur_bottom = sorted_words[0]["bottom"]

    for w in sorted_words[1:]:
        if w["top"] <= cur_bottom + tol:
            current.append(w)
            cur_top = min(cur_top, w["top"])
            cur_bottom = max(cur_bottom, w["bottom"])
        else:
            lines_raw.append((cur_top, cur_bottom, current))
            current = [w]
            cur_top = w["top"]
            cur_bottom = w["bottom"]
    lines_raw.append((cur_top, cur_bottom, current))

    result = []
    for top, bottom, line_words in lines_raw:
        ordered = sorted(line_words, key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in ordered)
        result.append({"top": top, "bottom": bottom, "text": text})
    return result


def _line_height_estimate(lines):
    if len(lines) < 2:
        return 22.0
    diffs = [lines[i + 1]["top"] - lines[i]["top"] for i in range(len(lines) - 1)]
    diffs = [d for d in diffs if d > 0]
    if not diffs:
        return 22.0
    diffs.sort()
    return diffs[len(diffs) // 2]


def _detect_qstart(line_text):
    m = QLINE_START_RE.match(line_text.strip())
    return int(m.group(1)) if m else None


# ─── Build ordered column "blocks" for a page (left col, then right col) ──
def _get_blocks_for_page(pdf_path, page, page_index):
    page_img = _render_page_image(pdf_path, page_index)
    if page_img is None:
        return []
    img_w, img_h = page_img.size
    words = _page_words_pixel_space(page, pdf_path, page_index, page_img)

    # Page-wide footer detection FIRST (before splitting into columns). The
    # footer is horizontally centered, so it can straddle the column split —
    # detecting it once across the whole page and clipping both columns to
    # the same y-position avoids leaking half of it into either column.
    page_lines = _cluster_lines(words)
    footer_top_y = None
    for ln in page_lines:
        if PROMO_TEXT_RE.search(ln["text"]):
            footer_top_y = ln["top"] if footer_top_y is None else min(footer_top_y, ln["top"])

    right_words = [w for w in words if w["x0"] >= img_w / 2]
    left_words = [w for w in words if w["x0"] < img_w / 2]

    if len(right_words) > 3:
        col_defs = [(left_words, 0, img_w / 2 - 6), (right_words, img_w / 2 + 6, img_w)]
    else:
        col_defs = [(words, 0, img_w)]

    blocks = []
    for col_words, cx0, cx1 in col_defs:
        lines = _cluster_lines(col_words)

        # Per-column safety net (in case the footer fragment on THIS column
        # alone also matches on its own).
        promo_idx = len(lines)
        for i, ln in enumerate(lines):
            if PROMO_TEXT_RE.search(ln["text"]):
                promo_idx = i
                break
        usable_lines = lines[:promo_idx]

        # Page-wide clip: drop any line at/after the detected footer y-position.
        if footer_top_y is not None:
            usable_lines = [ln for ln in usable_lines if ln["top"] < footer_top_y - 2]
            usable_bottom = footer_top_y - 2
        else:
            usable_bottom = img_h

        blocks.append({
            "lines": usable_lines,
            "img": page_img,
            "cx0": cx0,
            "cx1": cx1,
            "img_h": img_h,
            "usable_bottom": usable_bottom,
            "page": page_index + 1,
        })
    return blocks


# ─── Cropping / stitching helpers ───────────────────────────────────────
def _crop_region(block, top, bottom):
    img = block["img"]
    box = (max(block["cx0"], 0), max(top, 0), min(block["cx1"], img.width), min(bottom, block["img_h"]))
    if box[3] - box[1] < 8 or box[2] - box[0] < 8:
        return None
    return img.crop(box).convert("RGB")


def _stitch_images(pieces):
    pieces = [p for p in pieces if p is not None]
    if not pieces:
        return None
    if len(pieces) == 1:
        return pieces[0]
    width = max(p.width for p in pieces)
    total_height = sum(p.height for p in pieces) + STITCH_GAP_PX * (len(pieces) - 1)
    canvas = Image.new("RGB", (width, total_height), "white")
    y = 0
    for p in pieces:
        canvas.paste(p, (0, y))
        y += p.height + STITCH_GAP_PX
    return canvas


# ─── Main per-document question walker ──────────────────────────────────
def _extract_all_questions(pdf_path, pdf, question_page_count):
    questions_out = []   # [{"id", "image": PIL.Image, "page": int}]
    pending = None       # {"id","pieces":[...],"found_options":set(),"page"}

    for page_index in range(question_page_count):
        page = pdf.pages[page_index]
        blocks = _get_blocks_for_page(pdf_path, page, page_index)

        for block in blocks:
            lines = block["lines"]
            if not lines:
                continue
            line_h = _line_height_estimate(lines)
            pad = line_h * 2.0

            start_positions = []
            for idx, ln in enumerate(lines):
                qn = _detect_qstart(ln["text"])
                if qn is not None:
                    start_positions.append((idx, qn))

            first_start_idx = start_positions[0][0] if start_positions else len(lines)

            # ── Carry-over lines (before this block's first Q-start) ──
            if first_start_idx > 0:
                if pending is not None:
                    top = lines[0]["top"]
                    bottom_idx = first_start_idx - 1
                    bottom = lines[bottom_idx]["bottom"] + pad
                    next_start_top = lines[first_start_idx]["top"] if start_positions else block["usable_bottom"]
                    bottom = min(bottom, next_start_top)

                    crop = _crop_region(block, top, bottom)
                    if crop is not None:
                        pending["pieces"].append(crop)

                    carry_text = " ".join(l["text"] for l in lines[:first_start_idx])
                    for m in OPTION_RE.finditer(carry_text):
                        pending["found_options"].add(m.group(1))

                    if len(pending["found_options"]) >= 4:
                        stitched = _stitch_images(pending["pieces"])
                        if stitched is not None:
                            questions_out.append({"id": pending["id"], "image": stitched, "page": pending["page"]})
                        pending = None
                # else: stray header/footer lines with no open question -> ignore

            # ── This block's own question chunks ──
            for si, (line_idx, qnum) in enumerate(start_positions):
                chunk_end_idx = start_positions[si + 1][0] if si + 1 < len(start_positions) else len(lines)
                chunk_lines = lines[line_idx:chunk_end_idx]
                chunk_text = " ".join(l["text"] for l in chunk_lines)
                found_opts = set(m.group(1) for m in OPTION_RE.finditer(chunk_text))

                top = max(lines[line_idx]["top"] - pad, 0)
                if line_idx > 0:
                    top = max(top, lines[line_idx - 1]["bottom"])

                is_last_chunk_in_block = (si == len(start_positions) - 1)

                if len(found_opts) >= 4 or not is_last_chunk_in_block:
                    last_line = chunk_lines[-1]
                    bottom = min(last_line["bottom"] + pad, block["usable_bottom"])
                    if not is_last_chunk_in_block:
                        bottom = min(bottom, lines[chunk_end_idx]["top"])
                    crop = _crop_region(block, top, bottom)
                    if crop is not None:
                        questions_out.append({"id": qnum, "image": crop, "page": block["page"]})
                else:
                    # Incomplete (missing options) AND this is the last chunk in the
                    # block -> open it as pending, continues into the next block.
                    bottom = block["usable_bottom"]
                    crop = _crop_region(block, top, bottom)
                    pending = {
                        "id": qnum,
                        "pieces": [crop] if crop is not None else [],
                        "found_options": found_opts,
                        "page": block["page"],
                    }

    if pending is not None and pending["pieces"]:
        stitched = _stitch_images(pending["pieces"])
        if stitched is not None:
            questions_out.append({"id": pending["id"], "image": stitched, "page": pending["page"]})

    return questions_out


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
    with pdfplumber.open(pdf_path) as pdf:
        answer_key, question_page_count = _find_answer_key(pdf, pdf_path)
        raw_questions = _extract_all_questions(pdf_path, pdf, question_page_count)

    seen = set()
    final = []
    for q in raw_questions:
        if q["id"] in seen or q["image"] is None:
            continue
        seen.add(q["id"])
        buf = io.BytesIO()
        q["image"].save(buf, format="JPEG", quality=75)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        final.append({
            "id": q["id"],
            "image_base64": b64,
            "mime": "image/jpeg",
            "page": q["page"],
            "correct": answer_key.get(q["id"]),
        })

    final.sort(key=lambda q: (q["page"], q["id"]))

    meta = {
        "answer_key_found": len(answer_key) > 0,
        "questions_with_known_answer": sum(1 for q in final if q["correct"]),
        "total_pages_scanned": question_page_count,
    }

    return final, meta
