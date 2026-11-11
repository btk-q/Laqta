import io
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from laqta.storage import export, library
from laqta.storage.export import DISCORD_LIMIT, Info, Job

INFO = Info(duration=20.0, width=2560, height=1080, audio_tracks=3)
SRC = Path("C:/v/Game/2026-11-11/Replay 1.mkv")


def args(preset, start=2.0, end=12.0, info=INFO, **kw):
    return export.command(SRC, Path("out.mp4"), preset, start, end, info, **kw)


def after(argv, flag):
    return argv[argv.index(flag) + 1]


def test_every_preset_cuts_the_asked_range_and_reports_progress():
    for preset in export.PRESETS:
        a = args(preset)
        assert (after(a, "-ss"), after(a, "-to")) == ("2.000", "12.000")
        assert a.index("-ss") < a.index("-i")  # seek before decoding, or long recordings crawl
        assert after(a, "-progress") == "pipe:1" and "-nostats" in a


def test_exact_trim_keeps_every_track_and_other_presets_carry_one():
    assert after(args("exact"), "-map") == "0"
    for preset in ("vertical", "discord", "1080p60"):
        maps = [args(preset)[i + 1] for i, x in enumerate(args(preset)) if x == "-map"]
        assert maps == ["0:v:0", "0:a:0?"]  # the "?" keeps a silent video exportable


def test_leaving_the_microphone_out_uses_the_game_track_when_there_is_one():
    assert "0:a:1" in args("discord", without_mic=True)
    one_track = Info(20.0, 1920, 1080, audio_tracks=1)
    assert "0:a:0?" in args("discord", info=one_track, without_mic=True)  # nothing to separate: keep the sound


def test_vertical_crops_the_centre_strip_to_1080x1920():
    assert after(args("vertical"), "-vf") == "crop=trunc(ih*9/16/2)*2:ih,scale=1080:1920,setsar=1"


def test_discord_bitrate_fits_the_limit_for_any_length():
    for seconds in (5, 20, 60, 180):
        kbps = export.discord_video_kbps(seconds)
        assert (kbps + 96) * 1000 / 8 * seconds <= DISCORD_LIMIT * 0.951
    assert export.discord_video_kbps(60, safety=0.85) < export.discord_video_kbps(60)
    assert export.discord_video_kbps(10_000) == 100  # absurdly long: floor, and the size check reports it
    a = args("discord")
    assert after(a, "-b:v") == after(a, "-maxrate") == f"{export.discord_video_kbps(10.0)}k"


def test_bad_ranges_and_presets_are_refused():
    for start, end in ((5, 5), (8, 2), (-1, 4)):
        with pytest.raises(ValueError):
            args("exact", start, end)
    with pytest.raises(ValueError):
        args("gif")


def test_output_names_and_places(tmp_path):
    assert export.output_path(SRC, "exact", 2.4, 12.9) == SRC.with_name("Replay 1 trim 2-12.mkv")  # stays in the library
    assert export.output_path(SRC, "discord", 2, 12, tmp_path) == tmp_path / "Exports" / "Game Replay 1 2-12 discord.mp4"


def test_progress_lines():
    assert export.progress_seconds("out_time_us=5500000\n") == 5.5
    for line in ("out_time_us=N/A", "out_time_ms=5500000", "progress=continue", "frame=120", "", "out_time_us=-33"):
        assert export.progress_seconds(line) in (None, 0.0)
    assert export.progress_seconds("out_time_us=-33") == 0.0


class FakeStdin:
    """Records what the job writes; a "q" lets the fake ffmpeg finish, as the real one does."""

    def __init__(self, on_quit):
        self.written, self.on_quit = b"", on_quit

    def write(self, data):
        self.written += data
        if data == b"q":
            self.on_quit()

    def flush(self):
        pass

    def getvalue(self):
        return self.written


class FakeProcess:
    def __init__(self, out_file: Path, size: int, lines=(), code=0, hang=False):
        self._release = threading.Event()
        self.stdin, self.stderr = FakeStdin(self._release.set), io.BytesIO(b"boom" if code else b"")
        self.hang, self.code = hang, code
        if not hang:
            self._release.set()
        self.stdout = self._stream(lines)
        if size:
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(b"x" * size)

    def _stream(self, lines):
        yield from (line.encode() for line in lines)
        self._release.wait(5)

    def poll(self):
        return None if not self._release.is_set() else self.code

    def wait(self):
        self._release.wait(5)
        return self.code

    def kill(self):
        self._release.set()


def run_job(tmp_path, preset, sizes, lines=("out_time_us=5000000\n", "out_time_us=10000000\n"), code=0, cancel=False):
    progress, done, calls = [], [], []
    job_ref = {}

    def popen(argv, **kw):
        calls.append(argv)
        return FakeProcess(job_ref["job"].work, sizes[min(len(calls), len(sizes)) - 1], lines, code, hang=cancel)

    src = tmp_path / "Game" / "d" / "Replay 1.mkv"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"v")
    src.with_suffix(".json").write_text(json.dumps({"game": "Game", "events": [{"type": "kill", "offset": 1}, {"type": "ace", "offset": 6}]}))
    job = job_ref["job"] = Job(src, preset, 2.0, 12.0, INFO, progress.append, lambda path, why: done.append((path, why)),
                               root=tmp_path, popen=popen)
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    job.start()
    if cancel:
        time.sleep(0.2)
        job.cancel()
    job.join(10)
    return job, progress, done, calls


def test_job_reports_progress_and_the_finished_file(tmp_path):
    job, progress, done, calls = run_job(tmp_path, "1080p60", sizes=[5000])
    assert progress == [0.5, 1.0, 1.0] and done == [(job.out, "")] and len(calls) == 1


def test_discord_export_retries_lower_when_the_file_comes_out_too_big(tmp_path):
    job, _, done, calls = run_job(tmp_path, "discord", sizes=[DISCORD_LIMIT + 1, DISCORD_LIMIT - 1])
    rates = [int(c[c.index("-b:v") + 1].rstrip("k")) for c in calls]
    assert len(calls) == 2 and rates[1] < rates[0] and done == [(job.out, "")]


def test_discord_export_that_can_never_fit_says_so_and_leaves_no_file(tmp_path):
    job, _, done, calls = run_job(tmp_path, "discord", sizes=[DISCORD_LIMIT + 1])
    assert len(calls) == 3 and done[0][0] is None and "10 MB" in done[0][1] and not job.out.exists() and not job.work.exists()


def test_failed_export_reports_ffmpegs_words_and_leaves_no_file(tmp_path):
    job, _, done, _ = run_job(tmp_path, "vertical", sizes=[0], code=1)
    assert done == [(None, "boom")] and not job.out.exists()


def test_cancel_asks_ffmpeg_to_quit_and_removes_the_partial_file(tmp_path):
    job, _, done, _ = run_job(tmp_path, "1080p60", sizes=[4000], cancel=True)
    assert done == [(None, "cancelled")] and not job.out.exists() and not job.work.exists()
    assert job._process.stdin.getvalue() == b"q"  # not terminate(): that corrupts the file being written


def test_cancelling_or_failing_never_damages_an_earlier_file_of_the_same_name(tmp_path):
    for kwargs in ({"cancel": True, "sizes": [4000]}, {"code": 1, "sizes": [0]}):
        earlier = tmp_path / "Exports" / "Game Replay 1 2-12 1080p60.mp4"
        earlier.parent.mkdir(parents=True, exist_ok=True)
        earlier.write_bytes(b"the good export from last time")
        job, _, done, _ = run_job(tmp_path, "1080p60", **kwargs)
        assert job.out == earlier and done[0][0] is None
        assert earlier.read_bytes() == b"the good export from last time"


def test_exact_trim_carries_the_event_marks_inside_the_cut(tmp_path):
    job, _, done, _ = run_job(tmp_path, "exact", sizes=[3000])
    meta = json.loads(job.out.with_suffix(".json").read_text())
    assert done[0][0] == job.out and meta["seconds"] == 10.0 and meta["events"] == [{"type": "ace", "offset": 4.0}]


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def real_video(tmp_path_factory):
    root = tmp_path_factory.mktemp("exp")
    folder = root / "Test Game" / "2026-11-11"
    folder.mkdir(parents=True)
    path = folder / "Replay real.mkv"
    tones = [arg for hz in (440, 880) for arg in ("-f", "lavfi", "-i", f"sine=frequency={hz}:duration=12")]
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=duration=12:size=2560x1080:rate=60", *tones,
                    "-map", "0", "-map", "1", "-map", "2", "-g", "120", "-pix_fmt", "yuv420p", str(path)], check=True)
    return root, path


def real(root, path, preset, start, end, **kw):
    info, done, progress = export.probe(path), [], []
    job = Job(path, preset, start, end, info, progress.append, lambda p, why: done.append((p, why)), root=root, **kw)
    job.start()
    job.join(120)
    assert done and done[0][1] == "", done
    return done[0][0], progress, export.probe(done[0][0])


@needs_ffmpeg
def test_real_probe_and_frame(real_video, tmp_path):
    _, path = real_video
    assert export.probe(path) == Info(duration=pytest.approx(12.0, abs=0.2), width=2560, height=1080, audio_tracks=2)
    still = export.frame(path, 5.0, 192, tmp_path)
    assert still.read_bytes()[:2] == b"\xff\xd8" and export.frame(path, 5.0, 192, tmp_path) == still
    assert export.probe(tmp_path / "missing.mkv") is None and export.frame(tmp_path / "missing.mkv", 1, 100, tmp_path) is None


@needs_ffmpeg
def test_real_exact_trim_is_frame_accurate_where_stream_copy_is_not(real_video):
    root, path = real_video
    out, progress, info = real(root, path, "exact", 3.3, 7.7)  # neither end is on a 2-second keyframe
    assert info.duration == pytest.approx(4.4, abs=0.08) and info.audio_tracks == 2
    assert progress[-1] == 1.0 and all(0 <= p <= 1 for p in progress)
    copied = library.duration(library.trim(path, 3.3, 7.7))
    assert abs(copied - 4.4) > 0.3  # the fast cut lands on keyframes; that is why "exact" exists


@needs_ffmpeg
def test_real_presets_produce_the_promised_shapes(real_video):
    root, path = real_video
    _, _, vertical = real(root, path, "vertical", 0, 4)
    assert (vertical.width, vertical.height, vertical.audio_tracks) == (1080, 1920, 1)
    out, _, discord = real(root, path, "discord", 0, 10)
    assert discord.height == 720 and out.stat().st_size <= DISCORD_LIMIT
    _, _, hd = real(root, path, "1080p60", 0, 4)
    assert hd.width == 1920 and hd.height <= 1080 and hd.height % 2 == 0
    _, _, game_only = real(root, path, "1080p60", 4, 8, without_mic=True)
    assert game_only.audio_tracks == 1
