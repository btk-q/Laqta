"""Spot a known game among running processes, and notice unknown fullscreen apps.

Uses a system process snapshot (names only) and window geometry. No process is ever
opened, so there is nothing here for an anti-cheat to object to.
"""

from __future__ import annotations

import ctypes
import json
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable

GAMES_JSON = Path(__file__).with_name("games.json")

# Fullscreen things that are not games; we never ask about these.
NOT_GAMES = {
    "explorer.exe", "searchhost.exe", "shellexperiencehost.exe", "applicationframehost.exe", "lockapp.exe",
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe",
    "obs64.exe", "vlc.exe", "mpv.exe", "mpc-hc64.exe", "potplayermini64.exe", "wmplayer.exe",
    "powerpnt.exe", "code.exe", "claude.exe", "python.exe", "pythonw.exe", "laqta.exe",
}  # fmt: skip


def load_games(user_games: Iterable[dict] = (), default_mode: str = "session") -> dict[str, dict]:
    """Built-in DB overlaid with the user's entries, keyed by lower-case exe name.
    A user entry with "remove": true hides a built-in game."""
    games: dict[str, dict] = {}
    for entry in [*json.loads(GAMES_JSON.read_text(encoding="utf-8")), *user_games]:
        key = entry["exe"].lower()
        if entry.get("remove"):
            games.pop(key, None)
        else:
            games[key] = {"mode": default_mode, **games.get(key, {}), **entry}
    return games


# -- Windows: process snapshot and foreground window -------------------------------


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]  # fmt: skip


def processes() -> dict[int, str]:
    """{pid: exe name} from one Toolhelp snapshot. Opens no process handles."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, wintypes.HANDLE(-1).value):
        return {}
    found: dict[int, str] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            found[entry.th32ProcessID] = entry.szExeFile
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def running_exes() -> set[str]:
    return {name.lower() for name in processes().values()}


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def foreground_fullscreen() -> tuple[int, str] | None:
    """(pid, window title) if the foreground window covers its whole monitor, else None."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    window = user32.GetForegroundWindow()
    if not window:
        return None
    rect, info = wintypes.RECT(), _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    if not user32.GetWindowRect(window, ctypes.byref(rect)):
        return None
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(window, 2), ctypes.byref(info)):  # 2 = nearest monitor
        return None
    m = info.rcMonitor
    if (rect.left, rect.top, rect.right, rect.bottom) != (m.left, m.top, m.right, m.bottom):
        return None  # maximised windows keep the taskbar visible, so they do not match
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(window, title, 256)
    return pid.value, title.value


def game_window(exe: str, running: dict[int, str] | None = None) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the largest visible window owned by `exe`, or None.
    Asks the window manager only; the game process itself is never opened."""
    running = processes() if running is None else running
    pids = {pid for pid, name in running.items() if name.lower() == exe.lower()}
    if not pids:
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    best: list[tuple[int, tuple[int, int, int, int]]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(window, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(window):
            rect = wintypes.RECT()
            if user32.GetWindowRect(window, ctypes.byref(rect)):
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if area > 0:
                    best.append((area, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(visit, 0)
    return max(best)[1] if best else None


# -- detector ------------------------------------------------------------------------


class Detector:
    def __init__(
        self,
        games: dict[str, dict],
        processes: Callable[[], dict[int, str]] = processes,
        fullscreen: Callable[[], tuple[int, str] | None] = foreground_fullscreen,
        asked: Iterable[str] = (),
    ):
        self.games, self.processes, self.fullscreen = games, processes, fullscreen
        self.current: dict | None = None
        self.asked = {exe.lower() for exe in asked}  # never ask twice about the same app

    def poll(self) -> tuple[str, dict] | None:
        """Call every couple of seconds. Returns one of
        ("game_started", game), ("game_exited", game), ("unknown_fullscreen", {"exe", "title"}) or None."""
        running = self.processes()
        names = {name.lower() for name in running.values()}
        if self.current is not None:
            if self.current["exe"].lower() in names:
                return None
            game, self.current = self.current, None
            return "game_exited", game
        for exe, game in self.games.items():
            if exe in names:
                self.current = game
                return "game_started", game
        front = self.fullscreen()
        if front is not None:
            exe = running.get(front[0], "")
            key = exe.lower()
            if key and key not in self.asked and key not in NOT_GAMES and key not in self.games:
                self.asked.add(key)
                return "unknown_fullscreen", {"exe": exe, "title": front[1]}
        return None
