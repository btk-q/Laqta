"""Shared machinery for games with no local API: look at small regions of the screen a few
times a second and match HUD icons against templates.

Only the screen is read (like a screenshot tool); nothing touches the game process.
Cost control: each signal grabs just its own region, at FPS samples per second.

A vision plugin is a list of `Signal`s. Two kinds:
  count    a list that grows, e.g. killfeed rows: an event each time one more match appears
  present  a banner, e.g. VICTORY: an event when it appears
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np

from ..core.events import GameEvent, GamePlugin
from .scoring import Streak

log = logging.getLogger(__name__)

REF_HEIGHT = 1080  # templates are cut from a 1080-pixel-high screen; other sizes are rescaled to it
BLACK = 3.0  # mean brightness below this = nothing captured (exclusive fullscreen, minimised)


@dataclass(frozen=True)
class Signal:
    event: str
    roi: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the screen
    templates: tuple[np.ndarray, ...]  # BGR, at REF_HEIGHT scale; any one matching counts
    threshold: float = 0.8  # normalised correlation, 0..1
    kind: str = "count"  # "count" | "present"
    confirm: int = 2  # samples that must agree before we believe a change
    data: dict = field(default_factory=dict)


def load_templates(folder: Path, pattern: str) -> tuple[np.ndarray, ...]:
    return tuple(img for p in sorted(folder.glob(pattern)) if (img := cv2.imread(str(p), cv2.IMREAD_COLOR)) is not None)


def crop(frame: np.ndarray, roi: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = roi
    return frame[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]


def to_reference(region: np.ndarray, screen_height: int) -> np.ndarray:
    """Rescale a region as if the screen were REF_HEIGHT high, so one template set fits all."""
    if screen_height == REF_HEIGHT:
        return region
    scale = REF_HEIGHT / screen_height
    return cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


def find(region: np.ndarray, signal: Signal) -> tuple[int, float]:
    """(how many separate matches, best score) for a region already at reference scale."""
    count, best = 0, 0.0
    for template in signal.templates:
        th, tw = template.shape[:2]
        if region.shape[0] < th or region.shape[1] < tw:
            continue
        scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        found = 0
        for _ in range(12):  # more rows than any HUD list shows
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            best = max(best, float(score))
            if score < signal.threshold:
                break
            found += 1
            # Blank this match's neighbourhood so the next peak is a different row/icon.
            scores[max(0, y - th // 2) : y + th // 2 + 1, max(0, x - tw // 2) : x + tw // 2 + 1] = -1.0
        count = max(count, found)
    return count, best


class Edge:
    """Turns noisy per-sample observations of one signal into events."""

    def __init__(self, signal: Signal):
        self.signal = signal
        self.stable = 0  # last believed count (present: 0 or 1)
        self._candidate, self._seen = 0, 0

    def feed(self, count: int, score: float, now: float) -> list[GameEvent]:
        if self.signal.kind == "present":
            count = min(count, 1)
        if count == self.stable:
            self._seen = 0
            return []
        if count != self._candidate:
            self._candidate, self._seen = count, 0
        self._seen += 1
        if self._seen < self.signal.confirm:
            return []
        previous, self.stable, self._seen = self.stable, count, 0
        # ponytail: a row expiring in the same sample a new one appears leaves the count
        # unchanged and that kill is missed; track row positions if this shows up in practice.
        return [
            GameEvent(self.signal.event, now, confidence=min(1.0, max(0.0, score)), source="vision", data=dict(self.signal.data))
            for _ in range(max(0, count - previous))
        ]


class Watcher:
    """All of a plugin's signals over one stream of frames. Pure: give it regions, get events."""

    def __init__(self, signals: list[Signal], streak: Streak | None = None):
        self.edges = [Edge(s) for s in signals]
        self.streak = streak  # adds multi-kills on top of the kills seen

    def feed(self, regions: list[np.ndarray | None], screen_height: int, now: float) -> list[GameEvent]:
        """regions[i] is the screen crop for signals[i] (None or black = nothing captured)."""
        out: list[GameEvent] = []
        for edge, region in zip(self.edges, regions):
            if region is None or region.size == 0 or float(region.mean()) < BLACK:
                continue  # keep state: a black frame is "no information", not "icon gone"
            count, score = find(to_reference(region, screen_height), edge.signal)
            out += edge.feed(count, score, now)
        if self.streak:
            out += [extra for event in list(out) for extra in self.streak.feed(event)]
        return out


def run_video(signals: list[Signal], path: Path, fps: float = 3.0, streak: Streak | None = None) -> Iterator[tuple[float, GameEvent]]:
    """Run the same detection over a recording: yields (seconds into the video, event).
    For calibrating templates and thresholds against real gameplay."""
    video = cv2.VideoCapture(str(path))
    if not video.isOpened():
        raise OSError(f"cannot open {path}")
    step = max(1, round((video.get(cv2.CAP_PROP_FPS) or 60) / fps))
    watcher, index = Watcher(signals, streak), 0
    try:
        while video.grab():
            if index % step == 0:
                ok, frame = video.retrieve()
                if not ok:
                    break
                t = index / (video.get(cv2.CAP_PROP_FPS) or 60)
                for event in watcher.feed([crop(frame, s.roi) for s in signals], frame.shape[0], t):
                    yield t, event
            index += 1
    finally:
        video.release()


# -- calibration: cut templates out of a real recording ---------------------------------------


def frame_at(path: Path, seconds: float) -> np.ndarray:
    video = cv2.VideoCapture(str(path))
    if not video.isOpened():
        raise OSError(f"cannot open {path}")
    try:
        video.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = video.read()
    finally:
        video.release()
    if not ok:
        raise ValueError(f"no picture at {seconds:.1f} s; is that past the end of the video?")
    return frame


def reference_region(frame: np.ndarray, signal: Signal) -> np.ndarray:
    """The signal's region exactly as detection sees it. Templates must be cut from this."""
    return to_reference(crop(frame, signal.roi), frame.shape[0])


def outlined(frame: np.ndarray, signals: list[Signal]) -> np.ndarray:
    """The frame with each signal's region drawn on it, to check the regions sit on the HUD."""
    picture = frame.copy()
    h, w = picture.shape[:2]
    thick = max(2, h // 400)
    for signal in signals:
        x0, y0, x1, y1 = signal.roi
        corner = (int(x0 * w), int(y0 * h))
        cv2.rectangle(picture, corner, (int(x1 * w), int(y1 * h)), (0, 255, 255), thick)
        cv2.putText(picture, signal.event, (corner[0] + 6, corner[1] + 12 * thick), cv2.FONT_HERSHEY_SIMPLEX, thick / 3, (0, 255, 255), thick)
    return picture


def inspect(signals: list[Signal], frame: np.ndarray, out: Path) -> list[str]:
    """Save the outlined frame and each region; report what the current templates score there."""
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "frame.png"), outlined(frame, signals))
    lines = []
    for signal in signals:
        region = reference_region(frame, signal)
        cv2.imwrite(str(out / f"region_{signal.event}.png"), region)
        if not signal.templates:
            lines.append(f"{signal.event:<10} region {region.shape[1]}x{region.shape[0]}  no templates yet")
        else:
            count, score = find(region, signal)
            verdict = f"{count} match(es)" if count else "no match"
            lines.append(f"{signal.event:<10} region {region.shape[1]}x{region.shape[0]}  best score {score:.2f} (needs {signal.threshold:.2f}): {verdict}")
    return lines


def cut_template(frame: np.ndarray, signal: Signal, box: tuple[int, int, int, int], assets: Path) -> Path:
    """Save the box (x, y, width, height inside the signal's region image) as the next template."""
    region = reference_region(frame, signal)
    x, y, w, h = box
    if min(x, y) < 0 or w < 4 or h < 4 or x + w > region.shape[1] or y + h > region.shape[0]:
        raise ValueError(f"the box must lie inside the {region.shape[1]}x{region.shape[0]} region and be at least 4x4")
    assets.mkdir(parents=True, exist_ok=True)
    number = 1 + len(list(assets.glob(f"{signal.event}_*.png")))
    while (target := assets / f"{signal.event}_{number}.png").exists():
        number += 1
    cv2.imwrite(str(target), region[y : y + h, x : x + w])
    return target


class VisionPlugin(GamePlugin):
    """Subclass, set `name`, `event_types`, `assets`, and return your signals from `signals()`."""

    fps: float = 3.0
    assets: Path  # folder the template images live in
    streak_window: float | None = 10.0  # kills this close together make a multi-kill; None = do not derive any
    streak_ace: int | None = None  # see scoring.Streak before setting this

    def streak(self) -> Streak | None:
        return Streak(self.streak_window, self.streak_ace) if self.streak_window else None

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def signals(self) -> list[Signal]:
        raise NotImplementedError

    def start(self, emit: Callable[[GameEvent], None], game: dict) -> None:
        signals = [s for s in self.signals() if s.templates]
        if not signals:
            log.warning("%s: no templates installed, event detection is off", self.name)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(signals, emit), name=f"laqta-{self.name}", daemon=True)
        self._thread.start()

    def _run(self, signals: list[Signal], emit: Callable[[GameEvent], None]) -> None:
        import mss  # here, not at module import: only needed while a vision game is running

        watcher = Watcher(signals, self.streak())
        with mss.MSS() as screen:
            # ponytail: primary monitor, GDI capture. Fine for borderless/windowed; exclusive
            # fullscreen returns black (skipped above). Switch to DXGI duplication if needed.
            monitor = screen.monitors[1]
            boxes = [
                {
                    "left": monitor["left"] + int(s.roi[0] * monitor["width"]),
                    "top": monitor["top"] + int(s.roi[1] * monitor["height"]),
                    "width": max(1, int((s.roi[2] - s.roi[0]) * monitor["width"])),
                    "height": max(1, int((s.roi[3] - s.roi[1]) * monitor["height"])),
                }
                for s in signals
            ]
            while not self._stop.wait(1.0 / self.fps):
                try:
                    regions = [np.asarray(screen.grab(box))[:, :, :3] for box in boxes]  # BGRA -> BGR
                    for event in watcher.feed(regions, monitor["height"], time.monotonic()):
                        emit(event)
                except Exception:
                    log.exception("%s: frame analysis failed", self.name)

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
