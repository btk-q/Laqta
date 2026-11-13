"""Find laughs and shouts in the player's microphone track, after a recording has ended.

The microphone is recorded on its own audio track (track 3), so this reads that track from
the finished file with ffmpeg and runs YAMNet, Google's sound classifier (Apache 2.0, the
copy published for MediaPipe), over the loud parts. YAMNet knows "Laughter", "Giggle",
"Chuckle", "Shout", "Yell", "Screaming" and ordinary "Speech" apart, which loudness alone
cannot. Nothing runs while the game is being played, and nothing leaves the PC.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

log = logging.getLogger(__name__)

MODEL = Path(__file__).with_name("yamnet.tflite")
RATE = 16000
WINDOW = 15600  # samples YAMNet looks at in one go (0.975 s)
HOP = 7680  # move 0.48 s between looks
QUIET = 10 ** (-38 / 20)  # windows quieter than -38 dBFS are skipped without running the model
MIC_TRACK = "0:a:2"  # audio track 3 is the microphone (see obs_controller.audio)

# YAMNet class numbers (from the label list inside the model file).
LAUGH = (13, 14, 15, 16, 17, 18)  # Laughter, Baby laughter, Giggle, Snicker, Belly laugh, Chuckle
SHOUT = (6, 8, 9, 10, 11)  # Shout, Whoop, Yell, Children shouting, Screaming
# A kind counts when its classes together score this much. Summing matters: a scream is spread
# over Yell, Screaming and Shout, none of them high alone. Measured on public-domain laughs and
# screams and on calm and fast speech: laughs reached 0.69-1.37, screams 0.46-0.48, speech 0.00.
THRESHOLD = {"laugh": 0.3, "shout": 0.3}
JOIN = 3.0  # seconds: hits of the same kind closer than this are one moment


@dataclass(frozen=True)
class Moment:
    type: str  # "laugh" | "shout"
    offset: float  # seconds into the audio
    confidence: float  # YAMNet's score, 0..1


def labels() -> list[str]:
    """YAMNet's 521 class names, stored in the model file's own metadata."""
    with zipfile.ZipFile(io.BytesIO(MODEL.read_bytes())) as z:
        return z.read("yamnet_label_list.txt").decode("utf-8").splitlines()


class Classifier:
    """YAMNet on one CPU thread: about 5 ms per second of sound on this PC."""

    def __init__(self, threads: int = 1):
        from ai_edge_litert.interpreter import Interpreter  # here: only loaded when a recording is analysed

        self._it = Interpreter(model_path=str(MODEL), num_threads=threads)
        self._it.allocate_tensors()
        self._in = self._it.get_input_details()[0]["index"]
        self._out = self._it.get_output_details()[0]["index"]

    def scores(self, window: np.ndarray) -> np.ndarray:
        self._it.set_tensor(self._in, window.astype(np.float32))
        self._it.invoke()
        return self._it.get_tensor(self._out)[0]


def judge(scores: np.ndarray) -> tuple[str, float] | None:
    """Laugh, shout or neither, for one window's scores. Pure."""
    laugh, shout = float(scores[list(LAUGH)].sum()), float(scores[list(SHOUT)].sum())
    kind, score = ("laugh", laugh) if laugh >= shout else ("shout", shout)
    return (kind, min(1.0, score)) if score >= THRESHOLD[kind] else None


def merge(hits: list[tuple[float, str, float]], join: float = JOIN) -> list[Moment]:
    """Window hits (time, kind, score) -> moments: a run of the same kind is one moment, placed
    at its strongest window. Pure."""
    moments: list[list] = []  # [kind, last time, best time, best score]
    for t, kind, score in sorted(hits):
        if moments and moments[-1][0] == kind and t - moments[-1][1] <= join:
            run = moments[-1]
            run[1] = t
            if score > run[3]:
                run[2], run[3] = t, score
        else:
            moments.append([kind, t, t, score])
    return [Moment(kind, round(best, 2), round(score, 3)) for kind, _, best, score in moments]


def windows(samples: Iterator[np.ndarray]) -> Iterator[tuple[float, np.ndarray]]:
    """(start time, 0.975 s of sound) every 0.48 s, from audio arriving in chunks of any size."""
    buffer, start = np.zeros(0, np.float32), 0
    for chunk in samples:
        buffer = np.concatenate((buffer, chunk))
        while len(buffer) >= WINDOW:
            yield start / RATE, buffer[:WINDOW]
            buffer, start = buffer[HOP:], start + HOP
    if len(buffer) > HOP // 4:  # the last part, padded with silence: a shout can be the final second
        yield start / RATE, np.concatenate((buffer, np.zeros(WINDOW - len(buffer), np.float32)))


def find(samples: Iterator[np.ndarray], classifier: Classifier | None = None) -> list[Moment]:
    """Laughs and shouts in a stream of 16 kHz mono samples."""
    hits = []
    for t, window in windows(samples):
        if float(np.abs(window).max()) < QUIET:
            continue  # silence: no need to ask the model
        classifier = classifier or Classifier()
        if found := judge(classifier.scores(window)):
            hits.append((t, *found))
    return merge(hits)


def read(path: Path, stream: str = MIC_TRACK, chunk_seconds: float = 10.0) -> Iterator[np.ndarray]:
    """One audio track of a video as 16 kHz mono float samples, a chunk at a time (a long
    recording never sits in memory whole)."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise OSError("ffmpeg was not found")
    process = subprocess.Popen(
        [exe, "-v", "error", "-i", str(path), "-map", stream, "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )  # fmt: skip
    size = int(RATE * chunk_seconds) * 4
    try:
        while data := process.stdout.read(size):
            yield np.frombuffer(data[: len(data) // 4 * 4], np.float32)
    finally:
        process.kill()
        process.wait()


def analyse(path: Path, stream: str = MIC_TRACK) -> list[Moment]:
    """Laughs and shouts on the microphone track of a recording. [] if it has no such track."""
    return find(read(path, stream))


def mark(path: Path, moments: list[Moment]) -> None:
    """Write the moments into the recording's event list. Laughs and shouts guessed earlier from
    loudness alone (the live shout trigger) are replaced: this result is the better one."""
    sidecar = path.with_suffix(".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    kept = [e for e in meta.get("events", []) if not (e.get("type") in ("laugh", "shout") and e.get("source") == "audio")]
    found = [{"type": m.type, "source": "voice", "confidence": m.confidence, "offset": m.offset} for m in moments]
    meta["events"] = sorted(kept + found, key=lambda e: e.get("offset", 0))
    sidecar.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def process(path: Path, clip_minutes: int, label: str) -> tuple[list[Moment], list[Path]]:
    """After a recording: find laughs and shouts, mark them, and save the minutes leading up to
    each laugh as its own clip. Returns (moments, clips made)."""
    from ..storage import library

    moments = analyse(path)
    mark(path, moments)
    made = []
    if clip_minutes and any(m.type == "laugh" for m in moments):
        made = library.cut_moments(path, "laugh", before=max(clip_minutes * 60 - 30, 10), after=30, label=label)
    return moments, made
