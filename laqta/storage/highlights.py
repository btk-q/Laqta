"""Pick the best moments from recordings and clips and join them into one reel. No Qt.

Score of a moment = weight of its event type x the detector's confidence. Moments close
together merge into one stretch (a multi-kill is one scene, not three), and the best
stretches are kept until the reel is full, then played in the order they happened.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from . import clips, export
from .library import Clip

# What makes a moment worth keeping. A key the player pressed outranks anything detected.
WEIGHTS = {
    "manual": 8.0, "replay": 8.0, "ace": 10.0, "multikill": 6.0, "first_blood": 4.0, "goal": 6.0, "laugh": 5.0, "save": 4.0, "kill": 3.0, "objective": 3.0,
    "win": 2.0, "shout": 2.0, "headshot": 1.0, "round_win": 1.0, "assist": 1.0, "demolition": 1.0, "death": 0.5,
    "lose": 0.0, "round_lose": 0.0, "match_start": 0.0, "match_end": 0.0,
}  # fmt: skip
BEFORE, AFTER = 8.0, 4.0  # seconds kept around a marked moment in a full recording


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    end: float
    score: float

    @property
    def length(self) -> float:
        return self.end - self.start


def score(event: dict) -> float:
    return WEIGHTS.get(event.get("type", ""), 1.0) * float(event.get("confidence", 1.0))


def windows(path: Path, events: list[dict], duration: float, before: float = BEFORE, after: float = AFTER) -> list[Segment]:
    """Stretches of one recording around its events, overlapping ones merged and their scores added."""
    spans = sorted(
        (max(0.0, e["offset"] - before), min(duration, e["offset"] + after), score(e))
        for e in events
        if 0 <= e.get("offset", -1) <= duration and score(e) > 0
    )
    merged: list[list[float]] = []
    for start, end, value in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] += value
        else:
            merged.append([start, end, value])
    return [Segment(path, s, e, v) for s, e, v in merged if e - s >= 1.0]


def candidates(items: list[Clip]) -> list[Segment]:
    """Everything a reel could use: each saved clip whole, and each marked stretch of a recording."""
    found: list[Segment] = []
    for clip in sorted(items, key=lambda c: c.when):  # the reel plays in the order things happened
        if not clip.seconds:
            continue  # length unknown: nothing safe to cut
        events = list(clip.events)
        if clip.kind == "clip":
            found.append(Segment(clip.path, 0.0, float(clip.seconds), sum(map(score, events)) or 1.0))
        else:
            found.extend(windows(clip.path, events, float(clip.seconds)))
    return found


def pick(segments: list[Segment], max_seconds: float = 60.0) -> list[Segment]:
    """The best-scoring stretches that fit in `max_seconds`, back in their original order."""
    order = {id(s): i for i, s in enumerate(segments)}
    chosen, total = [], 0.0
    for segment in sorted(segments, key=lambda s: (-s.score, order[id(s)])):
        if total + segment.length <= max_seconds:
            chosen.append(segment)
            total += segment.length
    return sorted(chosen, key=lambda s: order[id(s)])


def command(segments: list[Segment], out: Path, width: int = 1920, height: int = 1080, fps: int = 60) -> list[str]:
    """ffmpeg arguments joining the segments. Sources may differ in size and frame rate, so each
    is fitted into the same frame (letterboxed, never stretched) before they are joined."""
    if not segments:
        raise ValueError("nothing to join")
    args = ["-y", "-v", "error", "-nostats", "-progress", "pipe:1"]
    graph = []
    for i, s in enumerate(segments):
        args += ["-ss", f"{s.start:.3f}", "-to", f"{s.end:.3f}", "-i", str(s.path)]
        graph.append(
            f"[{i}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{i}];"
            f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]"
        )
    joined = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    graph.append(f"{joined}concat=n={len(segments)}:v=1:a=1[v][a]")
    return [*args, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]


def reel_path(name: str, root: Path | None = None, today: datetime.date | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name).strip() or "Laqta"
    return (root or clips.ROOT) / "Exports" / f"Highlights {safe} {(today or datetime.date.today()).isoformat()}.mp4"


class ReelJob(export.Job):
    """An export.Job that joins segments instead of converting one file."""

    def __init__(self, segments: list[Segment], out: Path, on_progress, on_done, popen=None):
        info = export.Info(sum(s.length for s in segments), 1920, 1080, 1)
        kwargs = {"popen": popen} if popen else {}
        super().__init__(segments[0].path, "1080p60", 0.0, info.duration, info, on_progress, on_done, **kwargs)
        self.segments, self.out = segments, out

    def _args(self, safety: float) -> list[str]:
        return command(self.segments, self.work)
