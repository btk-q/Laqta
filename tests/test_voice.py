import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from laqta import voice
from laqta.storage import config, library

FIX = Path(__file__).parent / "fixtures" / "voice"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def scores(laugh=0.0, shout=0.0, speech=0.0):
    s = np.zeros(521, np.float32)
    s[13], s[11], s[9], s[0] = laugh, shout / 2, shout / 2, speech  # shout spread over Screaming and Yell, as YAMNet does
    return s


def test_judge_sums_each_kind_and_ignores_speech():
    assert voice.judge(scores(laugh=0.6)) == ("laugh", pytest.approx(0.6))
    assert voice.judge(scores(shout=0.46)) == ("shout", pytest.approx(0.46))  # neither class alone would pass
    assert voice.judge(scores(speech=1.0)) is None
    assert voice.judge(scores(laugh=0.2, shout=0.1)) is None
    assert voice.judge(scores(laugh=0.5, shout=0.9))[0] == "shout"


def test_merge_joins_a_run_and_keeps_its_strongest_moment():
    hits = [(10.0, "laugh", 0.4), (10.5, "laugh", 0.9), (11.0, "laugh", 0.5), (12.0, "shout", 0.5), (30.0, "laugh", 0.3)]
    assert voice.merge(hits) == [voice.Moment("laugh", 10.5, 0.9), voice.Moment("shout", 12.0, 0.5), voice.Moment("laugh", 30.0, 0.3)]
    assert voice.merge([]) == []


def test_windows_come_every_half_second_from_chunks_of_any_size_and_the_end_is_padded():
    sound = np.arange(16000 * 3, dtype=np.float32)
    chunks = [sound[i : i + 1234] for i in range(0, len(sound), 1234)]
    got = list(voice.windows(iter(chunks)))
    assert [round(t, 2) for t, _ in got] == [0.0, 0.48, 0.96, 1.44, 1.92, 2.4]
    assert all(len(w) == voice.WINDOW for _, w in got) and got[1][1][0] == voice.HOP
    assert got[-1][1][-1] == 0  # the last, short part is padded with silence


def test_silence_never_runs_the_model():
    class Refuse:
        def scores(self, window):
            raise AssertionError("the model ran on silence")

    assert voice.find(iter([np.zeros(16000 * 5, np.float32)]), Refuse()) == []


@needs_ffmpeg
def test_real_laugh_scream_and_speech_are_told_apart():
    """Real recordings: a public-domain laugh, the Wilhelm scream, and fast urgent speech."""
    clf = voice.Classifier()
    found = {name: [m.type for m in voice.find(voice.read(FIX / f"{name}.ogg", "0:a:0"), clf)] for name in ("laugh", "scream", "speech")}
    assert found["laugh"] and set(found["laugh"]) == {"laugh"}
    assert found["scream"] == ["shout"]
    assert found["speech"] == []


@needs_ffmpeg
def test_a_recording_gets_its_laughs_and_shouts_marked_and_a_funny_clip(tmp_path):
    """End to end on a recording with three audio tracks: talk, then a laugh at 20 s, a scream at 50 s."""
    folder = tmp_path / "Game" / "2026-11-13"
    folder.mkdir(parents=True)
    mic = tmp_path / "mic.wav"
    parts = ["speech", "laugh", "speech", "scream", "speech"]
    starts = [0, 20, 30, 50, 55]
    graph = "".join(f"[{i}:a]adelay={s * 1000}|{s * 1000}[a{i}];" for i, s in enumerate(starts))
    subprocess.run(["ffmpeg", "-v", "error", "-y", *[a for p in parts for a in ("-i", str(FIX / f"{p}.ogg"))],
                    "-filter_complex", graph + "".join(f"[a{i}]" for i in range(len(parts))) + f"amix=inputs={len(parts)}:normalize=0,apad=whole_dur=70",
                    "-ar", "16000", "-ac", "1", str(mic)], check=True)
    path = folder / "2026-11-13 20-00-00.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=duration=70:size=320x180:rate=30",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-i", str(mic),
                    "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-t", "70", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    old = [{"type": "kill", "offset": 5.0}, {"type": "shout", "source": "audio", "offset": 33.0}]  # a live loudness guess: replaced
    path.with_suffix(".json").write_text(json.dumps({"kind": "recording", "game": "Game", "seconds": 70.0, "events": old}))

    moments, made = voice.process(path, clip_minutes=1, label="Funny")
    kinds = [(m.type, m.offset) for m in moments]
    assert [k for k, _ in kinds] == ["laugh", "shout"], kinds
    assert 19 <= kinds[0][1] <= 25 and 49 <= kinds[1][1] <= 52.5
    events = json.loads(path.with_suffix(".json").read_text())["events"]
    assert [(e["type"], e.get("source")) for e in events] == [("kill", None), ("laugh", "voice"), ("shout", "voice")]
    (clip,) = made
    assert clip.name.startswith("Funny ") and library.duration(clip) > 30


def test_voice_settings_are_off_by_default_and_checked():
    assert config.DEFAULTS["voice_detect"] is False and config.DEFAULTS["laugh_clip_minutes"] == 5
    for bad in ({"voice_detect": 1}, {"laugh_clip_minutes": 60}):
        with pytest.raises(ValueError):
            config._check({**config.DEFAULTS, **bad})


def test_spans_around_moments_merge_and_stay_inside_the_video():
    spans = library.moment_spans([100, 130, 900, 2000], 1000, before=270, after=30)
    assert spans == [(0.0, 160.0), (630.0, 930.0)]
    assert library.moment_spans([], 1000, 270, 30) == []


@needs_ffmpeg
def test_funny_moments_are_cut_out_of_a_real_recording_and_the_recording_is_kept(tmp_path):
    folder = tmp_path / "Game" / "2026-11-11"
    folder.mkdir(parents=True)
    path = folder / "2026-11-11 10-00-00.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=duration=60:size=320x180:rate=30", "-g", "30",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    events = [{"type": "kill", "offset": 36.0}, {"type": "laugh", "offset": 40.0}, {"type": "laugh", "offset": 44.0}, {"type": "kill", "offset": 5.0}]
    path.with_suffix(".json").write_text(json.dumps({"kind": "recording", "game": "Game", "seconds": 60.0, "events": events}))
    size = path.stat().st_size
    (made,) = library.cut_moments(path, "laugh", before=10, after=5, label="Funny")
    assert made.name == "Funny 2026-11-11 10-00-00 00m30s.mkv" and path.stat().st_size == size
    assert library.duration(made) == pytest.approx(19.0, abs=1.5)
    note = json.loads(made.with_suffix(".json").read_text())
    assert note["kind"] == "clip" and [(e["type"], e["offset"]) for e in note["events"]] == [("kill", 6.0), ("laugh", 10.0), ("laugh", 14.0)]
    assert library.cut_moments(path, "laugh", before=270, after=30, label="Funny") == []
