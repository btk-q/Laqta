"""System tray: status, 'clip now', toasts, settings. No overlay, nothing drawn over the game."""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
import threading
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import __version__, i18n
from ..core.app import Core
from ..i18n import t
from ..storage import clips, config, update
from . import theme
from .hotkey import Hotkey
from .library import LibraryWindow
from .settings import SettingsWindow, set_game
from .toast import Toast

log = logging.getLogger(__name__)
ICON = Path(__file__).with_name("icon.png")


class Bridge(QObject):
    """Core thread -> Qt thread. Emitting from another thread is queued by Qt."""

    state = Signal(dict)
    toast = Signal(str)
    prompt = Signal(dict)
    update = Signal(object, bool)  # the newer Release or None; True if the user asked


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


def status_text(s: dict) -> str:
    if s["state"] != "active":
        key = f"status.{s['state']}"
    elif s["mode"] == "match":
        key = "status.match_rec" if s["recording"] else "status.match_wait"
    else:
        key = f"status.{s['mode']}"
    text = t(key, game=s["game"])
    return f"{text} — {s['error']}" if s.get("error") else text


class TrayApp:
    def __init__(self, app: QApplication, cfg: dict):
        self.app, self.cfg = app, cfg
        self.state = {"state": "idle", "game": None, "mode": None, "recording": False, "obs": False, "error": None}
        self.pending: dict | None = None  # unknown app the last toast asked about
        self.settings: SettingsWindow | None = None
        self.library: LibraryWindow | None = None
        self.hotkey: Hotkey | None = None
        self.replay_key: Hotkey | None = None
        self.popup: Toast | None = None  # made on first use, and only if the user turned it on

        base = QPixmap(str(ICON))
        self.icons = {"idle": QIcon(base), "busy": _with_dot(base, "#F5A524"), "rec": _with_dot(base, theme.REC)}
        app.setWindowIcon(self.icons["idle"])
        self.tray = QSystemTrayIcon(self.icons["idle"])
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.messageClicked.connect(self._toast_clicked)
        self.tray.activated.connect(lambda why: why == QSystemTrayIcon.ActivationReason.Trigger and self.open_library())
        self.found: update.Release | None = None  # a newer version, waiting for a moment when nothing is recording
        self.updating = False  # the new files are unpacked: quit so they can be swapped in
        self._hinted = False

        self.bridge = Bridge()
        self.bridge.state.connect(self._on_state)
        self.bridge.toast.connect(self.toast)
        self.bridge.prompt.connect(self._on_prompt)
        self.bridge.update.connect(self._on_update)
        self.core = Core(cfg, on_state=self.bridge.state.emit, notify=self.bridge.toast.emit, on_prompt=self.bridge.prompt.emit)

        self._apply_language()
        self._bind_hotkey()
        self.tray.show()

    # -- language, menu, hotkey ---------------------------------------------------

    def _apply_language(self) -> None:
        i18n.set_language(self.cfg["language"])
        self.app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl() else Qt.LayoutDirection.LeftToRight)
        self.menu.clear()
        self.status = self.menu.addAction("")
        self.status.setEnabled(False)
        self.menu.addSeparator()
        self.clip = self.menu.addAction(f"{t('menu.clip')}\t{self.cfg['hotkey']}")
        self.clip.triggered.connect(self.core.clip_now)
        self.replay = self.menu.addAction(f"{t('menu.replay', seconds=self.cfg['replay_seconds'])}\t{self.cfg['replay_hotkey']}")
        self.replay.triggered.connect(self.core.replay_now)
        self.menu.addAction(t("menu.library")).triggered.connect(self.open_library)
        self.menu.addAction(t("menu.folder")).triggered.connect(self._open_folder)
        self.menu.addAction(t("menu.settings")).triggered.connect(self.open_settings)
        self.menu.addAction(t("menu.update")).triggered.connect(lambda: self.check_update(asked=True))
        self.menu.addSeparator()
        self.menu.addAction(t("menu.quit")).triggered.connect(self.app.quit)
        self._on_state(self.state)

    def _bind_hotkey(self) -> None:
        self.hotkey = self._bind(self.hotkey, self.cfg["hotkey"], self.core.clip_now, 1)
        self.replay_key = self._bind(self.replay_key, self.cfg["replay_hotkey"], self.core.replay_now, 2)

    def _bind(self, old: Hotkey | None, combo: str, callback, id: int) -> Hotkey | None:
        if old is not None:
            self.app.removeNativeEventFilter(old)
            old.unregister()
        try:
            new = Hotkey(combo, callback, id)
        except ValueError:
            self.toast(t("toast.hotkey_bad", hotkey=combo))
            return None
        self.app.installNativeEventFilter(new)
        if not new.registered:
            self.toast(t("toast.hotkey_taken", hotkey=combo))
        return new

    # -- core -> UI -------------------------------------------------------------------

    def _on_state(self, s: dict) -> None:
        self.state = s
        text = status_text(s)
        self.status.setText(text)
        self.tray.setToolTip(f"Laqta — {text}"[:127])  # Windows truncates tray tooltips
        live = s["state"] == "active" and (s["recording"] or s["mode"] == "events")
        self.clip.setEnabled(live)
        self.replay.setEnabled(live)
        kind = "rec" if live else "idle" if s["state"] == "idle" else "busy"
        self.tray.setIcon(self.icons[kind])
        if self.library is not None:
            self.library.set_state(text, kind)
        self._offer_update()

    def toast(self, text: str) -> None:
        self.pending = None
        if self.cfg["toast_window"]:
            self.popup = self.popup or Toast()
            return self.popup.show_text(text)
        self.tray.showMessage("Laqta", text, self.icons["idle"], 4000)

    def _on_prompt(self, found: dict) -> None:
        """An unknown fullscreen app: ask once, by toast, without taking focus from it."""
        self.cfg["asked"] = sorted({*self.cfg["asked"], found["exe"].lower()})
        self._save()
        name = found["title"] or Path(found["exe"]).stem
        self.tray.showMessage("Laqta", t("toast.unknown_game", name=name), self.icons["idle"], 8000)
        self.pending = {"exe": found["exe"], "name": name}

    def _toast_clicked(self) -> None:
        if self.pending is None:
            return
        game, self.pending = self.pending, None
        set_game(self.cfg, game["exe"], name=game["name"])
        self._save()
        self.core.apply(self.cfg)
        self.toast(t("toast.game_added", name=game["name"]))

    # -- actions ------------------------------------------------------------------------

    def _save(self) -> None:
        try:
            config.save(self.cfg)
        except (OSError, ValueError) as e:
            log.error("could not save settings: %s", e)
            self.tray.showMessage("Laqta", str(e), QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _open_folder(self) -> None:
        clips.ROOT.mkdir(parents=True, exist_ok=True)
        os.startfile(clips.ROOT)

    def open_library(self) -> None:
        if self.library is not None:
            self.library.reload()
            self.library.showNormal()
            self.library.raise_()
            return self.library.activateWindow()
        self.library = window = LibraryWindow(webhook=self.cfg["discord_webhook"], on_settings=self.open_settings)
        window.finished.connect(lambda _: self._library_closed(window))
        self._on_state(self.state)  # so the new window shows what is happening right now
        window.show()

    def _library_closed(self, window: LibraryWindow) -> None:
        self.library = None
        window.deleteLater()
        if not self._hinted:  # once per run: closing the window does not stop Laqta
            self._hinted = True
            self.toast(t("library.tray_hint"))

    # -- updates --------------------------------------------------------------------------

    def check_update(self, asked: bool = False) -> None:
        """Ask GitHub in the background; the answer comes back through the bridge."""
        threading.Thread(target=lambda: self.bridge.update.emit(update.check(), asked), name="laqta-update-check", daemon=True).start()

    def _on_update(self, release, asked: bool) -> None:
        if release is None:
            if asked:
                self.toast(t("update.none", version=__version__))
            return
        self.found = release
        self._offer_update()

    def _offer_update(self) -> None:
        """Show the update window, but never in the middle of a game."""
        if self.found is None or self.state["state"] != "idle":
            return
        from .update import UpdateDialog  # imported here: most runs never need it

        release, self.found = self.found, None
        dialog = UpdateDialog(release)
        accepted = dialog.exec()
        if accepted:
            self.updating = True
        if accepted or not dialog.proceed:  # updated, or chose "Quit Laqta"
            self.app.quit()

    def open_settings(self) -> None:
        if self.settings is not None:
            self.settings.raise_()
            return self.settings.activateWindow()
        keys = {self.cfg[name]: bool(key and key.registered) for name, key in (('hotkey', self.hotkey), ('replay_hotkey', self.replay_key))}
        self.settings = window = SettingsWindow(self.cfg, hotkeys=keys)
        window.finished.connect(lambda result: self._settings_closed(window, result))
        window.show()

    def _settings_closed(self, window: SettingsWindow, result: int) -> None:
        self.settings = None
        window.deleteLater()
        if not result:
            return theme.apply(self.app, self.cfg["accent"])  # undo any colour preview
        old, self.cfg = self.cfg, window.cfg
        self._save()
        clips.set_root(self.cfg["save_folder"])  # so "Open clips folder" and the library follow at once
        self.core.apply(self.cfg)
        if self.library is not None:
            self.library.set_webhook(self.cfg["discord_webhook"])
        keys = ("hotkey", "replay_hotkey", "replay_seconds")
        if self.cfg["language"] != old["language"] or any(self.cfg[k] != old[k] for k in keys):
            self._apply_language()
        if any(self.cfg[k] != old[k] for k in keys[:2]):
            self._bind_hotkey()
        theme.apply(self.app, self.cfg["accent"])


MUTEX = "Laqta.SingleInstance"
SHOW = "Laqta.ShowWindow"  # a named Windows event: a second launch sets it, the running one opens its window


def run(cfg: dict, show: bool = True) -> int:
    # One instance only: two would fight over OBS and the hotkey.
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, MUTEX)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        handle = kernel32.OpenEventW(0x0002, False, SHOW)  # EVENT_MODIFY_STATE
        if handle and show:
            kernel32.SetEvent(handle)  # double-clicking the exe again brings the window up
        return 0
    wake = kernel32.CreateEventW(None, False, False, SHOW)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Laqta")
    app.setFont(QFont("Segoe UI", 10))  # has Arabic glyphs; Windows substitutes Segoe UI Arabic shapes
    theme.apply(app, cfg["accent"])
    tray = TrayApp(app, cfg)

    # Ctrl+C in a terminal: Qt's loop never returns to Python on its own, so the handler
    # would not run. A no-op timer gives the interpreter a turn twice a second.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: kernel32.WaitForSingleObject(wake, 0) == 0 and tray.open_library())
    heartbeat.start(500)
    if show:
        tray.open_library()
    if cfg["update_check"]:
        tray.check_update()

    tray.core.start()
    code = app.exec()
    tray.core.quit()
    tray.core.join(timeout=10)
    for key in (tray.hotkey, tray.replay_key):
        if key is not None:
            key.unregister()
    tray.tray.hide()
    del mutex
    return code
