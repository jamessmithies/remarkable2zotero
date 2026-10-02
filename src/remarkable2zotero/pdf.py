from __future__ import annotations

import difflib
import logging
from itertools import pairwise
from pathlib import Path

import pymupdf as fitz

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


def restore_highlight_text(
    source_path: Path, highlights: list[Highlight], any_page: bool = False
) -> int:
    """Restore the spaces the tablet dropped from each highlight, using the document.

    The tablet stores highlight text with no space at line breaks. Each highlight is
    matched against its page, which decides where the spaces go (see _respace); words
    the tablet dropped are restored too. Highlights that cannot be matched keep the
    tablet's text. Returns the number restored.

    With any_page, a highlight not found on its own page is searched for on every
    page, for a PDF whose pages may not line up with the tablet's copy.
    """
    doc, is_epub = _open_as_pdf(source_path)
    words_cache: dict[int, list] = {}
    restored = 0
    try:
        for h in highlights:
            own_page = [h.page_index] if h.page_index < len(doc) else []
            if is_epub or any_page:
                # Page indices don't correspond between the tablet and the converted PDF
                page_numbers = own_page + [n for n in range(len(doc)) if n not in own_page]
            else:
                page_numbers = own_page

            text = None
            for n in page_numbers:
                if n not in words_cache:
                    words_cache[n] = doc[n].get_text("words")
                text = _respace(h.text, words_cache[n])
                if text:
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
    """Find the run of page words that a highlight covers."""
    alignment = _align(page_words, text)
    if alignment is None:
        return None
    blocks, owner, _ = alignment

    # Extend past unaligned characters at either end of the highlight
    first, last = blocks[0], blocks[-1]
    target_len = len(alignment[2])
    start_char = max(0, first.a - first.b)
    end_char = min(len(owner), last.a + last.size + target_len - (last.b + last.size)) - 1
    return owner[start_char], owner[end_char]


def _align(page_words: list, text: str) -> tuple[list, list[int], list[int]] | None:
    """Align a highlight's characters with the page's.

    The reMarkable drops the space at every line break ('yethistoriography'), so both
    sides are compared as word characters only, lowercased, with spaces and
    punctuation removed. Words the tablet dropped or misread show up as gaps; at least
    40% of the highlight's characters must align with the page.

    Returns the aligned blocks (a = page character, b = highlight character), the page
    word each page character came from, and the position in `text` of each highlight
    character; or None if the highlight is not on the page.
    """
    target, target_pos = _key_chars(text)
    if not target or not page_words:
        return None

    chars: list[str] = []
    owner: list[int] = []
    for i, w in enumerate(page_words):
        key, _ = _key_chars(w[4])
        chars.append(key)
        owner.extend([i] * len(key))
    haystack = "".join(chars)
    if not haystack:
        return None

    pos = haystack.find(target)
    if pos >= 0:
        return [difflib.Match(pos, 0, len(target))], owner, target_pos

    # Cheap rejection before the alignment: one end of the highlight must be on the page
    anchor = min(len(target), 12)
    if target[:anchor] not in haystack and target[-anchor:] not in haystack:
        return None

    matcher = difflib.SequenceMatcher(None, haystack, target, autojunk=False)
    min_block = min(len(target), 8)
    blocks = [b for b in matcher.get_matching_blocks() if b.size >= min_block]
    if not blocks or sum(b.size for b in blocks) < len(target) * 0.4:
        return None
    return blocks, owner, target_pos


def _key_chars(text: str) -> tuple[str, list[int]]:
    """The word characters of `text`, lowercased, and the position of each in `text`."""
    key: list[str] = []
    pos: list[int] = []
    for i, ch in enumerate(text):
        if ch.isalnum():
            lower = ch.lower()
            key.append(lower if len(lower) == 1 else ch)
            pos.append(i)
    return "".join(key), pos


def _respace(text: str, page_words: list) -> str | None:
    """Put back the spaces the tablet dropped at line breaks, using the page's words.

    The tablet's characters are kept as they are; the page only decides where spaces
    go. A space is added wherever the page has a word break and the highlight has no
    whitespace, except at a word hyphenated across a line break. Whole words the page
    has and the highlight skips over (the tablet dropped them) are added too.
    Returns None if the highlight is not on the page.
    """
    alignment = _align(page_words, text)
    if alignment is None:
        return None
    blocks, owner, target_pos = alignment
    inserts: list[tuple[int, str]] = []

    def boundary(pa: int, pb: int, ta: int, tb: int, words: str = "") -> None:
        """Page characters pa, pb and highlight characters ta, tb are adjacent."""
        wa, wb = page_words[owner[pa]], page_words[owner[pb]]
        if not words and owner[pa] == owner[pb]:
            return
        gap = text[target_pos[ta] + 1 : target_pos[tb]]
        if not words:
            if any(c.isspace() for c in gap):
                return
            line_break = (wa[5], wa[6]) != (wb[5], wb[6])
            if line_break and wa[4].rstrip().endswith(("-", "\u00ad")):
                return
        # Opening punctuation of the next page word ('(', '"') goes after the space
        lead = len(wb[4]) - len(wb[4].lstrip("\"'(\u2018\u201c[\u00ad"))
        at = target_pos[tb] - min(lead, len(gap))
        inserts.append((at, f" {words} " if words else " "))

    for blk in blocks:
        for k in range(blk.size - 1):
            boundary(blk.a + k, blk.a + k + 1, blk.b + k, blk.b + k + 1)

    for prev, nxt in pairwise(blocks):
        pa, pb = prev.a + prev.size - 1, nxt.a
        ta, tb = prev.b + prev.size - 1, nxt.b
        if tb != ta + 1:
            continue
        if pb == pa + 1:
            boundary(pa, pb, ta, tb)
            continue
        # Page characters with no counterpart in the highlight: words the tablet dropped.
        # Only whole words, and not bare numbers (footnote markers, line numbers)
        if owner[pa] == owner[pa + 1] or owner[pb] == owner[pb - 1]:
            continue
        dropped = [page_words[i][4] for i in range(owner[pa] + 1, owner[pb])]
        if 0 < len(dropped) <= 8 and any(c.isalpha() for w in dropped for c in w):
            boundary(pa, pb, ta, tb, " ".join(dropped))

    for at, insert in sorted(inserts, reverse=True):
        text = text[:at] + insert + text[at:]
    return " ".join(text.split())


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
