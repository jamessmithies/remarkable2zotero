from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz  # pymupdf

from remarkable2zotero.models import Highlight, PositionedHighlight

log = logging.getLogger(__name__)


def embed_highlights(
    source_path: Path,
    highlights: list[Highlight],
    output_path: Path,
) -> list[PositionedHighlight]:
    """Embed highlights into a PDF and return positioned highlights with PDF coordinates."""
    source_doc = fitz.open(source_path)
    is_epub = not source_doc.is_pdf

    if is_epub:
        log.info("Converting %s to PDF", source_path.suffix.lstrip(".").upper())
        pdf_bytes = source_doc.convert_to_pdf()
        source_doc.close()
        doc = fitz.open("pdf", pdf_bytes)
    else:
        doc = source_doc

    positioned: list[PositionedHighlight] = []
    failed = 0

    if is_epub:
        for highlight in highlights:
            ph = _add_highlight_search_all_pages(doc, highlight)
            if ph:
                positioned.append(ph)
            else:
                failed += 1
    else:
        highlights_by_page: dict[int, list[Highlight]] = {}
        for h in highlights:
            highlights_by_page.setdefault(h.page_index, []).append(h)

        for page_index, page_highlights in highlights_by_page.items():
            if page_index >= len(doc):
                log.warning(
                    "Page index %d out of range (PDF has %d pages)", page_index, len(doc)
                )
                continue

            page = doc[page_index]
            for highlight in page_highlights:
                ph = _add_highlight_to_page(page, highlight)
                if ph:
                    positioned.append(ph)
                else:
                    failed += 1

    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()
    log.info(
        "Embedded %d highlights into %s (%d failed)",
        len(positioned), output_path.name, failed,
    )
    return positioned


def _add_highlight_search_all_pages(
    doc: fitz.Document, highlight: Highlight
) -> PositionedHighlight | None:
    text = highlight.text.strip()
    if not text:
        return None

    # Short text: full search works reliably
    if len(text) <= 80:
        for page in doc:
            quads = page.search_for(text, quads=True)
            if quads:
                return _apply_highlight(page, quads, highlight)

    # Long text or full search failed — use word-matching approach
    for page in doc:
        rects = _match_words_on_page(page, text)
        if rects:
            return _apply_highlight_from_rects(page, rects, highlight)

    log.debug("Text not found in any page: '%s'", text[:60])
    return None


def _add_highlight_to_page(
    page: fitz.Page, highlight: Highlight
) -> PositionedHighlight | None:
    text = highlight.text.strip()
    if not text:
        return None

    # Short text: full search works reliably
    if len(text) <= 80:
        quads = page.search_for(text, quads=True)
        if quads:
            return _apply_highlight(page, quads, highlight)

    # Long text or full search failed — use word-matching approach
    rects = _match_words_on_page(page, text)
    if rects:
        return _apply_highlight_from_rects(page, rects, highlight)

    log.debug("Text not found on page %d: '%s'", page.number, text[:60])
    return None


def _match_words_on_page(page: fitz.Page, text: str) -> list[fitz.Rect]:
    """Match highlight text against page words in sequence, collecting bounding rects.

    Handles the reMarkable's line-break concatenation issue (e.g. 'yethistoriography')
    by doing flexible word matching.
    """
    # Get all words from the page with positions: (x0, y0, x1, y1, "word", block, line, word_n)
    page_words = page.get_text("words")
    if not page_words:
        return []

    # Normalize the highlight text into words (split concatenated words too)
    highlight_text = _normalize_for_matching(text)
    h_words = highlight_text.lower().split()
    if not h_words:
        return []

    # Build page word list with positions
    pw_texts = [w[4].lower().strip() for w in page_words]
    pw_rects = [fitz.Rect(w[0], w[1], w[2], w[3]) for w in page_words]

    # Find the best starting position by sliding through page words
    best_start = -1
    best_count = 0

    for start_idx in range(len(pw_texts)):
        if not pw_texts[start_idx]:
            continue
        # Check if highlight words match starting from this position
        count = _count_matching_words(h_words, pw_texts, start_idx)
        if count > best_count:
            best_count = count
            best_start = start_idx

    # Require at least 40% of highlight words to match
    if best_count < max(2, len(h_words) * 0.4):
        return []

    # Collect rects for matched page words
    matched_rects = []
    h_idx = 0
    p_idx = best_start
    while h_idx < len(h_words) and p_idx < len(pw_texts):
        if _words_similar(h_words[h_idx], pw_texts[p_idx]):
            matched_rects.append(pw_rects[p_idx])
            h_idx += 1
            p_idx += 1
        elif _word_starts_with(pw_texts[p_idx], h_words, h_idx):
            # Page word contains multiple highlight words concatenated
            matched_rects.append(pw_rects[p_idx])
            consumed = _count_consumed_words(pw_texts[p_idx], h_words, h_idx)
            h_idx += consumed
            p_idx += 1
        else:
            # Skip this highlight word (might be part of concatenation)
            h_idx += 1

    return matched_rects


def _normalize_for_matching(text: str) -> str:
    """Normalize text for word matching — handle curly quotes, etc."""
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return text


def _count_matching_words(h_words: list[str], pw_texts: list[str], start: int) -> int:
    """Count how many highlight words match page words starting at the given position."""
    count = 0
    h_idx = 0
    p_idx = start

    while h_idx < len(h_words) and p_idx < len(pw_texts):
        if _words_similar(h_words[h_idx], pw_texts[p_idx]):
            count += 1
            h_idx += 1
            p_idx += 1
        elif _word_starts_with(pw_texts[p_idx], h_words, h_idx):
            count += 1
            consumed = _count_consumed_words(pw_texts[p_idx], h_words, h_idx)
            h_idx += consumed
            p_idx += 1
        else:
            h_idx += 1
            # Allow small gaps
            if h_idx - count > 3:
                break

    return count


def _words_similar(a: str, b: str) -> bool:
    """Check if two words are similar enough to count as a match."""
    a = re.sub(r"[^\w]", "", a)
    b = re.sub(r"[^\w]", "", b)
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def _word_starts_with(page_word: str, h_words: list[str], h_idx: int) -> bool:
    """Check if a page word is a concatenation starting with h_words[h_idx]."""
    pw = re.sub(r"[^\w]", "", page_word)
    hw = re.sub(r"[^\w]", "", h_words[h_idx])
    return len(pw) > len(hw) and pw.startswith(hw)


def _count_consumed_words(page_word: str, h_words: list[str], h_idx: int) -> int:
    """Count how many highlight words are consumed by a single concatenated page word."""
    pw = re.sub(r"[^\w]", "", page_word)
    consumed = 0
    pos = 0
    for i in range(h_idx, len(h_words)):
        hw = re.sub(r"[^\w]", "", h_words[i])
        if pw[pos:].startswith(hw):
            pos += len(hw)
            consumed += 1
            if pos >= len(pw):
                break
        else:
            break
    return max(consumed, 1)


def _apply_highlight(
    page: fitz.Page, quads: list, highlight: Highlight
) -> PositionedHighlight | None:
    try:
        annot = page.add_highlight_annot(quads)
    except Exception as e:
        log.warning("Failed to add highlight on page %d: %s", page.number, e)
        return None

    r_val, g_val, b_val, a_val = highlight.color
    annot.set_colors(stroke=[r_val / 255.0, g_val / 255.0, b_val / 255.0])
    annot.set_opacity(a_val / 255.0)
    annot.set_info(content=highlight.text.strip(), title="reMarkable")
    annot.update()

    pdf_rects = []
    for q in quads:
        r = q.rect
        pdf_rects.append([round(r.x0, 3), round(r.y0, 3), round(r.x1, 3), round(r.y1, 3)])

    return PositionedHighlight(
        pdf_page_index=page.number,
        text=highlight.text.strip(),
        color=highlight.color,
        pdf_rects=pdf_rects,
    )


def _apply_highlight_from_rects(
    page: fitz.Page, rects: list[fitz.Rect], highlight: Highlight
) -> PositionedHighlight | None:
    if not rects:
        return None

    try:
        annot = page.add_highlight_annot(rects)
    except Exception as e:
        log.warning("Failed to add highlight on page %d: %s", page.number, e)
        return None

    r_val, g_val, b_val, a_val = highlight.color
    annot.set_colors(stroke=[r_val / 255.0, g_val / 255.0, b_val / 255.0])
    annot.set_opacity(a_val / 255.0)
    annot.set_info(content=highlight.text.strip(), title="reMarkable")
    annot.update()

    pdf_rects = []
    for r in rects:
        pdf_rects.append([round(r.x0, 3), round(r.y0, 3), round(r.x1, 3), round(r.y1, 3)])

    return PositionedHighlight(
        pdf_page_index=page.number,
        text=highlight.text.strip(),
        color=highlight.color,
        pdf_rects=pdf_rects,
    )
