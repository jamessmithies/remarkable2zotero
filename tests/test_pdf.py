import pymupdf as fitz
import pytest

from remarkable2zotero import pdf
from remarkable2zotero.models import Highlight, Rect

LINES = [
    "Running header 147",
    "In 1988 I received a PhD in computer",
    "science at MIT, having conducted my disser-",
    "tation research at the Artificial",
    "Intelligence Laboratory. In order to find",
    "words for my newfound intuitions, I began",
    "studying several nontechnical fields.",
]


@pytest.fixture
def pdf_path(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(LINES):
        page.insert_text((72, 72 + 14 * i), line, fontsize=11)
    path = tmp_path / "doc.pdf"
    doc.save(path)
    doc.close()
    return path


def _highlight(text, page_index=0):
    return Highlight(page_index=page_index, text=text, color=(0, 0, 0, 0), rects=[Rect(0, 0, 1, 1)])


def test_restores_spaces_at_line_breaks(pdf_path):
    h = _highlight("In 1988 I received a PhD in computerscience at MIT")
    assert pdf.restore_highlight_text(pdf_path, [h]) == 1
    assert h.text == "In 1988 I received a PhD in computer science at MIT"


def test_heals_line_end_hyphens(pdf_path):
    h = _highlight("having conducted my dissertation research at the ArtificialIntelligence")
    pdf.restore_highlight_text(pdf_path, [h])
    assert h.text == (
        "having conducted my dissertation research at the Artificial Intelligence"
    )


def test_restores_words_the_tablet_dropped(pdf_path):
    h = _highlight(
        "In order to find words for my newfound intuitions, studyingseveral nontechnical fields."
    )
    pdf.restore_highlight_text(pdf_path, [h])
    assert h.text == (
        "In order to find words for my newfound intuitions, I began "
        "studying several nontechnical fields."
    )


def test_unmatched_highlight_keeps_tablet_text(pdf_path):
    h = _highlight("Nothing on this page resembles this sentence at all")
    assert pdf.restore_highlight_text(pdf_path, [h]) == 0
    assert h.text == "Nothing on this page resembles this sentence at all"


def test_page_index_out_of_range_keeps_tablet_text(pdf_path):
    h = _highlight("computerscience", page_index=5)
    assert pdf.restore_highlight_text(pdf_path, [h]) == 0
    assert h.text == "computerscience"


def test_match_words_on_page_returns_span(pdf_path):
    doc = fitz.open(pdf_path)
    words = doc[0].get_text("words")
    rects, span = pdf._match_words_on_page(doc[0], "PhD in computerscience")
    assert [w[4] for w in words[span[0] : span[1] + 1]] == ["PhD", "in", "computer", "science"]
    assert len(rects) == 4
    assert pdf._match_words_on_page(doc[0], "absent entirely from page") == ([], None)


def _words(*lines):
    """Page words in get_text("words") form, one line per argument."""
    return [
        (0, 0, 0, 0, word, 0, n, i)
        for n, line in enumerate(lines)
        for i, word in enumerate(line.split())
    ]


def test_respace_keeps_the_tablets_characters():
    # The page's OCR misread 'therefore'; the tablet's reading is kept
    words = _words("Without some idea, t", "of what the subjective")
    assert pdf._respace("Without some idea, thereforeof what the", words) == (
        "Without some idea, thereforeof what the"
    )
    words = _words("Without some idea, therefore", "of what the subjective")
    assert pdf._respace("Without some idea, thereforeof what the", words) == (
        "Without some idea, therefore of what the"
    )


def test_respace_does_not_add_trailing_footnote_markers():
    words = _words("passivism and", "isolationism.26 Next sentence")
    assert pdf._respace("passivism andisolationism.", words) == "passivism and isolationism."


def test_respace_puts_opening_punctuation_after_the_space():
    words = _words("as Nancy said:", "‘technology is", "a fetish-word’")
    assert pdf._respace("as Nancy said:‘technology isa fetish-word’", words) == (
        "as Nancy said: ‘technology is a fetish-word’"
    )


def test_respace_skips_soft_hyphen_line_breaks():
    words = _words("the predetermi­", "nations of")
    assert pdf._respace("the predeterminations of", words) == "the predeterminations of"
