"""A picture for each game: the icon inside the game's own program file.

Laqta knows games by program name only, so the files are found by looking through the
launchers' install folders (never by asking a running game), once per run, on a background
thread. Icons are kept as small PNGs in the settings folder, so later starts show them at once.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QFileInfo, QObject, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileIconProvider

from ..game_detector import library_scan
from ..storage import config

log = logging.getLogger(__name__)
CACHE = config.DIR / "icons"
SIZE = 64
_memory: dict[str, QPixmap | None] = {}


def _file(exe: str) -> Path:
    return CACHE / f"{exe.lower()}.png"


def get(exe: str) -> QPixmap | None:
    """The saved icon for a program name, or None if none has been found (yet)."""
    key = exe.lower()
    if key not in _memory:
        pixmap = QPixmap(str(_file(key))) if _file(key).exists() else QPixmap()
        _memory[key] = None if pixmap.isNull() else pixmap
    return _memory[key]


def blank() -> QPixmap:
    """An empty picture of the same size, for a game whose icon has not been found."""
    pixmap = QPixmap(SIZE, SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def extract(exe: str, path: Path) -> QPixmap | None:
    """Read the icon out of a program file and keep it. GUI thread only."""
    pixmap = QFileIconProvider().icon(QFileInfo(str(path))).pixmap(QSize(SIZE, SIZE))
    if pixmap.isNull():
        return None
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(_file(exe)), "PNG")
    except OSError:
        pass  # shown this run anyway
    _memory[exe.lower()] = pixmap
    return pixmap


class Finder(QObject):
    """Looks for the program files of games that have no saved icon. `done` fires on the GUI
    thread once new icons are ready, so views can repaint."""

    located = Signal(dict)  # {program name (lower case): full path}, from the worker thread
    done = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.located.connect(self._extract)
        self._running = False

    def find(self, exes: list[str]) -> None:
        wanted = {e.lower() for e in exes if get(e) is None}
        if not wanted or self._running:
            return
        self._running = True

        def work() -> None:
            try:
                found = {name: path for name, path in library_scan.program_files().items() if name in wanted}
            except Exception:
                log.exception("looking for game program files failed")
                found = {}
            try:
                self.located.emit({name: str(path) for name, path in found.items()})
            except RuntimeError:
                pass  # the window closed while we looked

        threading.Thread(target=work, name="laqta-icons", daemon=True).start()

    def _extract(self, found: dict) -> None:
        self._running = False
        made = [extract(name, Path(path)) for name, path in found.items()]
        if any(made):
            self.done.emit()
