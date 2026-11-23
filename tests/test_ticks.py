"""Picking several videos or games with plain clicks, no Ctrl."""
import datetime
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def click(view, item):
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.visualItemRect(item).center())


def test_select_mode_ticks_videos_with_plain_clicks(app, tmp_path):
    from laqta.ui.library import LibraryWindow

    for n in range(3):
        (tmp_path / "Game" / "2026-11-23").mkdir(parents=True, exist_ok=True)
        (tmp_path / "Game" / "2026-11-23" / f"clip {n}.mkv").write_bytes(b"x" * 100)
    window = LibraryWindow(root=tmp_path)
    window.resize(1400, 900)
    window.show()
    videos = [window.grid.item(i) for i in range(window.grid.count()) if window.grid.item(i).data(Qt.ItemDataRole.UserRole)]
    assert len(videos) == 3 and not window.pick_all.isVisible()

    click(window.grid, videos[0]); click(window.grid, videos[1])
    assert len(window.chosen()) == 1  # without Select, a plain click replaces the selection

    window.picking.setChecked(True)
    assert window.pick_all.isVisible() and window.grid.property("ticking")
    click(window.grid, videos[0]); click(window.grid, videos[2])
    assert len(window.chosen()) == 3  # each click adds one
    click(window.grid, videos[2])
    assert len(window.chosen()) == 2  # and a second click takes it back out
    window.pick_none.click()
    assert window.chosen() == []
    window.search.setText("clip 1")
    window.pick_all.click()
    assert [c.path.name for c in window.chosen()] == ["clip 1.mkv"]  # only what the filters show
    window.picking.setChecked(False)
    assert window.chosen() == [] and not window.pick_all.isVisible()
    window.pool.waitForDone(30000)  # its thumbnail jobs run ffmpeg; none may outlive this test
    window.close()


def test_add_a_game_ticks_with_plain_clicks(app, monkeypatch):
    from laqta.ui import settings_parts

    monkeypatch.setattr(settings_parts.library_scan, "scan", lambda known: [{"exe": "a.exe", "name": "Alpha"}, {"exe": "b.exe", "name": "Beta"}])
    monkeypatch.setattr(settings_parts, "processes", lambda: {})
    dialog = settings_parts.AddGameDialog(None, set())
    dialog.show()
    games = [dialog.list.item(i) for i in range(dialog.list.count()) if dialog.list.item(i).data(Qt.ItemDataRole.UserRole)]
    click(dialog.list, games[0]); click(dialog.list, games[1])
    assert [g["name"] for g in dialog.chosen()] == ["Alpha", "Beta"]
    assert all(item.checkState() == Qt.CheckState.Checked for item in games)
    click(dialog.list, games[0])
    assert [g["name"] for g in dialog.chosen()] == ["Beta"] and games[0].checkState() == Qt.CheckState.Unchecked
    dialog.close()


def test_clicking_a_cards_tick_box_ticks_it_without_ctrl_or_select_mode(app, tmp_path):
    from PySide6.QtCore import QRectF

    from laqta.ui.cards import Card
    from laqta.ui.library import LibraryWindow

    (tmp_path / "Game" / "2026-11-23").mkdir(parents=True)
    for n in range(3):
        (tmp_path / "Game" / "2026-11-23" / f"clip {n}.mkv").write_bytes(b"x" * 100)
    window = LibraryWindow(root=tmp_path)
    window.resize(1400, 900)
    window.show()
    videos = [window.grid.item(i) for i in range(window.grid.count()) if window.grid.item(i).data(Qt.ItemDataRole.UserRole)]

    def tick(item):
        box = Card.tick_rect(QRectF(window.grid.visualItemRect(item))).center().toPoint()
        QTest.mouseClick(window.grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, box)

    assert not window.picking.isChecked()
    tick(videos[0]); tick(videos[2])
    assert len(window.chosen()) == 2  # both kept, without Select mode
    tick(videos[0])
    assert [c.path for c in window.chosen()] == [videos[2].data(Qt.ItemDataRole.UserRole).path]
    click(window.grid, videos[1])
    assert len(window.chosen()) == 1  # a click on the card itself still picks just that one
    window.pool.waitForDone(30000)
    window.close()
