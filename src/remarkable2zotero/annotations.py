from __future__ import annotations

import logging
from pathlib import Path

import rmscene

from remarkable2zotero.models import Highlight, Rect, RemarkableDocument

log = logging.getLogger(__name__)

# Default highlight colors (RGBA)
COLOR_YELLOW = (255, 235, 59, 128)
COLOR_GREEN = (76, 175, 80, 128)
COLOR_PINK = (233, 30, 99, 128)
COLOR_BLUE = (33, 150, 243, 128)
COLOR_GRAY = (158, 158, 158, 128)
COLOR_ORANGE = (255, 152, 0, 128)


def extract_highlights(doc: RemarkableDocument) -> list[Highlight]:
    if not doc.local_rm_dir:
        return []

    highlights = []
    for page_index, page_uuid in enumerate(doc.page_uuids):
        rm_path = doc.local_rm_dir / f"{page_uuid}.rm"
        if not rm_path.exists():
            continue
        try:
            page_highlights = _parse_rm_file(rm_path, page_index)
            highlights.extend(page_highlights)
        except Exception as e:
            log.warning("Failed to parse %s (page %d): %s", rm_path.name, page_index, e)

    highlights.sort(key=lambda h: (h.page_index, h.rects[0].y if h.rects else 0))
    log.info("Extracted %d highlights from %s", len(highlights), doc.visible_name)
    return highlights


def _parse_rm_file(rm_path: Path, page_index: int) -> list[Highlight]:
    highlights = []

    with open(rm_path, "rb") as f:
        tree = rmscene.read_tree(f)

    for item in tree.walk():
        if item is None or not _is_glyph_range(item):
            continue

        text = getattr(item, "text", "") or ""
        if not text.strip():
            continue

        rects = _extract_rects(item)
        if not rects:
            continue

        color = _extract_color(item)

        highlights.append(Highlight(
            page_index=page_index,
            text=text,
            color=color,
            rects=rects,
        ))

    return highlights


def _is_glyph_range(item) -> bool:
    type_name = type(item).__name__
    return "GlyphRange" in type_name or "glyphrange" in type_name.lower()


def _extract_rects(item) -> list[Rect]:
    rects = []
    raw_rects = getattr(item, "rectangles", None) or getattr(item, "rects", None) or []
    for r in raw_rects:
        if hasattr(r, "x") and hasattr(r, "y") and hasattr(r, "w") and hasattr(r, "h"):
            rects.append(Rect(x=r.x, y=r.y, w=r.w, h=r.h))
        elif isinstance(r, (list, tuple)) and len(r) >= 4:
            rects.append(Rect(x=r[0], y=r[1], w=r[2], h=r[3]))
    return rects


def _extract_color(item) -> tuple[int, int, int, int]:
    color_rgba = getattr(item, "color_rgba", None)
    if color_rgba and len(color_rgba) >= 4:
        return tuple(int(c) for c in color_rgba[:4])

    color = getattr(item, "color", None)
    if color is not None:
        return _map_pen_color(color)

    return COLOR_YELLOW


def _map_pen_color(color) -> tuple[int, int, int, int]:
    color_val = color.value if hasattr(color, "value") else color
    color_name = str(color).lower()

    if "yellow" in color_name:
        return COLOR_YELLOW
    if "green" in color_name:
        return COLOR_GREEN
    if "pink" in color_name or "magenta" in color_name:
        return COLOR_PINK
    if "blue" in color_name:
        return COLOR_BLUE
    if "orange" in color_name:
        return COLOR_ORANGE
    if "gray" in color_name or "grey" in color_name:
        return COLOR_GRAY

    # Numeric fallback
    color_map = {
        3: COLOR_YELLOW,
        4: COLOR_GREEN,
        5: COLOR_PINK,
        6: COLOR_BLUE,
        7: COLOR_ORANGE,
        8: COLOR_GRAY,
        9: COLOR_YELLOW,  # PenColor.HIGHLIGHT
    }
    return color_map.get(color_val, COLOR_YELLOW)
