from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple


class Rect(NamedTuple):
    x: float
    y: float
    w: float
    h: float


@dataclass
class RemarkableDocument:
    uuid: str
    visible_name: str
    file_type: str
    last_modified: int
    page_uuids: list[str]
    local_pdf_path: Path | None = None
    local_rm_dir: Path | None = None


@dataclass
class Highlight:
    page_index: int
    text: str
    color: tuple[int, int, int, int]  # RGBA
    rects: list[Rect]


@dataclass
class PositionedHighlight:
    """A highlight with resolved PDF coordinates (page index + rects in PDF points)."""
    pdf_page_index: int
    text: str
    color: tuple[int, int, int, int]
    pdf_rects: list[list[float]]  # [[x1, y1, x2, y2], ...]


@dataclass
class AnnotatedPDF:
    document: RemarkableDocument
    highlights: list[Highlight]
    output_pdf_path: Path | None = None


@dataclass
class ZoteroMatch:
    parent_item_key: str
    parent_title: str
    attachment_key: str
    attachment_filename: str
    collections: list[str] = field(default_factory=list)
