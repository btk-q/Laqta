import json

import pytest

from laqta.core.events import GameEvent
from laqta.storage import clips, config
from laqta.ui import hotkey


def test_config_is_created_from_template_and_matches_defaults(tmp_path):
    path = tmp_path / "Laqta" / "config.toml"
    cfg = config.load(path)
    assert path.exists() and cfg == config.DEFAULTS


def test_config_rejects_bad_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('default_mode = "everything"')
    with pytest.raises(ValueError):
        config.load(path)
    path.write_text('[[games]]\nname = "no exe"')
    with pytest.raises(ValueError):
        config.load(path)


def test_game_dir_is_a_safe_windows_path(tmp_path):
    d = clips.game_dir({"exe": "x.exe", "name": 'Bad: "Name"?'}, tmp_path)
    assert d.parent.name == "Bad_ _Name__" and d.parent.parent == tmp_path


def test_finish_clip_trims_tail_and_writes_sidecar(tmp_path, monkeypatch):
    clip = tmp_path / "Replay.mkv"
    clip.write_bytes(b"full")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        (tmp_path / "Replay.trim.mkv").write_bytes(b"cut")
        return type("R", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr(clips.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(clips.subprocess, "run", fake_run)
    events = [GameEvent("kill", 50.0), GameEvent("manual", 53.0, source="manual")]
    clips.finish_clip(clip, {"exe": "g.exe", "name": "G"}, events, keep=23.0, pre=15.0)

    assert seen["cmd"][seen["cmd"].index("-sseof") + 1] == "-23.00"
    assert clip.read_bytes() == b"cut" and not (tmp_path / "Replay.trim.mkv").exists()
    meta = json.loads((tmp_path / "Replay.json").read_text())
    assert [(e["type"], e["offset"]) for e in meta["events"]] == [("kill", 15.0), ("manual", 18.0)]


def test_finish_clip_keeps_original_when_ffmpeg_fails(tmp_path, monkeypatch):
    clip = tmp_path / "Replay.mkv"
    clip.write_bytes(b"full")
    monkeypatch.setattr(clips.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(clips.subprocess, "run", lambda cmd, **kw: type("R", (), {"returncode": 1, "stderr": b"boom"})())
    clips.finish_clip(clip, {"exe": "g.exe"}, [], keep=20.0, pre=15.0)
    assert clip.read_bytes() == b"full"


def test_hotkey_parse():
    assert hotkey.parse("Ctrl+Alt+F9") == (0x3, 0x78)
    assert hotkey.parse("shift + k") == (0x4, ord("K"))
    for bad in ("Ctrl+Alt+", "Hyper+F9", "Ctrl+Enter", "F25"):
        with pytest.raises(ValueError):
            hotkey.parse(bad)
