"""System tray: status, 'clip now', toasts. No window, no overlay."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..core.app import Core
from ..storage import clips, config
from .hotkey import Hotkey

ICON = Path(__file__).with_name("icon.png")
STATUS = {
    "idle": "Waiting for a game",
    "wait_obs": "{game}: waiting for OBS",
    "exit_debounce": "{game}: closing",
}
ACTIVE = {"session": "{game}: recording", "events": "{game}: ready to clip"}


class Bridge(QObject):
    """Core thread -> Qt thread. Emitting from another thread is queued by Qt."""

    state = Signal(dict)
    toast = Signal(str)


def _with_dot(base: QPixmap, color: str) -> QIcon:
    pixmap = QPixmap(base)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    d = pixmap.width() * 0.45
    painter.drawEllipse(pixmap.width() - d, pixmap.height() - d, d, d)
    painter.end()
    return QIcon(pixmap)


def run(cfg: dict) -> int:
    # One instance only: two would fight over OBS and the hotkey.
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Laqta.SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return 0

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Laqta")

    base = QPixmap(str(ICON))
    icons = {"idle": QIcon(base), "busy": _with_dot(base, "#f5a524"), "rec": _with_dot(base, "#ff2d2d")}
    tray = QSystemTrayIcon(icons["idle"])
    bridge = Bridge()
    core = Core(cfg, on_state=bridge.state.emit, notify=bridge.toast.emit)

    menu = QMenu()
    status = menu.addAction("Starting…")
    status.setEnabled(False)
    menu.addSeparator()
    clip = menu.addAction(f"Clip now\t{cfg['hotkey']}")
    clip.triggered.connect(core.clip_now)
    menu.addAction("Open clips folder").triggered.connect(
        lambda: (clips.ROOT.mkdir(parents=True, exist_ok=True), os.startfile(clips.ROOT))
    )
    menu.addAction("Edit settings").triggered.connect(lambda: os.startfile(config.PATH))
    menu.addSeparator()
    menu.addAction("Quit").triggered.connect(app.quit)
    tray.setContextMenu(menu)

    def on_state(s: dict) -> None:
        template = ACTIVE[s["mode"]] if s["state"] == "active" else STATUS[s["state"]]
        text = template.format(game=s["game"])
        if s["error"]:
            text += f" — {s['error']}"
        status.setText(text)
        tray.setToolTip(f"Laqta — {text}"[:127])  # Windows truncates tray tooltips
        clip.setEnabled(s["state"] == "active" and s["mode"] == "events")
        tray.setIcon(icons["rec" if s["state"] == "active" else "idle" if s["state"] == "idle" else "busy"])

    bridge.state.connect(on_state)
    bridge.toast.connect(lambda text: tray.showMessage("Laqta", text, icons["idle"], 4000))

    hotkey = Hotkey(cfg["hotkey"], core.clip_now)
    app.installNativeEventFilter(hotkey)
    tray.show()
    if not hotkey.registered:
        tray.showMessage("Laqta", f"Hotkey {cfg['hotkey']} is taken by another app. Change it in settings.")

    core.start()
    code = app.exec()
    core.quit()
    core.join(timeout=10)
    hotkey.unregister()
    tray.hide()
    del mutex
    return code
