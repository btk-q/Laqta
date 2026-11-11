import datetime
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from laqta.storage import export, highlights
from laqta.storage.highlights import Segment, candidates, pick, windows
from laqta.storage.library import Clip

P = Path("match.mkv")
NOW = datetime.datetime(2026, 11, 11, 20, 0)


def ev(type, offset, confidence=1.0):
    return {"type": type, "offset": offset, "confidence": confidence}


def test_score_is_weight_times_confidence():
    assert highlights.score(ev("ace", 0)) == 10 and highlights.score(ev("kill", 0, 0.5)) == 1.5
    assert highlights.score(ev("match_start", 0)) == 0 and highlights.score(ev("something_new", 0)) == 1
    assert highlights.score(ev("manual", 0)) > highlights.score(ev("multikill", 0))  # the player's own key press wins


def test_close_moments_merge_into_one_scene_and_scores_add():
    spans = windows(P, [ev("kill", 100), ev("kill", 104), ev("multikill", 106), ev("kill", 300)], duration=600)
    assert [(s.start, s.end, s.score) for s in spans] == [(92, 110, 12), (292, 304, 3)]


def test_windows_stay_inside_the_video_and_ignore_worthless_or_out_of_range_events():
    spans = windows(P, [ev("kill", 2), ev("ace", 598), ev("match_end", 300), ev("kill", 900), ev("kill", -5)], duration=600)
    assert [(s.start, s.end) for s in spans] == [(0, 6), (590, 600)]


def test_the_best_moments_fill_the_reel_and_play_in_order():
    segments = [Segment(P, 0, 20, 3), Segment(P, 100, 130, 10), Segment(P, 200, 215, 6), Segment(P, 300, 320, 1)]
    chosen = pick(segments, max_seconds=50)
    assert [(s.start, s.score) for s in chosen] == [(100, 10), (200, 6)]  # 30 s + 15 s; the 20 s ones do not fit
    assert pick(segments, max_seconds=1000) == segments and pick([], 60) == []
    assert [s.start for s in pick(segments, max_seconds=25)] == [200]  # the best is too long: keep what does fit


def test_candidates_use_clips_whole_and_recordings_by_their_marks():
    clip = Clip(Path("a.mkv"), "CS2", NOW, 1, "clip", (ev("kill", 15), ev("ace", 18)), 23.0)
    bare = Clip(Path("b.mkv"), "CS2", NOW - datetime.timedelta(hours=1), 1, "clip", (), 30.0)  # a hotkey clip, no marks
    match = Clip(Path("m.mkv"), "CS2", NOW - datetime.timedelta(hours=2), 1, "recording", (ev("kill", 100), ev("death", 400)), 1800.0)
    plain = Clip(Path("s.mkv"), "CS2", NOW, 1, "recording", (), 3600.0)  # a session with nothing marked
    unknown = Clip(Path("u.mkv"), "CS2", NOW, 1, "clip", (ev("kill", 1),), None)  # length unknown
    found = candidates([clip, bare, match, plain, unknown])
    assert [(s.path.name, s.start, s.end, s.score) for s in found] == [
        ("m.mkv", 92, 104, 3), ("m.mkv", 392, 404, 0.5), ("b.mkv", 0, 30, 1), ("a.mkv", 0, 23, 13)]


def test_command_fits_every_source_into_one_frame_and_joins_them():
    args = highlights.command([Segment(Path("a.mkv"), 5, 20, 1), Segment(Path("b.mkv"), 0, 10, 1)], Path("out.mp4"))
    graph = args[args.index("-filter_complex") + 1]
    assert args.count("-i") == 2 and args[args.index("-ss") + 1] == "5.000"
    assert "pad=1920:1080" in graph and "concat=n=2:v=1:a=1[v][a]" in graph and "[1:a:0]aresample=48000" in graph
    with pytest.raises(ValueError):
        highlights.command([], Path("out.mp4"))


def test_reel_file_name_is_safe():
    path = highlights.reel_path('Bad: "Name"?', Path("C:/v"), datetime.date(2026, 11, 11))
    assert path == Path("C:/v/Exports/Highlights Bad_ _Name__ 2026-11-11.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_reel_from_sources_of_different_shapes(tmp_path):
    sources = []
    for name, size, rate in (("wide.mkv", "2560x1080", 60), ("small.mkv", "1280x720", 30)):
        path = tmp_path / name
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=duration=12:size={size}:rate={rate}",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-pix_fmt", "yuv420p", str(path)], check=True)
        sources.append(path)
    segments = [Segment(sources[0], 2, 6, 5), Segment(sources[1], 3, 8, 3), Segment(sources[0], 9, 11, 1)]
    done, progress = [], []
    job = highlights.ReelJob(segments, tmp_path / "Exports" / "reel.mp4", progress.append, lambda p, why: done.append((p, why)))
    job.start()
    job.join(180)
    assert done == [(tmp_path / "Exports" / "reel.mp4", "")], done
    info = export.probe(done[0][0])
    assert (info.width, info.height, info.audio_tracks) == (1920, 1080, 1)
    assert info.duration == pytest.approx(11.0, abs=0.3) and progress[-1] == 1.0
