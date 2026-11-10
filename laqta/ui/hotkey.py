"""Global hotkey via RegisterHotKey: Windows posts a message to our own thread.
No keyboard hook is installed, so nothing touches other processes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse(combo: str) -> tuple[int, int]:
    """'Ctrl+Alt+F9' -> (modifier flags, virtual-key code). Raises ValueError if unusable."""
    *mods, key = [part.strip().lower() for part in combo.split("+")]
    flags = 0
    for mod in mods:
        if mod not in MODS:
            raise ValueError(f"unknown modifier {mod!r} in hotkey {combo!r}")
        flags |= MODS[mod]
    if key[:1] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return flags, 0x70 + int(key[1:]) - 1
    if len(key) == 1 and key.isalnum():
        return flags, ord(key.upper())
    raise ValueError(f"unknown key {key!r} in hotkey {combo!r} (use a letter, digit or F1-F24)")


class Hotkey(QAbstractNativeEventFilter):
    """Install with app.installNativeEventFilter(hotkey). Must be created on the Qt main thread."""

    def __init__(self, combo: str, callback: Callable[[], None], id: int = 1):
        super().__init__()
        self.callback, self.ID = callback, id  # each hotkey in the app needs its own id
        flags, vk = parse(combo)
        self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, self.ID, flags | MOD_NOREPEAT, vk))

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.ID:
                self.callback()
                return True, 0
        return False, 0

    def unregister(self) -> None:
        ctypes.windll.user32.UnregisterHotKey(None, self.ID)
