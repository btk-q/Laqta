"""Updates from the project's GitHub releases. No Qt.

What goes over the network: one anonymous HTTPS request to GitHub asking for the latest
release, and, if the user accepts it, the download of that release's zip. Nothing about the
PC or the user is sent. Only this project's own release address is ever contacted, and a
download is used only if its SHA-256 matches the one GitHub lists for it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import __version__

REPO = "btk-q/Laqta"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
ASSET = re.compile(r"Laqta-[\w.]+-win64\.zip")
HEADERS = {"User-Agent": "Laqta", "Accept": "application/vnd.github+json"}


@dataclass(frozen=True)
class Release:
    version: str  # "0.3.0"
    notes: str  # the patch notes, Markdown
    url: str  # the zip
    size: int
    sha256: str


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def from_api(data: dict) -> Release | None:
    """The release GitHub described, or None if it has nothing we can safely install."""
    if data.get("draft") or data.get("prerelease"):
        return None
    for asset in data.get("assets") or ():
        name, url = str(asset.get("name", "")), str(asset.get("browser_download_url", ""))
        digest = str(asset.get("digest") or "")  # "sha256:<hex>", computed by GitHub at upload
        if not digest:  # older API answers: our release script also writes it in the notes
            found = re.search(r"SHA256:\s*([0-9a-fA-F]{64})", str(data.get("body", "")))
            digest = f"sha256:{found.group(1)}" if found else ""
        if ASSET.fullmatch(name) and url.startswith(DOWNLOADS) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            return Release(str(data.get("tag_name", "")).lstrip("v"), str(data.get("body") or "").strip(), url,
                           int(asset.get("size") or 0), digest[7:].lower())
    return None


def check(opener: Callable = urllib.request.urlopen, current: str = __version__) -> Release | None:
    """The newer release, if there is one. None when up to date, offline, or anything is odd:
    a failed check must never stop Laqta from starting."""
    try:
        with opener(urllib.request.Request(API, headers=HEADERS), timeout=8) as response:
            release = from_api(json.loads(response.read()))
    except (OSError, ValueError):  # URLError is an OSError
        return None
    return release if release and newer(release.version, current) else None


def download(release: Release, folder: Path, progress: Callable[[float], None] = lambda fraction: None,
             opener: Callable = urllib.request.urlopen, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Fetch the zip and check it is byte-for-byte what GitHub listed. Raises OSError otherwise."""
    if not release.url.startswith(DOWNLOADS):
        raise OSError("the update does not come from Laqta's own releases")
    folder.mkdir(parents=True, exist_ok=True)
    target, digest, done = folder / f"Laqta-{release.version}.zip", hashlib.sha256(), 0
    try:
        with opener(urllib.request.Request(release.url, headers={"User-Agent": "Laqta"}), timeout=30) as response, open(target, "wb") as out:
            while chunk := response.read(1 << 18):
                if cancelled():
                    raise OSError("cancelled")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(min(1.0, done / release.size) if release.size else 0.0)
        if digest.hexdigest() != release.sha256:
            raise OSError("the downloaded update does not match the published file, so it was not installed")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def app_dir() -> Path | None:
    """The folder of the packaged app, or None when running from source (updated with git instead)."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else None


def unpack(archive: Path, folder: Path) -> Path:
    """Extract the zip; returns the folder inside it that holds Laqta.exe."""
    with zipfile.ZipFile(archive) as z:
        z.extractall(folder)  # zipfile drops absolute paths and ".." itself
    found = next((p.parent for p in folder.rglob("Laqta.exe") if (p.parent / "_internal").is_dir()), None)
    if found is None:
        raise OSError("the update file does not contain Laqta")
    return found


def swap_script(new: Path, app: Path, pid: int, restart: bool = True, work: Path | None = None, archive: Path | None = None) -> str:
    """A batch file that waits for this Laqta to close, replaces its files and starts it again.
    It removes only the app's own `_internal` folder: whatever else sits beside Laqta.exe
    (the user may have unpacked it straight onto the Desktop) is never deleted."""
    lines = [
        "@echo off",
        "chcp 65001 >nul",  # the file is UTF-8: folder names are not always ASCII
        ":wait",
        # ping as a one-second pause: `timeout` refuses to run without a console
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)',
        f'if exist "{app}\\_internal\\base_library.zip" rmdir /s /q "{app}\\_internal"',
        f'robocopy "{new}" "{app}" /E /R:5 /W:1 /NFL /NDL /NJH /NJS >nul',
        f'start "" "{app}\\Laqta.exe"' if restart else "rem no restart",
        f'rmdir /s /q "{work or new.parent}"',
        f'del /q "{archive}"' if archive else "rem nothing downloaded to remove",
        '(goto) 2>nul & del "%~f0"',
    ]
    return "\r\n".join(lines) + "\r\n"


def writable(folder: Path) -> bool:
    try:
        probe = folder / ".laqta-update-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def install(archive: Path, app: Path, restart: bool = True) -> None:
    """Unpack next to the temp files and start the swap. The caller must now quit Laqta."""
    if not writable(app):
        raise OSError(f"Laqta cannot write to its own folder ({app}). Move it somewhere like Documents, or update by hand.")
    work = Path(tempfile.mkdtemp(prefix="laqta-update-"))
    new = unpack(archive, work / "new")
    script = work.parent / f"{work.name}.cmd"
    script.write_text(swap_script(new, app, os.getpid(), restart, work, archive), encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True, cwd=str(work.parent))
