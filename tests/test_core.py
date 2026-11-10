import json
import time
import urllib.request

from laqta.core.app import with_usable_mode
from laqta.core.events import GameEvent
from laqta.plugins import cs2_gsi
from laqta.plugins.cs2_gsi import Cs2Plugin
from laqta.storage import clips


def test_full_match_falls_back_to_session_when_nothing_can_signal_a_match():
    assert with_usable_mode({"exe": "a.exe", "mode": "match"})["mode"] == "session"  # no plugin
    assert with_usable_mode({"exe": "a.exe", "mode": "match", "plugin": "nope"})["mode"] == "session"
    # Valorant's plugin exists but cannot report a match start, so "match" would record nothing.
    assert with_usable_mode({"exe": "v.exe", "mode": "match", "plugin": "valorant"})["mode"] == "session"
    assert with_usable_mode({"exe": "c.exe", "mode": "match", "plugin": "cs2"})["mode"] == "match"
    assert with_usable_mode({"exe": "l.exe", "mode": "match", "plugin": "lol"})["mode"] == "match"
    assert with_usable_mode({"exe": "v.exe", "mode": "events", "plugin": "valorant"})["mode"] == "events"


def test_a_hung_ffmpeg_cannot_block_forever(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(clips.shutil, "which", lambda name: "ffmpeg")

    def run(cmd, **kw):
        seen.update(kw)
        return type("R", (), {"returncode": 1, "stderr": b""})()

    monkeypatch.setattr(clips.subprocess, "run", run)
    clip = tmp_path / "Replay.mkv"
    clip.write_bytes(b"full")
    clips.finish_clip(clip, {"exe": "g.exe"}, [GameEvent("kill", 1.0)], keep=20.0, pre=15.0)
    assert seen["timeout"] and seen["timeout"] <= 120


def test_gsi_listener_survives_a_payload_of_the_wrong_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(cs2_gsi.config, "DIR", tmp_path)
    secret, seen = cs2_gsi.token(), []
    plugin = Cs2Plugin()
    plugin.start(seen.append, {"exe": "cs2.exe"})

    def post(body):
        req = urllib.request.Request(f"http://127.0.0.1:{cs2_gsi.PORT}/", data=json.dumps(body).encode(), method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status

    try:
        auth = {"auth": {"token": secret}}
        assert post({**auth, "map": "not an object", "player": 7}) == 200  # nonsense, but authenticated
        good = {**auth, "provider": {"steamid": "1"}, "map": {"phase": "live"}, "player": {"steamid": "1"}}
        assert post(good) == 200  # the listener is still alive and still tracking
        time.sleep(0.1)
    finally:
        plugin.stop()
    assert [e.type for e in seen] == ["match_start"]
