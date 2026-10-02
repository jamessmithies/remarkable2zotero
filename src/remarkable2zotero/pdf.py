from __future__ import annotations

import difflib
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
    doc, is_epub = _open_as_pdf(source_path)

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


def restore_highlight_text(source_path: Path, highlights: list[Highlight]) -> int:
    """Replace each highlight's text with the matching words from the document.

    The tablet stores highlight text with no space at line breaks. The words are
    taken from the PDF instead, as the whole run between the first and last matched
    word, so words the tablet dropped are restored too. Highlights that cannot be
    matched keep the tablet's text. Returns the number restored.
    """
    doc, is_epub = _open_as_pdf(source_path)
    words_cache: dict[int, list] = {}
    restored = 0
    try:
        for h in highlights:
            if is_epub:
                # Page indices don't correspond between the tablet and the converted PDF
                page_numbers = range(len(doc))
            elif h.page_index < len(doc):
                page_numbers = [h.page_index]
            else:
                page_numbers = []

            text = None
            for n in page_numbers:
                if n not in words_cache:
                    words_cache[n] = doc[n].get_text("words")
                page_words = words_cache[n]
                span = _match_word_span(page_words, h.text)
                if span:
                    text = _join_words(page_words[span[0] : span[1] + 1])
                    break

            if text:
                h.text = text
                restored += 1
            else:
                log.warning(
                    "Could not match highlight on page %d against the PDF, "
                    "keeping the tablet's text: '%s'",
                    h.page_index + 1, h.text.strip()[:60],
                )
    finally:
        doc.close()

    log.info("Restored text of %d/%d highlights from the PDF", restored, len(highlights))
    return restored


def _open_as_pdf(source_path: Path) -> tuple[fitz.Document, bool]:
    """Open a PDF, or convert an EPUB to PDF. Returns the document and whether it was an EPUB."""
    source_doc = fitz.open(source_path)
    if source_doc.is_pdf:
        return source_doc, False

    log.info("Converting %s to PDF", source_path.suffix.lstrip(".").upper())
    pdf_bytes = source_doc.convert_to_pdf()
    source_doc.close()
    return fitz.open("pdf", pdf_bytes), True


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
        rects, _ = _match_words_on_page(page, text)
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
    rects, _ = _match_words_on_page(page, text)
    if rects:
        return _apply_highlight_from_rects(page, rects, highlight)

    log.debug("Text not found on page %d: '%s'", page.number, text[:60])
    return None


def _match_words_on_page(
    page: fitz.Page, text: str
) -> tuple[list[fitz.Rect], tuple[int, int] | None]:
    """Match highlight text against the page's words.

    Returns the rects of the matched words and the (start, end) indices of the
    matched run in page.get_text("words"), or ([], None) if there is no match.
    """
    page_words = page.get_text("words")
    span = _match_word_span(page_words, text)
    if span is None:
        return [], None
    start, end = span
    rects = [fitz.Rect(w[:4]) for w in page_words[start : end + 1]]
    return rects, span


def _match_word_span(page_words: list, text: str) -> tuple[int, int] | None:
    """Find the run of page words that a highlight covers.

    The reMarkable drops the space at every line break ('yethistoriography'), so
    matching is done on the text with all non-word characters removed. Words the
    tablet dropped or misread show up as gaps in the alignment; at least 40% of the
    highlight's characters must align with the page.
    """
    target = _match_key(text)
    if not target or not page_words:
        return None

    # Concatenated page text, with the index of the word each character came from
    chars: list[str] = []
    owner: list[int] = []
    for i, w in enumerate(page_words):
        key = _match_key(w[4])
        chars.append(key)
        owner.extend([i] * len(key))
    haystack = "".join(chars)
    if not haystack:
        return None

    pos = haystack.find(target)
    if pos >= 0:
        return owner[pos], owner[pos + len(target) - 1]

    # Cheap rejection before the alignment: one end of the highlight must be on the page
    anchor = min(len(target), 12)
    if target[:anchor] not in haystack and target[-anchor:] not in haystack:
        return None

    matcher = difflib.SequenceMatcher(None, haystack, target, autojunk=False)
    min_block = min(len(target), 8)
    blocks = [b for b in matcher.get_matching_blocks() if b.size >= min_block]
    if not blocks or sum(b.size for b in blocks) < len(target) * 0.4:
        return None

    # Extend past unaligned characters at either end of the highlight
    first, last = blocks[0], blocks[-1]
    start_char = max(0, first.a - first.b)
    end_char = min(len(haystack), last.a + last.size + len(target) - (last.b + last.size)) - 1
    return owner[start_char], owner[end_char]


def _match_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", _normalize_for_matching(text)).lower()


def _normalize_for_matching(text: str) -> str:
    """Normalize text for word matching — handle curly quotes, etc."""
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return text


def _join_words(page_words: list) -> str:
    """Join page words with spaces, healing words hyphenated across a line break."""
    parts: list[str] = []
    for i, w in enumerate(page_words):
        word = w[4]
        nxt = page_words[i + 1] if i + 1 < len(page_words) else None
        line_end = nxt is not None and (w[5], w[6]) != (nxt[5], nxt[6])
        if (
            line_end
            and re.search(r"\w-$", word)
            and nxt[4][:1].islower()
        ):
            parts.append(word[:-1])
        else:
            parts.append(word + " ")
    return "".join(parts).strip()


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
