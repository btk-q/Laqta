import datetime
import json
import os
import shutil
import subprocess

import pytest

from laqta.storage import library
from laqta.storage.library import Clip

NOW = datetime.datetime(2026, 11, 10, 12, 0)
GB = 1024**3
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make(root, game, name, age_hours=0.0, size=10, meta=None):
    folder = root / game / "2026-11-10"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x" * size)
    stamp = (datetime.datetime.now() - datetime.timedelta(hours=age_hours)).timestamp()
    os.utime(path, (stamp, stamp))
    if meta is not None:
        path.with_suffix(".json").write_text(json.dumps(meta))
    return path


def test_scan_reads_videos_and_sidecars_newest_first(tmp_path):
    make(tmp_path, "CS2", "2026-11-10 10-00-00.mkv", age_hours=5)
    make(tmp_path, "CS2", "Replay 2026-11-10 11-00-00.mkv", age_hours=1,
         meta={"game": "Counter-Strike 2", "seconds": 23.0, "events": [{"type": "kill", "offset": 15.0}, {"type": "ace", "offset": 18.0}]})
    make(tmp_path, "CS2", "notes.txt")
    make(tmp_path, "CS2", "half.trim.mkv")  # a trim in progress
    make(tmp_path, "Exports", "shared.mp4")  # exports are copies, not library items
    (tmp_path / "CS2" / "2026-11-10" / "broken.json").write_text("{")
    make(tmp_path, "CS2", "broken.mkv", age_hours=9)

    clips = library.scan(tmp_path)
    assert [c.path.name for c in clips] == ["Replay 2026-11-10 11-00-00.mkv", "2026-11-10 10-00-00.mkv", "broken.mkv"]
    clip, recording, broken = clips
    assert (clip.kind, clip.game, clip.seconds, clip.event_types) == ("clip", "Counter-Strike 2", 23.0, {"kill", "ace"})
    assert (recording.kind, recording.game, recording.events) == ("recording", "CS2", ())
    assert broken.game == "CS2"  # unreadable sidecar falls back to the folder name


def clip(name="a", game="CS2", days_old=0.0, size=GB, kind="clip", events=()):
    return Clip(path=__import__("pathlib").Path(name), game=game, when=NOW - datetime.timedelta(days=days_old), size=size,
                kind=kind, events=tuple({"type": e, "offset": 1.0} for e in events))


def test_filters():
    c = clip(game="CS2", days_old=3, events=("kill",))
    assert library.matches(c, now=NOW)
    assert library.matches(c, game="CS2", event="kill", days=7, kind="clip", now=NOW)
    assert not library.matches(c, game="Dota 2", now=NOW)
    assert not library.matches(c, event="ace", now=NOW)
    assert not library.matches(c, days=1, now=NOW)
    assert not library.matches(c, kind="recording", now=NOW)


def test_cleanup_by_age():
    clips = [clip("new", days_old=1), clip("old", days_old=31), clip("older", days_old=90)]
    assert [c.path.name for c in library.to_delete(clips, max_age_days=30, max_gb=0, now=NOW)] == ["old", "older"]
    assert library.to_delete(clips, 0, 0, NOW) == []


def test_cleanup_by_size_removes_oldest_until_under_the_limit():
    clips = [clip("a", days_old=1, size=4 * GB), clip("b", days_old=2, size=4 * GB), clip("c", days_old=3, size=4 * GB)]
    assert [c.path.name for c in library.to_delete(clips, 0, max_gb=10, now=NOW)] == ["c"]
    assert [c.path.name for c in library.to_delete(clips, 0, max_gb=5, now=NOW)] == ["c", "b"]
    assert library.to_delete(clips, 0, max_gb=100, now=NOW) == []


def test_cleanup_never_touches_a_file_from_the_last_minutes_and_combines_rules():
    recording_now = clip("live", days_old=0, size=50 * GB)
    clips = [recording_now, clip("week", days_old=7, size=GB), clip("ancient", days_old=400, size=GB)]
    doomed = library.to_delete(clips, max_age_days=30, max_gb=10, now=NOW)
    assert [c.path.name for c in doomed] == ["ancient", "week"]  # age first, then size; never the live one


def test_cleanup_deletes_video_and_sidecar(tmp_path):
    old = make(tmp_path, "CS2", "old.mkv", age_hours=24 * 40, meta={"game": "CS2"})
    new = make(tmp_path, "CS2", "new.mkv", age_hours=1)
    assert library.cleanup(0, 0, tmp_path) == 0 and old.exists()
    assert library.cleanup(30, 0, tmp_path) == 1
    assert not old.exists() and not old.with_suffix(".json").exists() and new.exists()


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    root = tmp_path_factory.mktemp("lib")
    folder = root / "Test Game" / "2026-11-10"
    folder.mkdir(parents=True)
    path = folder / "Replay sample.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=30",
                    "-g", "30", "-pix_fmt", "yuv420p", str(path)], check=True)
    path.with_suffix(".json").write_text(json.dumps(
        {"game": "Test Game", "seconds": 8.0, "events": [{"type": "kill", "offset": 1.0}, {"type": "ace", "offset": 5.0}]}))
    return root, path


@needs_ffmpeg
def test_thumbnail_is_made_once_and_cached(video, tmp_path):
    _, path = video
    thumb = library.thumbnail(path, tmp_path / "thumbs")
    assert thumb.read_bytes()[:2] == b"\xff\xd8"  # a JPEG
    stamp = thumb.stat().st_mtime_ns
    assert library.thumbnail(path, tmp_path / "thumbs") == thumb and thumb.stat().st_mtime_ns == stamp
    assert library.thumbnail(tmp_path / "missing.mkv", tmp_path / "thumbs") is None


@needs_ffmpeg
def test_trim_keeps_the_original_and_shifts_event_marks(video):
    _, path = video
    out = library.trim(path, 3, 7)
    assert path.exists() and 3.5 <= library.duration(out) <= 5.5  # stream copy cuts on keyframes
    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["seconds"] == 4.0 and meta["events"] == [{"type": "ace", "offset": 2.0}]
    with pytest.raises(ValueError):
        library.trim(path, 5, 5)


@needs_ffmpeg
def test_export_makes_an_mp4_outside_the_library(video):
    root, path = video
    out = library.export(path, root)
    assert out == root / "Exports" / "Test Game Replay sample.mp4" and 7.5 <= library.duration(out) <= 8.5
    assert all(c.path != out for c in library.scan(root))
