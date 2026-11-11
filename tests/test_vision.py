"""Vision machinery on synthetic frames: a random 'icon' pasted into a noisy screen.
Real-game frames belong here too once a Valorant recording exists."""

import cv2
import numpy as np
import pytest

from laqta.plugins import vision
from laqta.plugins.valorant_vision import ValorantPlugin
from laqta.plugins.vision import Signal, Watcher, crop

rng = np.random.default_rng(7)
ICON = rng.integers(0, 255, (24, 24, 3), dtype=np.uint8)
ROI = (0.70, 0.0, 1.0, 0.30)
KILL = Signal("kill", ROI, (ICON,))


def screen(rows=(), height=1080, banner=False):
    """A 16:9 noisy frame with the icon on the given killfeed rows (in 1080p coordinates)."""
    frame = np.random.default_rng(1).integers(40, 90, (1080, 1920, 3), dtype=np.uint8)
    for row in rows:
        y = 40 + row * 40
        frame[y : y + 24, 1700 : 1700 + 24] = ICON
    if banner:
        frame[200:224, 900:924] = ICON
    if height != 1080:
        frame = cv2.resize(frame, (height * 16 // 9, height), interpolation=cv2.INTER_AREA)
    return frame


def feed(watcher, frames, signals=(KILL,)):
    out = []
    for t, frame in enumerate(frames):
        regions = [None if frame is None else crop(frame, s.roi) for s in signals]
        height = 1080 if frame is None else frame.shape[0]
        out.append([e.type for e in watcher.feed(regions, height, float(t))])
    return out


def test_find_counts_separate_rows_and_ignores_noise():
    assert vision.find(crop(screen(), ROI), KILL)[0] == 0
    count, score = vision.find(crop(screen(rows=(0, 2, 3)), ROI), KILL)
    assert count == 3 and score > 0.99


def test_a_new_row_is_one_event_after_two_agreeing_samples():
    w = Watcher([KILL])
    a, b = screen(rows=(0,)), screen(rows=(0, 1))
    assert feed(w, [screen(), a, a, a, b, b, b, screen(), screen()]) == [[], [], ["kill"], [], [], ["kill"], [], [], []]


def test_single_sample_glitch_is_not_an_event():
    w = Watcher([KILL])
    assert feed(w, [screen(), screen(rows=(0,)), screen(), screen(rows=(0,)), screen()]) == [[]] * 5


def test_two_kills_in_one_sample_are_two_events():
    w = Watcher([KILL])
    b = screen(rows=(0, 1))
    assert feed(w, [screen(), b, b]) == [[], [], ["kill", "kill"]]


def test_black_or_missing_frames_do_not_reset_the_count():
    w = Watcher([KILL])
    a = screen(rows=(0,))
    black = np.zeros((1080, 1920, 3), np.uint8)
    assert feed(w, [a, a, black, None, black, a, a]) == [[], ["kill"], [], [], [], [], []]


@pytest.mark.parametrize("height", [720, 1440, 2160])
def test_other_resolutions_are_rescaled_to_the_template(height):
    w = Watcher([Signal("kill", ROI, (ICON,), threshold=0.6)])  # resampling blurs a random-noise icon
    a = screen(rows=(1,), height=height)
    assert feed(w, [screen(height=height), a, a])[-1] == ["kill"]


def test_present_signal_fires_once_per_appearance():
    win = Signal("win", (0.30, 0.10, 0.70, 0.35), (ICON,), kind="present")
    w = Watcher([win])
    on, off = screen(banner=True), screen()
    assert feed(w, [off, on, on, on, on, off, off, on, on], [win]) == [[], [], ["win"], [], [], [], [], [], ["win"]]


def test_event_carries_vision_source_and_match_score():
    w = Watcher([KILL])
    a = screen(rows=(0,))
    w.feed([crop(a, ROI)], 1080, 0.0)
    (event,) = w.feed([crop(a, ROI)], 1080, 1.0)
    assert event.source == "vision" and event.confidence > 0.99 and event.t == 1.0


def test_run_video_reports_times(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (1920, 1080))
    for i in range(90):  # 3 s: icon appears at 1.0 s
        writer.write(screen(rows=(0,)) if i >= 30 else screen())
    writer.release()
    signal = Signal("kill", ROI, (ICON,), threshold=0.6)  # MJPG compression softens the icon
    hits = [(round(t, 1), e.type) for t, e in vision.run_video([signal], path, fps=6)]
    assert len(hits) == 1 and hits[0][1] == "kill" and 1.0 <= hits[0][0] <= 1.4


def test_uncalibrated_valorant_plugin_starts_and_stops_without_a_thread():
    plugin = ValorantPlugin()
    assert all(not s.templates for s in plugin.signals()), "remove this test once templates are shipped"
    plugin.start(lambda e: None, {"exe": "VALORANT-Win64-Shipping.exe"})
    assert plugin._thread is None
    plugin.stop()


# -- multi-kills from plain kills ---------------------------------------------------------------

from laqta.core.events import GameEvent  # noqa: E402
from laqta.plugins.scoring import Streak  # noqa: E402


def streak_types(streak, *events):
    return [[(e.type, e.data.get("count")) for e in streak.feed(GameEvent(type, t, confidence))] for type, t, confidence in events]


def test_kills_close_together_become_a_growing_multikill():
    out = streak_types(Streak(window=10), ("kill", 0, 1.0), ("kill", 4, 1.0), ("kill", 13, 1.0), ("kill", 40, 1.0), ("kill", 45, 1.0))
    assert out == [[], [("multikill", 2)], [("multikill", 3)], [], [("multikill", 2)]]


def test_dying_or_a_new_round_ends_the_streak():
    for reset in ("death", "round_win", "round_lose", "match_end"):
        out = streak_types(Streak(), ("kill", 0, 1.0), (reset, 1, 1.0), ("kill", 2, 1.0), ("assist", 3, 1.0), ("kill", 4, 1.0))
        assert out == [[], [], [], [], [("multikill", 2)]], reset


def test_ace_is_only_claimed_when_asked_for_and_then_starts_over():
    five = [("kill", t, 1.0) for t in range(5)]
    assert streak_types(Streak(), *five)[-1] == [("multikill", 5)]
    out = streak_types(Streak(ace=5), *five, ("kill", 6, 1.0))
    assert out[4] == [("ace", None)] and out[5] == []


def test_a_streak_is_as_sure_as_its_weakest_kill():
    streak = Streak()
    streak.feed(GameEvent("kill", 0, 0.9, "vision"))
    streak.feed(GameEvent("kill", 1, 0.6, "vision"))
    (third,) = streak.feed(GameEvent("kill", 2, 0.95, "vision"))
    assert third.confidence == 0.6 and third.source == "vision" and third.t == 2


def test_watcher_adds_multikills_when_given_a_streak():
    w = Watcher([KILL], Streak(window=10))
    a, b = screen(rows=(0,)), screen(rows=(0, 1))
    assert feed(w, [screen(), a, a, b, b]) == [[], [], ["kill"], [], ["kill", "multikill"]]
    assert ValorantPlugin().streak().window == 10 and "multikill" in ValorantPlugin.event_types


# -- calibration --------------------------------------------------------------------------------


def test_cut_a_template_from_a_recording_and_detect_with_it(tmp_path):
    """The whole calibration loop: look at a moment, cut the icon out, then find it elsewhere."""
    path = tmp_path / "match.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (1280, 720))  # not the 1080p templates assume
    for i in range(90):
        writer.write(screen(rows=(0,) if i >= 30 else (), height=720))
    writer.release()
    blank = Signal("kill", ROI, (), threshold=0.6)
    frame = vision.frame_at(path, 2.0)
    assert frame.shape[:2] == (720, 1280)

    lines = vision.inspect([blank], frame, tmp_path / "look")
    assert "no templates yet" in lines[0] and "576x324" in lines[0]  # the region, at 1080p scale
    assert cv2.imread(str(tmp_path / "look" / "region_kill.png")).shape[:2] == (324, 576)
    assert cv2.imread(str(tmp_path / "look" / "frame.png")).shape[:2] == (720, 1280)

    # In region_kill.png the icon sits at x 1700-1344=356, y 40.
    first = vision.cut_template(frame, blank, (356, 40, 24, 24), tmp_path / "assets")
    second = vision.cut_template(frame, blank, (356, 40, 24, 24), tmp_path / "assets")
    assert (first.name, second.name) == ("kill_1.png", "kill_2.png")
    made = Signal("kill", ROI, vision.load_templates(tmp_path / "assets", "kill_*.png"), threshold=0.6)
    assert "1 match(es)" in vision.inspect([made], frame, tmp_path / "look")[0]
    assert "no match" in vision.inspect([made], vision.frame_at(path, 0.2), tmp_path / "look")[0]
    hits = [(round(t, 1), e.type) for t, e in vision.run_video([made], path, fps=6)]
    assert len(hits) == 1 and 1.0 <= hits[0][0] <= 1.4


def test_calibration_refuses_nonsense(tmp_path):
    frame = screen()
    for box in ((-1, 0, 10, 10), (0, 0, 2, 2), (570, 0, 24, 24), (0, 310, 24, 24)):
        with pytest.raises(ValueError):
            vision.cut_template(frame, KILL, box, tmp_path)
    assert not list(tmp_path.iterdir())
    with pytest.raises(OSError):
        vision.frame_at(tmp_path / "missing.mkv", 1)


def test_clock_seconds():
    from laqta.__main__ import clock_seconds

    assert [clock_seconds(x) for x in ("754", "12:34", "1:02:03.5")] == [754, 754, 3723.5]
