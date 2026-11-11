"""Settings window. Works on a copy of the config; nothing changes until "Save changes"."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)  # fmt: skip

from ..core import health
from ..core.recorder import BOUNDARIES, MODES
from ..game_detector import library_scan
from ..game_detector.detector import GAMES_JSON, NOT_GAMES, load_games, processes
from ..i18n import LANGUAGES, t
from ..obs_controller import audio, encoders
from ..obs_controller.capture import KINDS
from ..plugins import PLUGINS
from ..storage import autostart, clips, config
from ..storage.clips import usable_folder
from . import theme

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
    def __init__(self, cfg: dict, parent: QWidget | None = None, hotkeys: dict[str, bool] | None = None):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        self.hotkeys = hotkeys  # combo -> did Windows give it to us; shown on the Health page
        self.builtin = builtin_exes()
        self.setWindowTitle(t("settings.title"))
        self.resize(900, 660)

        self.rail = QListWidget()
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(170)
        self.pages = QStackedWidget()
        for key, page in (("nav.general", self._general()), ("nav.games", self._games()), ("nav.recording", self._recording()),
                          ("nav.audio", self._audio()), ("nav.appearance", self._appearance()), ("nav.health", self._health())):
            self.rail.addItem(t(key))
            scroll = QScrollArea()  # pages outgrow a 768-pixel-high screen
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
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
        self.replay_hotkey = QKeySequenceEdit(QKeySequence(self.cfg["replay_hotkey"]))
        self.replay_hotkey.setMaximumSequenceLength(1)
        self.replay_seconds = _spin(self.cfg["replay_seconds"], 5, 300)
        self.replay_hint = _label(t("general.replay_hint"), "hint")
        form.addRow(t("general.replay_hotkey"), self.replay_hotkey)
        form.addRow(t("general.replay_seconds"), self.replay_seconds)
        form.addRow("", self.replay_hint)
        form.addRow(t("general.default_mode"), self.default_mode)
        form.addRow("", self.default_mode_help)
        layout.addLayout(form)
        self._autostart = autostart.state()
        self.autostart = QCheckBox(t("general.autostart"))
        self.autostart.setChecked(self._autostart == "on")
        layout.addWidget(self.autostart)
        self.autostart_note = _label(t("general.autostart_blocked") if self._autostart == "blocked" else "", "hint")
        layout.addWidget(self.autostart_note)
        layout.addStretch(1)
        return page

    # -- Recording -------------------------------------------------------------

    def _recording(self) -> QWidget:
        page, layout = self._page("nav.recording")
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(20)
        self.quality = _combo([(t(f"quality.{q}"), q) for q in config.QUALITIES], self.cfg["quality"])
        self.fps = _combo([(str(f), f) for f in config.FPS], self.cfg["fps"])
        self.encoder = _combo(self._encoder_options(t("rec.encoder_auto"), "auto"), self.cfg["encoder"])
        self.output_height = _combo(self._height_options(t("rec.output_native"), 0), self.cfg["output_height"])
        self.bitrate = _spin(self.cfg["bitrate"], 0, 150000)
        self.bitrate.setSingleStep(1000)
        self.bitrate.setSpecialValueText(t("rec.bitrate_auto"))  # shown instead of 0
        self.pre = _spin(self.cfg["pre_seconds"], 0, 120)
        self.post = _spin(self.cfg["post_seconds"], 0, 60)
        self.max_clip = _spin(self.cfg["max_clip_seconds"], 10, 300)
        self.debounce = _spin(self.cfg["exit_debounce_seconds"], 0, 120)
        form.addRow(t("rec.quality"), self.quality)
        form.addRow(t("rec.fps"), self.fps)
        form.addRow(t("rec.output"), self.output_height)
        form.addRow(t("rec.encoder"), self.encoder)
        form.addRow(t("rec.bitrate"), self.bitrate)
        form.addRow(t("rec.pre"), self.pre)
        form.addRow(t("rec.post"), self.post)
        form.addRow(t("rec.max_clip"), self.max_clip)
        self.cleanup_days = _spin(self.cfg["cleanup_days"], 0, 3650)
        self.cleanup_gb = _spin(self.cfg["cleanup_gb"], 0, 100000)
        form.addRow(t("rec.debounce"), self.debounce)
        form.addRow(t("rec.cleanup_days"), self.cleanup_days)
        form.addRow(t("rec.cleanup_gb"), self.cleanup_gb)
        self.min_free = _spin(self.cfg["min_free_gb"], 0, 1000)
        self.min_free.setSpecialValueText(t("rec.min_free_off"))
        form.addRow(t("rec.min_free"), self.min_free)
        form.addRow("", _label(t("rec.min_free_hint"), "hint"))
        layout.addLayout(form)
        layout.addWidget(_label(t("rec.cleanup_hint"), "hint"))
        self.save_folder = QLineEdit(self.cfg["save_folder"] or str(clips.DEFAULT_ROOT))
        self.save_folder.setReadOnly(True)  # chosen with the folder dialog, so it always exists
        browse, default = QPushButton(t("rec.browse")), QPushButton(t("rec.use_default"))
        browse.clicked.connect(self._browse_folder)
        default.clicked.connect(lambda: self.save_folder.setText(str(clips.DEFAULT_ROOT)))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.save_folder, 1)
        row.addWidget(browse)
        row.addWidget(default)
        layout.addWidget(_label(t("rec.save_folder")))
        layout.addLayout(row)
        self.folder_note = _label(t("rec.save_hint"), "hint")
        layout.addWidget(self.folder_note)
        layout.addStretch(1)
        return page

    @staticmethod
    def _encoder_options(first: str, first_value) -> list[tuple[str, object]]:
        return [(first, first_value), *((encoders.NAMES[e], e) for e in encoders.ORDER)]

    @staticmethod
    def _height_options(first: str, first_value) -> list[tuple[str, object]]:
        return [(first, first_value), *((t("rec.output_p", height=h), h) for h in config.HEIGHTS if h)]

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, t("rec.save_folder"), self.save_folder.text())
        if not chosen:
            return
        folder = Path(chosen)
        if not usable_folder(folder):
            return self.folder_note.setText(t("rec.folder_bad", path=str(folder)))
        self.save_folder.setText(str(folder))
        self.folder_note.setText(t("rec.save_hint"))

    # -- Audio -----------------------------------------------------------------

    def _audio(self) -> QWidget:
        page, layout = self._page("nav.audio")
        self.mic_enabled = QCheckBox(t("audio.mic"))
        self.mic_enabled.setChecked(self.cfg["mic_enabled"])
        devices = [(t("audio.mic_default"), "default"), *((name, device) for device, name in audio.microphones())]
        if self.cfg["mic_device"] not in [d for _, d in devices]:
            devices.append((self.cfg["mic_device"], self.cfg["mic_device"]))  # unplugged right now: keep the choice
        self.mic_device = _combo(devices, self.cfg["mic_device"])
        self.mic_volume = _spin(self.cfg["mic_volume"], 0, 200)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(20)
        form.addRow(t("audio.mic_device"), self.mic_device)
        form.addRow(t("audio.mic_volume"), self.mic_volume)
        layout.addWidget(self.mic_enabled)
        layout.addWidget(_label(t("audio.privacy"), "hint"))
        layout.addLayout(form)
        layout.addWidget(_label(t("audio.tracks"), "hint"))
        self.mic_trigger = QCheckBox(t("audio.trigger"))
        self.mic_trigger.setChecked(self.cfg["mic_trigger"])
        self.mic_trigger_db = _spin(self.cfg["mic_trigger_db"], -60, 0)
        trigger = QFormLayout()
        trigger.setHorizontalSpacing(20)
        trigger.addRow(t("audio.trigger_level"), self.mic_trigger_db)
        layout.addSpacing(10)
        layout.addWidget(self.mic_trigger)
        layout.addLayout(trigger)
        layout.addWidget(_label(t("audio.trigger_hint"), "hint"))
        layout.addStretch(1)

        def sync() -> None:
            self.mic_device.setEnabled(self.mic_enabled.isChecked())
            self.mic_volume.setEnabled(self.mic_enabled.isChecked())
            self.mic_trigger.setEnabled(self.mic_enabled.isChecked())  # it reads the microphone track's meter
            self.mic_trigger_db.setEnabled(self.mic_enabled.isChecked() and self.mic_trigger.isChecked())

        self.mic_enabled.toggled.connect(sync)
        self.mic_trigger.toggled.connect(sync)
        sync()
        return page

    # -- Health ----------------------------------------------------------------

    def _health(self) -> QWidget:
        page, layout = self._page("nav.health")
        layout.addWidget(_label(t("health.intro"), "hint"))
        self.health_rows = QVBoxLayout()
        self.health_rows.setSpacing(14)
        holder = QWidget()
        holder.setObjectName("page")
        holder.setLayout(self.health_rows)
        layout.addWidget(holder)
        again, save = QPushButton(t("health.again")), QPushButton(t("health.zip"))
        again.clicked.connect(self._fill_health)
        save.clicked.connect(self._save_zip)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(again)
        row.addWidget(save)
        row.addStretch(1)
        layout.addSpacing(6)
        layout.addLayout(row)
        self.health_note = _label(t("health.zip_hint"), "hint")
        layout.addWidget(self.health_note)
        layout.addStretch(1)
        self._fill_health()
        return page

    def _fill_health(self) -> None:
        while self.health_rows.count():
            item = self.health_rows.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        # ponytail: checked on the UI thread (registry reads and one test write, a few
        # milliseconds); move to a worker if a check ever needs OBS or the network.
        self.health = health.checks(health.gather(self.cfg, self.hotkeys))
        for check in self.health:
            badge = _label(t(f"health.status.{check.status}"), f"badge_{check.status}")
            badge.setWordWrap(False)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedWidth(76)
            words = QVBoxLayout()
            words.setSpacing(2)
            words.addWidget(_label(t(f"health.{check.key}"), "checkTitle"))
            text = _label(check.text, "hint" if check.status == "ok" else "")
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            words.addWidget(text)
            if check.key == "encoder" and check.status == "warn":
                fix = QPushButton(t("health.encoder.fix"))
                fix.clicked.connect(lambda: (health.forget_encoder(), self._fill_health()))
                words.addWidget(fix, 0, Qt.AlignmentFlag.AlignLeading)
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(14)
            line.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            line.addLayout(words, 1)
            row = QWidget()
            row.setObjectName("page")
            row.setLayout(line)
            self.health_rows.addWidget(row)

    def _save_zip(self) -> None:
        import datetime

        name = t("health.zip_name", date=datetime.date.today().isoformat()) + ".zip"
        chosen, _ = QFileDialog.getSaveFileName(self, t("health.zip"), str(Path.home() / "Desktop" / name), "Zip (*.zip)")
        if not chosen:
            return
        try:
            path = health.write_zip(Path(chosen), self.cfg, self.health)
        except OSError as e:
            return self.health_note.setText(t("health.zip_failed", error=e))
        self.health_note.setText(t("health.zip_done", path=str(path)))

    # -- Appearance ------------------------------------------------------------

    def _appearance(self) -> QWidget:
        page, layout = self._page("nav.appearance")
        layout.addWidget(_label(t("look.color")))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.swatches: dict[str, QPushButton] = {}
        for name, color in theme.PRESETS.items():
            button = QPushButton()
            button.setObjectName("swatch")
            button.setCheckable(True)
            button.setToolTip(t(f"color.{name}"))
            button.setAccessibleName(t(f"color.{name}"))
            button.setStyleSheet(f"QPushButton#swatch {{ background: {color}; }}")
            button.clicked.connect(lambda _=False, c=color: self._set_accent(c))
            self.swatches[color] = button
            row.addWidget(button)
        custom = QPushButton(t("look.custom"))
        custom.clicked.connect(self._pick_accent)
        row.addSpacing(8)
        row.addWidget(custom)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(_label(t("look.hint"), "hint"))
        layout.addStretch(1)
        self._set_accent(self.cfg["accent"], preview=False)
        return page

    def _set_accent(self, color: str, preview: bool = True) -> None:
        self.cfg["accent"] = color.upper()
        for preset, button in self.swatches.items():
            button.setChecked(preset.upper() == color.upper())
        if preview and (app := QApplication.instance()):
            theme.apply(app, color)  # live preview; Cancel puts the saved colour back

    def _pick_accent(self) -> None:
        from PySide6.QtGui import QColor

        chosen = QColorDialog.getColor(QColor(self.cfg["accent"]), self, t("look.color"))
        if chosen.isValid():
            self._set_accent(chosen.name())

    # -- Games -----------------------------------------------------------------

    def _games(self) -> QWidget:
        page, layout = self._page("nav.games")
        self.game_list = QListWidget()
        self.game_list.setFixedWidth(220)
        self.game_list.setMinimumHeight(300)
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
        game_audio = QCheckBox(t("games.game_audio"))
        game_audio.setChecked(bool(game.get("game_audio_only")))
        game_audio.setToolTip(t("games.game_audio_hint"))
        game_audio.toggled.connect(lambda on: set_game(self.cfg, exe, game_audio_only=on or None))
        form.addRow("", game_audio)
        same = t("games.as_global")
        fps = _combo([(same, None), *((str(f), f) for f in config.FPS)], game.get("fps"))
        height = _combo(self._height_options(same, None), game.get("output_height"))
        encoder = _combo(self._encoder_options(same, None), game.get("encoder"))
        bitrate = _spin(game.get("bitrate") or 0, 0, 150000)
        bitrate.setSingleStep(1000)
        bitrate.setSpecialValueText(same)
        fps.currentIndexChanged.connect(lambda: set_game(self.cfg, exe, fps=fps.currentData()))
        height.currentIndexChanged.connect(lambda: set_game(self.cfg, exe, output_height=height.currentData()))
        encoder.currentIndexChanged.connect(lambda: set_game(self.cfg, exe, encoder=encoder.currentData()))
        bitrate.valueChanged.connect(lambda value: set_game(self.cfg, exe, bitrate=value or None))
        form.addRow(t("rec.fps"), fps)
        form.addRow(t("rec.output"), height)
        form.addRow(t("rec.encoder"), encoder)
        form.addRow(t("rec.bitrate"), bitrate)
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
            if getattr(plugin, "wants_player", False):
                player = QLineEdit(game.get("player", ""))
                player.setAccessibleName(t("games.player"))
                player.textChanged.connect(lambda text: set_game(self.cfg, exe, player=text.strip() or None))
                who = QFormLayout()
                who.setHorizontalSpacing(16)
                who.addRow(t("games.player"), player)
                box = QWidget()
                box.setObjectName("page")
                box.setLayout(who)
                self.detail.addWidget(box)
                self.detail.addWidget(_label(t("games.player_hint"), "hint"))
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
        clip_key = self.hotkey.keySequence().toString() or self.cfg["hotkey"]
        replay_key = self.replay_hotkey.keySequence().toString() or self.cfg["replay_hotkey"]
        if clip_key == replay_key:  # one key cannot do both: say so instead of silently breaking one
            self.rail.setCurrentRow(0)
            return self.replay_hint.setText(t("general.hotkey_same"))
        if self.autostart.isChecked() != (self._autostart == "on"):  # only touch the registry when the box was changed
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except OSError as e:
                self.rail.setCurrentRow(0)
                return self.autostart_note.setText(t("general.autostart_failed", error=e))
        self.cfg.update(
            replay_hotkey=replay_key,
            replay_seconds=self.replay_seconds.value(),
            language=self.language.currentData(),
            hotkey=self.hotkey.keySequence().toString() or self.cfg["hotkey"],
            default_mode=self.default_mode.currentData(),
            quality=self.quality.currentData(),
            fps=self.fps.currentData(),
            encoder=self.encoder.currentData(),
            output_height=self.output_height.currentData(),
            bitrate=self.bitrate.value(),
            pre_seconds=self.pre.value(),
            post_seconds=self.post.value(),
            max_clip_seconds=self.max_clip.value(),
            exit_debounce_seconds=self.debounce.value(),
            cleanup_days=self.cleanup_days.value(),
            cleanup_gb=self.cleanup_gb.value(),
            min_free_gb=self.min_free.value(),
            mic_enabled=self.mic_enabled.isChecked(),
            mic_device=self.mic_device.currentData(),
            mic_volume=self.mic_volume.value(),
            mic_trigger=self.mic_trigger.isChecked(),
            mic_trigger_db=self.mic_trigger_db.value(),
            save_folder="" if Path(self.save_folder.text()) == clips.DEFAULT_ROOT else self.save_folder.text(),
        )
        self.accept()
