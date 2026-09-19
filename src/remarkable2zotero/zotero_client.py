from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

from pyzotero import zotero
from pyzotero import errors as zotero_errors

from remarkable2zotero.models import Highlight, PositionedHighlight, RemarkableDocument, ZoteroMatch

log = logging.getLogger(__name__)


def create_client(api_key: str, group_id: int) -> zotero.Zotero:
    try:
        return zotero.Zotero(group_id, "group", api_key)
    except Exception as e:
        raise ConnectionError(f"Failed to create Zotero client: {e}")


def find_matching_item(
    zot: zotero.Zotero,
    document: RemarkableDocument,
    strategy: str = "filename",
) -> ZoteroMatch | None:
    author, title = _parse_name(document.visible_name)
    stem = _strip_extension(document.visible_name)

    for query in _build_search_queries(author, title, stem):
        log.debug("Searching Zotero with q='%s'", query)
        match = _search_and_match(zot, document, author, title, query)
        if match:
            return match

    return None


_EXTENSION_RE = re.compile(r"\.(pdf|epub)$", re.IGNORECASE)
_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an) ")


def _strip_extension(name: str) -> str:
    return _EXTENSION_RE.sub("", name.strip())


def _normalize(text: str) -> str:
    """Lowercase, strip accents, brackets and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
    text = re.sub(r"[\W_]+", " ", text)
    return " ".join(text.split())


def _parse_name(visible_name: str) -> tuple[str | None, str]:
    """Split 'Author - Year - Title' into (author, title). Author is None if absent."""
    name = _strip_extension(visible_name)
    parts = [p.strip() for p in re.split(r"\s+-\s+", name) if p.strip()]
    non_year_parts = [p for p in parts if not re.match(r"^\d{4}$", p)]

    if len(non_year_parts) >= 2:
        return non_year_parts[0], " - ".join(non_year_parts[1:])
    return None, name.replace("_", " ")


def _build_search_queries(author: str | None, title: str, stem: str) -> list[str]:
    # Short queries survive truncated names and punctuation differences; accents are
    # kept because Zotero's quick search does not fold them
    words = re.sub(r"[\W_]+", " ", title).lower().split()
    queries = [" ".join(words[:4])]
    if author:
        surname = re.sub(r"[\W_]+", " ", author).lower().split()[-1]
        queries.append(f"{surname} {' '.join(words[:2])}")
        queries.append(surname)
    queries.append(stem)  # finds attachments by filename
    return [q for i, q in enumerate(queries) if q and q not in queries[:i]]


def _search_and_match(
    zot: zotero.Zotero,
    document: RemarkableDocument,
    author: str | None,
    title: str,
    query: str,
) -> ZoteroMatch | None:
    try:
        items = zot.items(q=query, limit=50)
    except zotero_errors.PyZoteroError as e:
        _handle_http_error(e)
        return None

    doc_stem = _strip_extension(document.visible_name).lower()

    for item in items:
        data = item.get("data", {})
        item_type = data.get("itemType", "")

        if item_type in ("attachment", "note"):
            # Check if this attachment's filename matches
            filename = data.get("filename", "")
            if filename and _strip_extension(filename).lower() == doc_stem:
                parent_key = data.get("parentItem", "")
                if parent_key:
                    return _build_match(zot, parent_key, item)
        elif item_type != "annotation":
            # Parent item — match by title, and by author when the name has one
            if _titles_match(title, data.get("title", "")) and _author_matches(
                author, data.get("creators", [])
            ):
                return ZoteroMatch(
                    parent_item_key=data.get("key", ""),
                    parent_title=data.get("title", ""),
                    attachment_key="",
                    attachment_filename="",
                    collections=data.get("collections", []),
                )

    return None


def _titles_match(doc_title: str, zotero_title: str) -> bool:
    """Equal after normalization, or one is a prefix of the other.

    Prefix matching covers names truncated by the reMarkable and Zotero titles
    carrying a subtitle the filename omits.
    """
    a = _LEADING_ARTICLE_RE.sub("", _normalize(doc_title))
    b = _LEADING_ARTICLE_RE.sub("", _normalize(zotero_title))
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 8 and " " in shorter and longer.startswith(shorter)


def _author_matches(author: str | None, creators: list[dict]) -> bool:
    if not author or not creators:
        return True
    author_words = set(_normalize(author).split())
    for creator in creators:
        last = creator.get("lastName") or creator.get("name", "")
        last_words = _normalize(last).split()
        if last_words and last_words[-1] in author_words:
            return True
    return False


def _find_pdf_attachment(zot: zotero.Zotero, parent_key: str) -> dict | None:
    try:
        children = zot.children(parent_key)
    except zotero_errors.PyZoteroError:
        return None

    for child in children:
        data = child.get("data", {})
        if data.get("itemType") == "attachment" and data.get("contentType") == "application/pdf":
            return child
    return None


def _build_match(zot: zotero.Zotero, parent_key: str, attachment_item: dict) -> ZoteroMatch:
    try:
        parent = zot.item(parent_key)
        parent_data = parent.get("data", {})
    except zotero_errors.PyZoteroError:
        parent_data = {}

    return ZoteroMatch(
        parent_item_key=parent_key,
        parent_title=parent_data.get("title", ""),
        attachment_key=attachment_item["data"]["key"],
        attachment_filename=attachment_item["data"].get("filename", ""),
        collections=parent_data.get("collections", []),
    )


def upload_annotated_pdf(
    zot: zotero.Zotero,
    match: ZoteroMatch,
    pdf_path: Path,
) -> str | None:
    """Replace the attachment and return the new attachment key, or None on failure."""
    log.info(
        "Updating '%s' — replacing attachment %s",
        match.parent_title,
        match.attachment_key,
    )

    # Delete old attachment (also removes any existing Zotero annotations on it)
    try:
        old_item = zot.item(match.attachment_key)
        zot.delete_item(old_item)
        log.debug("Deleted old attachment %s", match.attachment_key)
    except zotero_errors.PyZoteroError as e:
        log.error("Failed to delete old attachment: %s", e)
        return None

    # Upload new attachment as child of the same parent
    try:
        zot.attachment_simple([str(pdf_path)], parentid=match.parent_item_key)
        log.info("Uploaded %s to Zotero", pdf_path.name)
    except zotero_errors.PyZoteroError as e:
        log.error("Failed to upload new attachment: %s", e)
        _handle_http_error(e)
        return None

    # Find the new attachment key
    new_attachment = _find_pdf_attachment(zot, match.parent_item_key)
    if new_attachment:
        new_key = new_attachment["data"]["key"]
        log.debug("New attachment key: %s", new_key)
        return new_key

    log.warning("Could not find new attachment after upload")
    return None


def create_annotations(
    zot: zotero.Zotero,
    attachment_key: str,
    positioned_highlights: list[PositionedHighlight],
) -> int:
    """Create Zotero annotation items via the API. Returns the number created."""
    if not positioned_highlights:
        return 0

    # Build annotation items in batches of 50 (Zotero API limit)
    items_to_create = []
    for ph in positioned_highlights:
        color_hex = "#{:02x}{:02x}{:02x}".format(ph.color[0], ph.color[1], ph.color[2])
        page_label = str(ph.pdf_page_index + 1)

        # annotationSortIndex: "PPPPP|OOOOOO|YYYYY"
        # page (5 digits) | character offset (6 digits, 0 as placeholder) | y position (5 digits)
        y_pos = int(ph.pdf_rects[0][1]) if ph.pdf_rects else 0
        sort_index = f"{ph.pdf_page_index:05d}|000000|{y_pos:05d}"

        position = json.dumps({
            "pageIndex": ph.pdf_page_index,
            "rects": ph.pdf_rects,
        })

        items_to_create.append({
            "itemType": "annotation",
            "parentItem": attachment_key,
            "annotationType": "highlight",
            "annotationText": ph.text,
            "annotationColor": color_hex,
            "annotationPageLabel": page_label,
            "annotationSortIndex": sort_index,
            "annotationPosition": position,
        })

    created = 0
    batch_size = 50
    for i in range(0, len(items_to_create), batch_size):
        batch = items_to_create[i : i + batch_size]
        try:
            resp = zot.create_items(batch)
            successful = resp.get("successful", {})
            created += len(successful)
            failed_items = resp.get("failed", {})
            if failed_items:
                log.warning("Failed to create %d annotations in batch", len(failed_items))
                for key, err in list(failed_items.items())[:3]:
                    log.debug("  %s: %s", key, err)
        except zotero_errors.PyZoteroError as e:
            log.error("Failed to create annotation batch: %s", e)
            _handle_http_error(e)

    log.info("Created %d Zotero annotations on attachment %s", created, attachment_key)
    return created


def create_highlights_note(
    zot: zotero.Zotero,
    parent_key: str,
    highlights: list[Highlight],
    document_name: str,
) -> bool:
    """Create a Zotero note with all highlighted text, grouped by page."""
    if not highlights:
        return False

    # Delete any existing "reMarkable Highlights" note to avoid duplicates
    try:
        children = zot.children(parent_key)
        for child in children:
            data = child.get("data", {})
            if (data.get("itemType") == "note"
                    and "reMarkable Highlights" in data.get("note", "")):
                zot.delete_item(child)
                log.debug("Deleted existing reMarkable highlights note")
    except zotero_errors.PyZoteroError:
        pass

    # Build HTML note content
    lines = ["<h2>reMarkable Highlights</h2>"]
    lines.append(f"<p><em>{document_name}</em></p><hr/>")

    current_page = -1
    for h in sorted(highlights, key=lambda x: (x.page_index, x.rects[0].y if x.rects else 0)):
        if h.page_index != current_page:
            current_page = h.page_index
            lines.append(f"<h3>Page {current_page + 1}</h3>")
        text = h.text.strip()
        lines.append(f"<blockquote><p>{text}</p></blockquote>")

    note_html = "\n".join(lines)

    template = zot.item_template("note")
    template["parentItem"] = parent_key
    template["note"] = note_html

    try:
        resp = zot.create_items([template])
        if resp.get("successful"):
            log.info("Created highlights note with %d excerpts", len(highlights))
            return True
        log.error("Failed to create note: %s", resp.get("failed"))
        return False
    except zotero_errors.PyZoteroError as e:
        log.error("Failed to create note: %s", e)
        return False


def create_new_item(
    zot: zotero.Zotero,
    document: RemarkableDocument,
    pdf_path: Path,
) -> str | None:
    template = zot.item_template("document")
    template["title"] = document.visible_name

    try:
        resp = zot.create_items([template])
    except zotero_errors.PyZoteroError as e:
        log.error("Failed to create Zotero item: %s", e)
        _handle_http_error(e)
        return None

    created = resp.get("successful", {})
    if "0" not in created:
        log.error("Zotero did not return a created item")
        return None

    item_key = created["0"]["key"]
    log.info("Created new Zotero item: %s (%s)", document.visible_name, item_key)

    try:
        zot.attachment_simple([str(pdf_path)], parentid=item_key)
        log.info("Attached %s to new item", pdf_path.name)
    except zotero_errors.PyZoteroError as e:
        log.error("Failed to attach PDF: %s", e)

    return item_key


def _handle_http_error(e: zotero_errors.PyZoteroError) -> None:
    status = getattr(e, "status_code", None) or str(e)
    if "403" in str(status):
        log.error("API key lacks write permission to this group library.")
    elif "404" in str(status):
        log.error("Group library not found. Check group_id in config.")
    elif "409" in str(status) or "412" in str(status):
        log.error("Conflict: item was modified by another client. Try again.")
    else:
        log.error("Zotero API error: %s", e)
