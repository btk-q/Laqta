"""Where recordings go, and what happens to a clip once OBS has written it."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..core.events import GameEvent

log = logging.getLogger(__name__)

# ponytail: assumes Videos is not redirected; use SHGetKnownFolderPath if someone's is.
DEFAULT_ROOT = Path.home() / "Videos" / "Laqta"
ROOT = DEFAULT_ROOT  # where recordings go now; read it as clips.ROOT, it changes with settings


def free_bytes(folder: Path | None = None) -> int | None:
    """Free space on the drive recordings go to. The folder may not exist yet: ask its nearest parent."""
    folder = folder or ROOT
    for place in (folder, *folder.parents):
        if place.exists():
            try:
                return shutil.disk_usage(place).free
            except OSError:
                return None
    return None


def usable_folder(folder: Path) -> bool:
    """Can recordings be written there? Checked by actually writing, the only test that is right
    for network drives, read-only media and protected folders alike."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".laqta-write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def set_root(folder: str | None) -> Path:
    """Use the folder from settings ("" or None = the default under Videos)."""
    global ROOT
    ROOT = Path(folder) if folder else DEFAULT_ROOT
    return ROOT


def game_dir(game: dict, root: Path | None = None) -> Path:
    name = re.sub(r'[<>:"/\\|?*]', "_", game.get("name") or game["exe"]).strip(". ") or "Unknown"
    return (root or ROOT) / name / datetime.date.today().isoformat()


def finish_clip(path: Path, game: dict, events: list[GameEvent], keep: float, pre: float) -> Path:
    """OBS saved the whole replay buffer; keep only the last `keep` seconds and describe it.
    `pre` is how long before the first event the kept part starts (for event offsets)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        tmp = path.with_name(path.stem + ".trim" + path.suffix)
        # Stream copy: instant and lossless, but the cut lands on the keyframe before the
        # mark, so a clip can start a few seconds early.
        done = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-sseof", f"-{keep:.2f}", "-i", str(path), "-map", "0", "-c", "copy", str(tmp)],  # -map 0: keep every audio track
            capture_output=True,
            timeout=60,  # raises TimeoutExpired; the caller keeps the untrimmed replay
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if done.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, path)
        else:
            log.warning("trim failed, keeping full replay: %s", done.stderr.decode(errors="replace")[:300])
            tmp.unlink(missing_ok=True)
    first = events[0].t if events else 0.0
    write_sidecar(path, game, events, start=first - pre, seconds=keep, kind="clip")
    return path


def write_sidecar(
    path: Path, game: dict, events: list[GameEvent], start: float, seconds: float | None = None, kind: str = "recording"
) -> None:
    """<video>.json next to the video: what it is and where in it each event happened.
    `start` is the clock value at the video's first frame. Tags and favourites the user adds
    later live in the same file (see storage.library.set_meta)."""
    sidecar = {
        "kind": kind,  # "clip" (saved around events) | "recording" (a session or a match)
        "game": game.get("name") or game["exe"],
        "exe": game["exe"],
        "mode": game.get("mode"),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "seconds": None if seconds is None else round(seconds, 2),
        "events": [
            {"type": e.type, "source": e.source, "confidence": e.confidence, "offset": round(e.t - start, 2), **({"data": e.data} if e.data else {})}
            for e in events
        ],
    }
    path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
