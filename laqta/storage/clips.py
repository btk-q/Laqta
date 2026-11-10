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
ROOT = Path.home() / "Videos" / "Laqta"


def game_dir(game: dict, root: Path = ROOT) -> Path:
    name = re.sub(r'[<>:"/\\|?*]', "_", game.get("name") or game["exe"]).strip(". ") or "Unknown"
    return root / name / datetime.date.today().isoformat()


def finish_clip(path: Path, game: dict, events: list[GameEvent], keep: float, pre: float) -> Path:
    """OBS saved the whole replay buffer; keep only the last `keep` seconds and describe it.
    `pre` is how long before the first event the kept part starts (for event offsets)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        tmp = path.with_name(path.stem + ".trim" + path.suffix)
        # Stream copy: instant and lossless, but the cut lands on the keyframe before the
        # mark, so a clip can start a few seconds early.
        done = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-sseof", f"-{keep:.2f}", "-i", str(path), "-c", "copy", str(tmp)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if done.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, path)
        else:
            log.warning("trim failed, keeping full replay: %s", done.stderr.decode(errors="replace")[:300])
            tmp.unlink(missing_ok=True)
    first = events[0].t if events else 0.0
    write_sidecar(path, game, events, start=first - pre, seconds=keep)
    return path


def write_sidecar(path: Path, game: dict, events: list[GameEvent], start: float, seconds: float | None = None) -> None:
    """<video>.json next to the video: what it is and where in it each event happened.
    `start` is the clock value at the video's first frame."""
    sidecar = {
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
