import json

import pytest

from laqta.storage import library


def test_marks_added_while_watching_are_kept_in_order_and_not_doubled(tmp_path):
    folder = tmp_path / "Game" / "2026-11-22"
    folder.mkdir(parents=True)
    path = folder / "a.mkv"
    path.write_bytes(b"x")
    path.with_suffix(".json").write_text(json.dumps({"game": "Game", "events": [{"type": "kill", "offset": 30.0}], "tags": ["keep"]}))
    library.add_mark(path, 50.0)
    events = library.add_mark(path, 10.4)
    assert [(e["type"], e["offset"]) for e in events] == [("manual", 10.4), ("kill", 30.0), ("manual", 50.0)]
    assert len(library.add_mark(path, 10.9)) == 3  # within a second of an existing mark: moved, not doubled
    meta = json.loads(path.with_suffix(".json").read_text())
    assert meta["tags"] == ["keep"] and meta["events"][0]["source"] == "manual"
    bare = folder / "old.mkv"
    bare.write_bytes(b"x")
    assert library.add_mark(bare, -3)[0]["offset"] == 0.0  # an old video without a sidecar gets one


def test_the_volume_is_remembered_between_runs(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from laqta.ui import player

    app = QApplication.instance() or QApplication([])  # noqa: F841
    monkeypatch.setattr(player, "_volume_file", lambda: tmp_path / "player.json")
    assert player._load_volume() == 0.8  # first run
    p = player.Player()
    p.volume.setValue(35)
    assert player._load_volume() == pytest.approx(0.35) and player.VOLUME[0] == pytest.approx(0.35)
    (tmp_path / "player.json").write_text("not json")
    assert player._load_volume() == 0.8
    p.close_media()
