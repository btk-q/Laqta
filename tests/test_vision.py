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
