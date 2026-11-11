"""A small "Clip saved" note in the corner of the screen, for when Windows hides tray
notifications during games (Focus Assist does that by default).

An ordinary desktop window: always on top, never takes focus, lets clicks through to
whatever is under it. Nothing is drawn inside the game, so it cannot show over a game in
exclusive fullscreen; borderless and windowed games are fine.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel

FLAGS = (
    Qt.WindowType.Tool  # no taskbar button
    | Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.WindowDoesNotAcceptFocus  # never steals the keyboard from the game
    | Qt.WindowType.WindowTransparentForInput  # clicks go to what is underneath
)
MARGIN = 24


class Toast(QLabel):
    def __init__(self):
        super().__init__(None, FLAGS)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWordWrap(True)
        self.setFixedWidth(340)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, ms: int = 3500) -> None:
        self.setText(text)
        self.adjustSize()
        # ponytail: always the primary screen; follow the game's monitor if that is asked for.
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - MARGIN, area.bottom() - self.height() - MARGIN)
        self.show()
        self._timer.start(ms)
