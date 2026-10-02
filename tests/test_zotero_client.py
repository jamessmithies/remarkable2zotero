from unittest.mock import MagicMock

from pyzotero import errors as zotero_errors

from remarkable2zotero import zotero_client
from remarkable2zotero.models import Highlight, Rect

OLD_NOTE = {"data": {"itemType": "note", "note": "<h2>reMarkable Highlights</h2>"}}


def _zot():
    zot = MagicMock()
    zot.children.return_value = [OLD_NOTE]
    zot.item_template.return_value = {}
    zot.create_items.return_value = {"successful": {"0": {}}}
    return zot


def _highlights():
    return [Highlight(page_index=0, text="some text", color=(0, 0, 0, 0), rects=[Rect(0, 0, 1, 1)])]


def test_replaces_existing_note():
    zot = _zot()
    assert zotero_client.create_highlights_note(zot, "KEY", _highlights(), "Doc")
    zot.delete_item.assert_called_once_with(OLD_NOTE)
    zot.create_items.assert_called_once()


def test_failed_delete_skips_create():
    zot = _zot()
    zot.delete_item.side_effect = zotero_errors.PyZoteroError("412")
    assert not zotero_client.create_highlights_note(zot, "KEY", _highlights(), "Doc")
    zot.create_items.assert_not_called()
