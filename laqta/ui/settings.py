"""Settings window. Works on a copy of the config; nothing changes until "Save changes"."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)  # fmt: skip

from ..core.recorder import BOUNDARIES, MODES
from ..game_detector import library_scan
from ..game_detector.detector import GAMES_JSON, NOT_GAMES, load_games, processes
from ..i18n import LANGUAGES, t
from ..obs_controller.capture import KINDS
from ..plugins import PLUGINS
from ..storage import clips, config

# -- config edits (no Qt: unit-tested) ------------------------------------------------


def builtin_exes() -> set[str]:
    import json

    return {g["exe"].lower() for g in json.loads(GAMES_JSON.read_text(encoding="utf-8"))}


def set_game(cfg: dict, exe: str, **fields) -> None:
    """Create or update the user's entry for `exe`. A field set to None is removed."""
    entry = next((g for g in cfg["games"] if g["exe"].lower() == exe.lower()), None)
    if entry is None:
        entry = {"exe": exe}
        cfg["games"].append(entry)
    entry.pop("remove", None)
    for key, value in fields.items():
        if value is None or value == []:
            entry.pop(key, None)
        else:
            entry[key] = value


def remove_game(cfg: dict, exe: str, builtin: set[str]) -> None:
    """Stop recording `exe`: drop a user game, or hide a built-in one."""
    cfg["games"] = [g for g in cfg["games"] if g["exe"].lower() != exe.lower()]
    if exe.lower() in builtin:
        cfg["games"].append({"exe": exe, "remove": True})


# -- widgets -----------------------------------------------------------------------------


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _combo(options: list[tuple[str, object]], current) -> QComboBox:
    combo = QComboBox()
    for text, value in options:
        combo.addItem(text, value)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


def _spin(value: int, low: int, high: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(int(value))
    return spin


class PickRunningApp(QDialog):
    def __init__(self, parent: QWidget, known: set[str]):
        super().__init__(parent)
        self.setWindowTitle(t("games.pick_title"))
        self.resize(380, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(_label(t("games.pick_hint"), "hint"))
        self.filter = QLineEdit()
        self.filter.setClearButtonEnabled(True)
        self.list = QListWidget()
        names = sorted({n for n in processes().values() if n.lower() not in known | NOT_GAMES and n.lower().endswith(".exe")}, key=str.lower)
        self.list.addItems(names)
        self.filter.textChanged.connect(self._apply_filter)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        add, cancel = QPushButton(t("button.add")), QPushButton(t("button.cancel"))
        add.setObjectName("primary")
        add.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(add)
        layout.addWidget(self.filter)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)

    def _apply_filter(self, text: str) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setHidden(text.lower() not in self.list.item(i).text().lower())

    def chosen(self) -> str | None:
        item = self.list.currentItem()
        return item.text() if item and not item.isHidden() else None


class SettingsWindow(QDialog):
    def __init__(self, cfg: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        self.builtin = builtin_exes()
        self.setWindowTitle(t("settings.title"))
        self.resize(820, 560)

        self.rail = QListWidget()
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(170)
        self.pages = QStackedWidget()
        for key, page in (("nav.general", self._general()), ("nav.games", self._games()), ("nav.recording", self._recording())):
            self.rail.addItem(t(key))
            self.pages.addWidget(page)
        self.rail.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.rail.setCurrentRow(0)

        save, cancel = QPushButton(t("button.save")), QPushButton(t("button.cancel"))
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(24, 12, 24, 16)
        buttons.setSpacing(10)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.pages, 1)
        right.addLayout(buttons)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.rail)
        root.addLayout(right, 1)

    def _page(self, title_key: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 8)
        layout.setSpacing(12)
        layout.addWidget(_label(t(title_key), "heading"))
        return page, layout

    # -- General ---------------------------------------------------------------

    def _general(self) -> QWidget:
        page, layout = self._page("nav.general")
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(20)
        self.language = _combo([(name, code) for code, name in LANGUAGES.items()], self.cfg["language"])
        self.hotkey = QKeySequenceEdit(QKeySequence(self.cfg["hotkey"]))
        self.hotkey.setMaximumSequenceLength(1)
        self.default_mode = _combo([(t(f"mode.{m}"), m) for m in MODES], self.cfg["default_mode"])
        self.default_mode_help = _label("", "hint")
        self.default_mode.currentIndexChanged.connect(
            lambda: self.default_mode_help.setText(t(f"mode.{self.default_mode.currentData()}.help"))
        )
        self.default_mode_help.setText(t(f"mode.{self.cfg['default_mode']}.help"))
        form.addRow(t("general.language"), self.language)
        form.addRow(t("general.hotkey"), self.hotkey)
        form.addRow("", _label(t("general.hotkey_hint"), "hint"))
        form.addRow(t("general.default_mode"), self.default_mode)
        form.addRow("", self.default_mode_help)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    # -- Recording -------------------------------------------------------------

    def _recording(self) -> QWidget:
        page, layout = self._page("nav.recording")
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(20)
        self.quality = _combo([(t(f"quality.{q}"), q) for q in config.QUALITIES], self.cfg["quality"])
        self.fps = _combo([(str(f), f) for f in (30, 60, 120)], self.cfg["fps"])
        self.pre = _spin(self.cfg["pre_seconds"], 0, 120)
        self.post = _spin(self.cfg["post_seconds"], 0, 60)
        self.max_clip = _spin(self.cfg["max_clip_seconds"], 10, 300)
        self.debounce = _spin(self.cfg["exit_debounce_seconds"], 0, 120)
        form.addRow(t("rec.quality"), self.quality)
        form.addRow(t("rec.fps"), self.fps)
        form.addRow(t("rec.pre"), self.pre)
        form.addRow(t("rec.post"), self.post)
        form.addRow(t("rec.max_clip"), self.max_clip)
        form.addRow(t("rec.debounce"), self.debounce)
        layout.addLayout(form)
        layout.addWidget(_label(t("rec.folder", path=str(clips.ROOT)), "hint"))
        layout.addStretch(1)
        return page

    # -- Games -----------------------------------------------------------------

    def _games(self) -> QWidget:
        page, layout = self._page("nav.games")
        self.game_list = QListWidget()
        self.game_list.setFixedWidth(230)
        self.game_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.game_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.game_list.currentItemChanged.connect(lambda *_: self._show_game())
        scan, add = QPushButton(t("games.scan")), QPushButton(t("games.add"))
        scan.clicked.connect(self._scan)
        add.clicked.connect(self._add_running)
        self.scan_result = _label("", "hint")
        left = QVBoxLayout()
        left.addWidget(self.game_list, 1)
        left.addWidget(scan)
        left.addWidget(add)
        left.addWidget(self.scan_result)

        self.detail = QVBoxLayout()
        self.detail.setContentsMargins(20, 0, 0, 0)
        self.detail.setSpacing(10)
        holder = QWidget()
        holder.setObjectName("page")
        holder.setLayout(self.detail)
        body = QHBoxLayout()
        body.addLayout(left)
        body.addWidget(holder, 1)
        layout.addLayout(body, 1)
        self._fill_games()
        return page

    def _effective(self) -> dict[str, dict]:
        return load_games(self.cfg["games"], self.default_mode.currentData() if hasattr(self, "default_mode") else self.cfg["default_mode"])

    def _fill_games(self, select: str | None = None) -> None:
        self.game_list.blockSignals(True)
        self.game_list.clear()
        games = self._effective()
        names = [g.get("name") or g["exe"] for g in games.values()]
        for key, game in sorted(games.items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
            name = game.get("name") or game["exe"]
            item = QListWidgetItem(f"{name} ({game['exe']})" if names.count(name) > 1 else name)  # e.g. two Apex exes
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.game_list.addItem(item)
            if key == (select or "").lower():
                self.game_list.setCurrentItem(item)
        self.game_list.blockSignals(False)
        self._show_game()

    def _clear_detail(self) -> None:
        while self.detail.count():
            item = self.detail.takeAt(0)
            if item.widget():
                item.widget().hide()  # deleteLater alone leaves it painted until the next event loop turn
                item.widget().deleteLater()

    def _show_game(self) -> None:
        self._clear_detail()
        item = self.game_list.currentItem()
        game = self._effective().get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if game is None:
            self.detail.addWidget(_label(t("games.empty"), "hint"))
            self.detail.addStretch(1)
            return
        exe = game["exe"]
        self.detail.addWidget(_label(game.get("name") or exe, "gameName"))
        self.detail.addWidget(_label(exe, "muted"))

        mode = _combo([(t(f"mode.{m}"), m) for m in MODES], game["mode"])
        mode_help = _label(t(f"mode.{game['mode']}.help"), "hint")
        capture = _combo([(t("capture.auto"), None)] + [(t(f"capture.{k}"), k) for k in KINDS], game.get("capture"))

        def on_mode() -> None:
            set_game(self.cfg, exe, mode=mode.currentData())
            mode_help.setText(t(f"mode.{mode.currentData()}.help"))

        mode.currentIndexChanged.connect(on_mode)
        capture.currentIndexChanged.connect(lambda: set_game(self.cfg, exe, capture=capture.currentData()))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(16)
        form.addRow(t("games.mode"), mode)
        form.addRow("", mode_help)
        form.addRow(t("games.capture"), capture)
        wrap = QWidget()
        wrap.setObjectName("page")
        wrap.setLayout(form)
        self.detail.addWidget(wrap)

        rule = QFrame()
        rule.setObjectName("rule")
        self.detail.addWidget(rule)
        plugin = PLUGINS.get(game.get("plugin"))
        if plugin is None:
            self.detail.addWidget(_label(t("games.no_plugin"), "hint"))
        else:
            self.detail.addWidget(_label(t("games.events"), "muted"))
            disabled = set(game.get("disabled_events", ()))
            boxes: dict[str, QCheckBox] = {}

            def on_toggle() -> None:
                set_game(self.cfg, exe, disabled_events=[e for e, b in boxes.items() if not b.isChecked()])

            grid = QHBoxLayout()
            columns = [QVBoxLayout(), QVBoxLayout()]
            events = [e for e in plugin.event_types if e not in BOUNDARIES]
            for i, event in enumerate(events):
                box = QCheckBox(t(f"event.{event}"))
                box.setChecked(event not in disabled)
                box.toggled.connect(on_toggle)
                boxes[event] = box
                columns[i * 2 // max(1, len(events))].addWidget(box)
            for column in columns:
                column.addStretch(1)
                grid.addLayout(column)
            holder = QWidget()
            holder.setObjectName("page")
            holder.setLayout(grid)
            self.detail.addWidget(holder)
        self.detail.addStretch(1)
        remove = QPushButton(t("games.remove"))
        remove.setObjectName("danger")
        remove.clicked.connect(lambda: (remove_game(self.cfg, exe, self.builtin), self._fill_games()))
        row = QHBoxLayout()
        row.addWidget(remove)
        row.addStretch(1)
        bottom = QWidget()
        bottom.setObjectName("page")
        bottom.setLayout(row)
        self.detail.addWidget(bottom)

    def _scan(self) -> None:
        # ponytail: runs on the UI thread (about a second here); move to a worker thread if
        # big libraries on slow disks make the window stall.
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            found = library_scan.scan(set(self._effective()))
        finally:
            self.unsetCursor()
        for game in found:
            set_game(self.cfg, game["exe"], name=game["name"])
        self.scan_result.setText(t("games.scan_done", count=len(found)))
        self._fill_games(found[0]["exe"] if found else None)

    def _add_running(self) -> None:
        dialog = PickRunningApp(self, set(self._effective()))
        if dialog.exec() and (exe := dialog.chosen()):
            set_game(self.cfg, exe, name=Path(exe).stem)
            self._fill_games(exe)

    # -- Save ------------------------------------------------------------------

    def _save(self) -> None:
        self.cfg.update(
            language=self.language.currentData(),
            hotkey=self.hotkey.keySequence().toString() or self.cfg["hotkey"],
            default_mode=self.default_mode.currentData(),
            quality=self.quality.currentData(),
            fps=self.fps.currentData(),
            pre_seconds=self.pre.value(),
            post_seconds=self.post.value(),
            max_clip_seconds=self.max_clip.value(),
            exit_debounce_seconds=self.debounce.value(),
        )
        self.accept()
