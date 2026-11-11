"""Re-encoding exports: exact trims and sharing presets, with progress and cancel.

Pure command builders plus a small job runner. No Qt. Every recipe here was run on this
project's ffmpeg before being written down; see docs/adr/0002-export-recipes.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import clips

log = logging.getLogger(__name__)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DISCORD_LIMIT = 10 * 1024 * 1024  # bytes a free Discord upload may be
PRESETS = ("exact", "vertical", "discord", "1080p60")


@dataclass(frozen=True)
class Info:
    duration: float
    width: int
    height: int
    audio_tracks: int


def probe(path: Path) -> Info | None:
    exe = shutil.which("ffprobe")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, timeout=20, creationflags=NO_WINDOW,
        )  # fmt: skip
        data = json.loads(out.stdout)
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        return Info(float(data["format"]["duration"]), int(video["width"]), int(video["height"]),
                    sum(s["codec_type"] == "audio" for s in data["streams"]))
    except (OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired):
        return None


def frame(path: Path, t: float, width: int, cache: Path) -> Path | None:
    """One still from `path` at `t` seconds, `width` pixels wide, cached on disk."""
    exe = shutil.which("ffmpeg")
    try:
        stat = path.stat()
    except OSError:
        return None
    key = hashlib.sha1(f"{path}|{stat.st_size}|{stat.st_mtime_ns}|{t:.2f}|{width}".encode()).hexdigest()[:20]
    target = cache / f"f{key}.jpg"
    if target.exists():
        return target
    if exe is None:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [exe, "-y", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(path), "-an", "-frames:v", "1",
             "-vf", f"scale={width}:-2", str(target)],
            capture_output=True, timeout=30, creationflags=NO_WINDOW,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    return target if target.exists() and target.stat().st_size > 0 else None


# -- command builders (pure) ----------------------------------------------------------------


def discord_video_kbps(seconds: float, safety: float = 0.95, audio_kbps: int = 96) -> int:
    """Video bitrate that makes `seconds` of video fit the Discord limit, leaving room for audio."""
    total = DISCORD_LIMIT * 8 * safety / max(seconds, 0.5) / 1000
    return max(100, int(total - audio_kbps))


def output_path(src: Path, preset: str, start: float, end: float, root: Path | None = None) -> Path:
    span = f"{int(start)}-{int(end)}"
    if preset == "exact":  # a trimmed copy stays in the library, next to its source
        return src.with_name(f"{src.stem} trim {span}{src.suffix}")
    folder = (root or clips.ROOT) / "Exports"
    return folder / f"{src.parents[1].name} {src.stem} {span} {preset}.mp4"


def command(src: Path, out: Path, preset: str, start: float, end: float, info: Info, without_mic: bool = False,
            safety: float = 0.95) -> list[str]:
    """ffmpeg arguments (without the program name) for one export of seconds [start, end]."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}")
    if not 0 <= start < end:
        raise ValueError("The end must be after the start.")
    head = ["-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src)]
    # Track 1 is the full mix. Track 2 is the game alone: use it to leave the microphone out.
    audio = "0:a:1" if without_mic and info.audio_tracks >= 2 else "0:a:0?"
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if preset == "exact":
        # Whole-clip re-encode: the only frame-accurate cut that proved reliable ("smart cut" did not).
        return [*head, "-map", "0", *x264, "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)]
    if preset == "vertical":
        crop = "crop=trunc(ih*9/16/2)*2:ih,scale=1080:1920,setsar=1"  # centre strip of the picture
        return [*head, "-map", "0:v:0", "-map", audio, "-vf", crop, *x264, "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(out)]
    if preset == "discord":
        kbps = discord_video_kbps(end - start, safety)
        return [*head, "-map", "0:v:0", "-map", audio, "-vf", "scale=-2:720,fps=30", *x264,
                "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)]
    scale = "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=60"
    return [*head, "-map", "0:v:0", "-map", audio, "-vf", scale, *x264, "-crf", "20", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(out)]


def progress_seconds(line: str) -> float | None:
    """Seconds encoded so far, from one line of `-progress` output (None for other lines)."""
    key, _, value = line.strip().partition("=")
    if key != "out_time_us":  # out_time_ms is also microseconds; ffmpeg's naming, not ours
        return None
    try:
        return max(0.0, int(value) / 1_000_000)
    except ValueError:
        return None  # "N/A" at the very start


# -- job runner ---------------------------------------------------------------------------------


class Job:
    """One export running in a background thread. Callbacks arrive on that thread."""

    def __init__(
        self,
        src: Path,
        preset: str,
        start: float,
        end: float,
        info: Info,
        on_progress: Callable[[float], None],  # 0..1
        on_done: Callable[[Path | None, str], None],  # (file, "") or (None, reason); reason "cancelled" if stopped
        without_mic: bool = False,
        root: Path | None = None,
        popen: Callable = subprocess.Popen,
    ):
        self.src, self.preset, self.start_s, self.end_s, self.info = src, preset, start, end, info
        self.on_progress, self.on_done, self.without_mic, self.popen = on_progress, on_done, without_mic, popen
        self.out = output_path(src, preset, start, end, root)
        self._process = None
        self._cancelled = False
        self._thread = threading.Thread(target=self._run, name="laqta-export", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        """Ask ffmpeg to stop. "q" on stdin lets it close the file; terminate() leaves a broken one."""
        self._cancelled = True
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.stdin.write(b"q")
                process.stdin.flush()
            except (OSError, ValueError):
                process.kill()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def work(self) -> Path:
        """Where ffmpeg writes. The real name appears only once the file is complete, so a failed
        or cancelled export can never damage a file of the same name made earlier."""
        return self.out.with_name(f"{self.out.stem}.part{self.out.suffix}")

    def _args(self, safety: float) -> list[str]:
        return command(self.src, self.work, self.preset, self.start_s, self.end_s, self.info, self.without_mic, safety)

    def _encode(self, safety: float) -> str:
        """Run ffmpeg once. Returns "" on success, else what went wrong."""
        exe = shutil.which("ffmpeg")
        if exe is None:
            return "ffmpeg was not found. Install ffmpeg to use this."
        args = self._args(safety)
        self._process = process = self.popen([exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        if self._cancelled:
            self.cancel()  # Cancel was pressed before ffmpeg had started: stop it now
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
        drain.start()  # a full stderr pipe would stall ffmpeg
        length = max(self.end_s - self.start_s, 0.001)
        for raw in process.stdout:
            done = progress_seconds(raw.decode(errors="replace"))
            if done is not None:
                self.on_progress(min(1.0, done / length))
        code = process.wait()
        drain.join(timeout=5)
        if self._cancelled:
            return "cancelled"
        if code != 0 or not self.work.exists() or self.work.stat().st_size == 0:
            return (b"".join(errors).decode(errors="replace").strip()[-300:]) or "ffmpeg could not write this file"
        return ""

    def _run(self) -> None:
        try:
            self.out.parent.mkdir(parents=True, exist_ok=True)
            # Real gameplay can overshoot a target bitrate: check the size and try again lower.
            for safety in (0.95, 0.85, 0.72) if self.preset == "discord" else (0.95,):
                problem = self._encode(safety)
                if problem or self.preset != "discord" or self.work.stat().st_size <= DISCORD_LIMIT:
                    break
            else:
                problem = "This clip is too long to fit in 10 MB. Trim it shorter."
            if not problem:
                self.work.replace(self.out)
                if self.preset == "exact":
                    self._carry_marks()
        except (OSError, ValueError) as e:
            problem = str(e)
        if problem:
            self.work.unlink(missing_ok=True)  # never leave half a file behind
            self.on_done(None, problem)
        else:
            self.on_progress(1.0)
            self.on_done(self.out, "")

    def _carry_marks(self) -> None:
        """A trimmed copy keeps the event marks that fall inside it."""
        try:
            meta = json.loads(self.src.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        meta["seconds"] = round(self.end_s - self.start_s, 2)
        meta["events"] = [{**e, "offset": round(e["offset"] - self.start_s, 2)} for e in meta.get("events", [])
                          if self.start_s <= e.get("offset", -1) <= self.end_s]
        self.out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
