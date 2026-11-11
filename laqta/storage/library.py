"""The clip library's data side: what is on disk, and the things you can do to it.
No Qt here. Every video action is an ffmpeg stream copy: fast, and no quality loss."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import clips

log = logging.getLogger(__name__)

VIDEO = (".mkv", ".mp4")
EXPORTS = "Exports"
RECENT = datetime.timedelta(minutes=15)  # cleanup never touches something that may still be being written


@dataclass(frozen=True)
class Clip:
    path: Path
    game: str
    when: datetime.datetime
    size: int
    kind: str  # "clip" (saved around events) | "recording" (session or match)
    events: tuple = ()  # dicts {"type", "offset", ...} from the sidecar
    seconds: float | None = None  # known for clips; recordings are probed on demand
    tags: tuple = ()
    favorite: bool = False  # favourites are never removed by automatic cleanup

    @property
    def event_types(self) -> set[str]:
        return {e["type"] for e in self.events}


def scan(root: Path | None = None) -> list[Clip]:
    """Everything Laqta has saved, newest first. Layout: <root>/<Game>/<date>/<video>."""
    found = []
    for path in (root or clips.ROOT).glob("*/*/*"):
        if path.suffix.lower() not in VIDEO or {".trim", ".part"} & set(path.suffixes) or path.parents[1].name == EXPORTS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        meta: dict = {}
        try:
            meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        kind = meta.get("kind")
        if kind not in ("clip", "recording"):  # files from before the sidecar said so
            kind = "clip" if meta.get("seconds") is not None or path.name.startswith("Replay") else "recording"
        found.append(
            Clip(
                path=path,
                game=meta.get("game") or path.parents[1].name,
                when=datetime.datetime.fromtimestamp(stat.st_mtime),
                size=stat.st_size,
                kind=kind,
                events=tuple(meta.get("events") or ()),
                seconds=meta.get("seconds"),
                tags=tuple(str(tag) for tag in meta.get("tags") or ()),
                favorite=bool(meta.get("favorite")),
            )
        )
    return sorted(found, key=lambda c: c.when, reverse=True)


def matches(
    clip: Clip,
    game: str | None = None,
    event: str | None = None,
    days: int | None = None,
    kind: str | None = None,
    now: datetime.datetime | None = None,
    text: str = "",
    favorite: bool = False,
) -> bool:
    """Library filter. None, "" and False mean "any"."""
    if favorite and not clip.favorite:
        return False
    if text:
        haystack = " ".join([clip.game, clip.path.name, *clip.tags, *clip.event_types]).casefold()
        if not all(word in haystack for word in text.casefold().split()):
            return False
    if game and clip.game != game:
        return False
    if event and event not in clip.event_types:
        return False
    if kind and clip.kind != kind:
        return False
    if days is not None and clip.when < (now or datetime.datetime.now()) - datetime.timedelta(days=days):
        return False
    return True


def set_meta(path: Path, **fields) -> None:
    """Update a video's sidecar (favorite=..., tags=[...]). Creates it for old recordings that have none."""
    sidecar = path.with_suffix(".json")
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {"game": path.parents[1].name, "events": []}
    if "tags" in fields:
        seen: dict[str, None] = {}
        for tag in fields["tags"]:
            if tag.strip():
                seen.setdefault(tag.strip()[:40])
        fields["tags"] = list(seen)[:20]
    meta.update(fields)
    tmp = sidecar.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(sidecar)  # never leave half a file where the event marks were


def summary(clip: Clip) -> dict | None:
    """Kills / deaths / assists and the result, for a video whose plugin reported events."""
    counted = [e["type"] for e in clip.events]
    if not any(t in counted for t in ("kill", "death", "assist", "win", "lose")):
        return None
    result = "win" if "win" in counted else "lose" if "lose" in counted else None
    return {"kills": counted.count("kill"), "deaths": counted.count("death"), "assists": counted.count("assist"),
            "result": result, "seconds": clip.seconds}


# -- actions ---------------------------------------------------------------------------


def _run(*args: str, tool: str = "ffmpeg", timeout: float = 120) -> subprocess.CompletedProcess:
    exe = shutil.which(tool)
    if exe is None:
        raise OSError(f"{tool} was not found. Install ffmpeg to use this.")
    return subprocess.run([exe, *args], capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def duration(path: Path) -> float | None:
    try:
        out = _run("-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path), tool="ffprobe", timeout=20)
        return float(out.stdout.decode().strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def thumbnail(path: Path, cache: Path) -> Path | None:
    """A small JPEG for `path`, made once and reused until the video changes."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = hashlib.sha1(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:20]
    target = cache / f"{key}.jpg"
    if target.exists():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    try:
        # 2 s in: past the fade most games open with, still inside the shortest clip.
        for seek in (["-ss", "2"], []):
            _run("-y", "-v", "error", *seek, "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-2", str(target), timeout=30)
            if target.exists() and target.stat().st_size > 0:
                return target
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _check(done: subprocess.CompletedProcess, target: Path, what: str) -> Path:
    if done.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise OSError(done.stderr.decode(errors="replace").strip()[:300] or f"ffmpeg could not {what} this file")
    return target


def trim(path: Path, start: float, end: float) -> Path:
    """Save seconds [start, end] of `path` as a new file next to it. The original is kept."""
    if not 0 <= start < end:
        raise ValueError("The end must be after the start.")
    target = path.with_name(f"{path.stem} trim {int(start)}-{int(end)}{path.suffix}")
    done = _run("-y", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(path), "-map", "0", "-c", "copy", str(target))
    _check(done, target, "trim")
    try:  # carry over the event marks that fall inside the cut
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        meta["seconds"] = round(end - start, 2)
        meta["events"] = [
            {**e, "offset": round(e["offset"] - start, 2)} for e in meta.get("events", []) if start <= e.get("offset", -1) <= end
        ]
        target.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass
    return target


def export(path: Path, root: Path | None = None) -> Path:
    """An MP4 copy in <root>/Exports: phones, browsers and chat apps play MP4 but often not MKV."""
    folder = (root or clips.ROOT) / EXPORTS
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.parents[1].name} {path.stem}.mp4"
    # Track 1 is the full mix; phones and chat apps play only the first track anyway.
    done = _run("-y", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", str(target), timeout=600)
    return _check(done, target, "export")


# -- auto-cleanup -----------------------------------------------------------------------


def to_delete(clips: list[Clip], max_age_days: int, max_gb: float, now: datetime.datetime) -> list[Clip]:
    """Which files auto-cleanup would remove. 0 switches a rule off. Oldest go first."""
    settled = [c for c in clips if now - c.when >= RECENT and not c.favorite]  # favourites are kept forever
    doomed = [c for c in settled if max_age_days and now - c.when > datetime.timedelta(days=max_age_days)]
    if max_gb:
        total = sum(c.size for c in clips if c not in doomed)
        for clip in sorted((c for c in settled if c not in doomed), key=lambda c: c.when):
            if total <= max_gb * 1024**3:
                break
            doomed.append(clip)
            total -= clip.size
    return doomed


def cleanup(max_age_days: int, max_gb: float, root: Path | None = None) -> int:
    """Permanently delete what the user's limits say to. Returns how many videos went."""
    if not max_age_days and not max_gb:
        return 0
    removed = 0
    for clip in to_delete(scan(root), max_age_days, max_gb, datetime.datetime.now()):
        try:
            clip.path.unlink()
            clip.path.with_suffix(".json").unlink(missing_ok=True)
            removed += 1
        except OSError as e:
            log.warning("cleanup could not delete %s: %s", clip.path, e)
    if removed:
        log.info("auto-cleanup removed %d file(s)", removed)
    return removed
