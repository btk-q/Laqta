"""Finding and installing the outside programs Laqta needs (ffmpeg, OBS Studio).

Finding: a program installed a minute ago is on the PATH Windows will give the *next*
program, not on ours, so `which` also asks the registry for the current PATH and looks where
winget puts things. Installing: winget, Microsoft's own package manager, in its own window,
only when the user presses the button.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import winreg
from pathlib import Path

PACKAGES = {"obs": "OBSProject.OBSStudio", "ffmpeg": "Gyan.FFmpeg"}
PAGES = {"obs": "https://obsproject.com/download", "ffmpeg": "https://www.gyan.dev/ffmpeg/builds/"}
_found: dict[str, str] = {}


def _registry_path() -> str:
    """PATH as Windows has it now (user and machine), which may be newer than this process's copy."""
    parts = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    return os.pathsep.join(parts)


def _winget_places(name: str) -> list[Path]:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    return [base / "Links" / f"{name}.exe", *sorted(base.glob(f"Packages/Gyan.FFmpeg*/*/bin/{name}.exe"), reverse=True)]


def which(name: str) -> str | None:
    """Full path of `name` (ffmpeg, ffprobe), or None. A hit is remembered; a miss is looked for again."""
    if name in _found and Path(_found[name]).exists():
        return _found[name]
    found = shutil.which(name) or shutil.which(name, path=_registry_path())
    if not found:
        found = next((str(p) for p in _winget_places(name) if p.exists()), None)
    if found:
        _found[name] = found
    return found


def winget() -> str | None:
    return shutil.which("winget")


def install_command(what: str) -> list[str]:
    """The winget command that installs OBS or ffmpeg. The two --accept flags agree to winget's
    source terms and the package's licence, which the button's text tells the user."""
    return ["winget", "install", "--id", PACKAGES[what], "-e", "--accept-source-agreements", "--accept-package-agreements"]


def install(what: str, popen=subprocess.Popen):
    """Start the install in its own console window, so winget's progress and any Windows
    permission prompt are the user's to see and answer. Returns the process, or None if this
    PC has no winget (the caller opens the download page instead)."""
    if winget() is None:
        return None
    return popen(install_command(what), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
