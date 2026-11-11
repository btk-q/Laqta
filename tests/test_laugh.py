import json
import shutil
import subprocess

import pytest

from laqta.obs_controller.audio import Laugh
from laqta.storage import config, library

LOUD, QUIET = 0.5, 0.01  # against the default -18 dB line (0.126)
HA = [LOUD, LOUD, QUIET, QUIET]  # one "ha": 100 ms of sound, 100 ms of breath, at 20 samples a second


def run(laugh, levels, start=0.0):
    return [round(start + i * 0.05, 2) for i, level in enumerate(levels) if laugh.feed(level, start + i * 0.05)]


def test_a_run_of_quick_bursts_is_a_laugh():
    assert run(Laugh(), [QUIET] * 10 + HA * 6 + [QUIET] * 10) == [1.1]  # at the start of the fourth "ha"


def test_a_shout_a_cough_or_slow_words_are_not():
    assert run(Laugh(), [QUIET] * 5 + [LOUD] * 60 + [QUIET] * 5) == []  # one long sound
    assert run(Laugh(), [QUIET] * 5 + HA * 2 + [QUIET] * 40) == []  # two bursts
    assert run(Laugh(), ([LOUD] * 2 + [QUIET] * 24) * 6) == []  # loud words more than a second apart


def test_one_long_laugh_is_one_moment_and_a_later_one_counts_again():
    laugh = Laugh(cooldown=30)
    assert run(laugh, HA * 40) == [0.6]  # eight seconds of laughing
    assert run(laugh, HA * 6, start=20.0) == [] and run(laugh, HA * 6, start=40.0) == [40.6]


def test_the_level_is_the_users_to_set():
    quiet_laugh = [0.05, 0.05, 0.001, 0.001] * 6
    assert run(Laugh(), quiet_laugh) == [] and run(Laugh(db=-30), quiet_laugh) == [0.6]


def test_laugh_settings_are_off_by_default_and_checked():
    assert config.DEFAULTS["laugh_trigger"] is False and config.DEFAULTS["laugh_clip_minutes"] == 5
    for bad in ({"laugh_trigger": 1}, {"laugh_db": 3}, {"laugh_clip_minutes": 60}):
        with pytest.raises(ValueError):
            config._check({**config.DEFAULTS, **bad})


def test_spans_around_moments_merge_and_stay_inside_the_video():
    spans = library.moment_spans([100, 130, 900, 2000], 1000, before=270, after=30)
    assert spans == [(0.0, 160.0), (630.0, 930.0)]  # two laughs close together are one clip; 2000 is past the end
    assert library.moment_spans([], 1000, 270, 30) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
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
    assert library.duration(made) == pytest.approx(19.0, abs=1.5)  # 30 s to 49 s: both laughs in one clip
    note = json.loads(made.with_suffix(".json").read_text())
    assert note["kind"] == "clip" and [(e["type"], e["offset"]) for e in note["events"]] == [("kill", 6.0), ("laugh", 10.0), ("laugh", 14.0)]
    assert sorted(p.name for p in folder.iterdir()) == sorted([path.name, path.with_suffix(".json").name, made.name, made.with_suffix(".json").name])
    assert library.cut_moments(path, "laugh", before=270, after=30, label="Funny") == []  # would be the whole recording: skipped


# Magnitude values OBS 33 really reported, one per 50 ms, for a test tone: four bursts a second
# (the meter repeats each value about twice and catches some gaps half-way), then a steady tone.
REAL_BURSTS = [0.459, 0.459, 0.460, 0.460, 0.002, 0.528, 0.528, 0.267, 0.267, 0.263, 0.263, 0.529, 0.529, 0.045, 0.045,
               0.375, 0.375, 0.460, 0.002, 0.002, 0.529, 0.529, 0.376, 0.376, 0.459, 0.002, 0.528, 0.528, 0.267, 0.267,
               0.045, 0.045, 0.375, 0.460, 0.002, 0.002, 0.529, 0.529]
REAL_STEADY = [0.531, 0.531, 0.529, 0.530, 0.530, 0.531, 0.531, 0.530, 0.530, 0.529] * 4


def test_against_what_obs_really_reports():
    assert len(run(Laugh(), [0.002] * 10 + REAL_BURSTS + [0.002] * 10)) == 1
    assert run(Laugh(), [0.002] * 10 + REAL_STEADY + [0.002] * 10) == []


def test_a_half_caught_gap_does_not_split_one_sound_into_bursts():
    wobble = [0.5, 0.5, 0.2, 0.5, 0.15, 0.5, 0.2, 0.5, 0.18, 0.5] * 3  # loud throughout, never under half the line... of -18 dB
    assert run(Laugh(), wobble) == []


def test_mic_magnitude_reads_the_first_value_of_the_microphone():
    from laqta.obs_controller.audio import MIC, mic_magnitude

    event = {"inputs": [{"inputName": MIC, "inputLevelsMul": [[0.1, 0.9, 0.9], [0.3, 0.9, 0.9]]}]}
    assert mic_magnitude(event) == 0.3 and mic_magnitude({}) == 0.0
