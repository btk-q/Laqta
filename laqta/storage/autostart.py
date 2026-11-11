"""Start Laqta when the user signs in to Windows: one value in the user's own Run key.

The registry is the only record (no copy in config.toml that could disagree with it).
Windows keeps a second value, "StartupApproved", that Task Manager and Settings > Apps >
Startup use to switch an entry off without deleting it; we read it, so the tick box shows
what will really happen.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE = "Laqta"


def command() -> str:
    """The command line that starts this copy of Laqta without a console window, and without
    opening its main window: at sign-in it should just sit in the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    windowless = Path(sys.executable).with_name("pythonw.exe")
    return f'"{windowless if windowless.exists() else sys.executable}" -m laqta --tray'


def _read(key: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, VALUE)[0]
    except OSError:
        return None


def _delete(key: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE)
    except OSError:
        pass  # was not there


def state(run: str = RUN, approved: str = APPROVED) -> str:
    """"on", "off", or "blocked" (set up, but switched off in Windows' own startup list)."""
    if _read(run) is None:
        return "off"
    flag = _read(approved)
    # First byte: even = enabled (02, 06), odd = disabled (03, 07).
    return "blocked" if isinstance(flag, bytes) and flag and flag[0] % 2 else "on"


def set_enabled(on: bool, run: str = RUN, approved: str = APPROVED) -> None:
    """Ticking the box in Laqta is the user asking for it, so that also lifts a Windows block."""
    _delete(approved)
    if not on:
        return _delete(run)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run) as k:
        winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
