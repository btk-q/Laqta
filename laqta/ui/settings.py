"""Settings window. Works on a copy of the config; nothing changes until "Save changes"."""

from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QGridLayout, QColorDialog, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)  # fmt: skip

from ..core import health
from ..core.recorder import BOUNDARIES, MODES
from ..game_detector.detector import load_games
from ..i18n import LANGUAGES, isolate, t
from ..obs_controller import audio, encoders
from ..obs_controller.capture import KINDS
from ..plugins import PLUGINS
from ..storage import autostart, clips, config, library
from ..storage.clips import usable_folder
from . import gameicons, icons, theme
from .library import size_text

# -- config edits (no Qt: unit-tested) ------------------------------------------------


# -- widgets -----------------------------------------------------------------------------
from .settings_parts import (  # moved; imported back so existing imports keep working
    builtin_exes,
    VIDEO_PRESETS,
    preset_of,
    set_game,
    remove_game,
    _label,
    _combo,
    _spin,
    addable_games,
    AddGameDialog,
    Tile,
)


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
        self.rail.setFixedWidth(230)
        self.rail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages = QStackedWidget()
        for key, page in (("nav.general", self._general()), ("nav.games", self._games()), ("nav.recording", self._recording()),
                          ("nav.storage", self._storage()), ("nav.audio", self._audio()), ("nav.appearance", self._appearance()), ("nav.sharing", self._sharing()), ("nav.health", self._health())):
            self.rail.addItem(t(key))
            scroll = QScrollArea()  # pages outgrow a 768-pixel-high screen
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        # The Games page is reached by picking a game under "My games", not from this list.
        self.rail.item(1).setHidden(True)
        self.rail.setFixedHeight(sum(self.rail.sizeHintForRow(i) for i in range(self.rail.count()) if i != 1) + 34)
        self.rail.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rail.currentRowChanged.connect(self._page_chosen)
        self.rail.setCurrentRow(0)
        add = QPushButton(t("games.add_title"))
        add.clicked.connect(self._add_game)
        side = QVBoxLayout()
        side.setContentsMargins(1, 14, 1, 12)  # 1 px each side so the lists do not cover the sidebar border
        side.setSpacing(0)
        side.addWidget(_label(t("nav.settings"), "railTitle"))
        side.addWidget(self.rail)
        side.addWidget(_label(t("nav.my_games"), "railTitle"))
        side.addWidget(self.game_list, 1)
        side.addWidget(self.scan_result)
        below = QHBoxLayout()
        below.setContentsMargins(12, 8, 12, 0)
        below.addWidget(add)
        side.addLayout(below)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(232)
        sidebar.setLayout(side)

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
        root.addWidget(sidebar)
        root.addLayout(right, 1)

    def _page_chosen(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        if row != 1 and self.game_list.currentItem() is not None:  # left the games: nothing stays highlighted there
            self.game_list.blockSignals(True)
            self.game_list.setCurrentRow(-1)
            self.game_list.blockSignals(False)

    def _game_chosen(self) -> None:
        self._show_game()
        if self.game_list.currentItem() is not None:
            self.rail.setCurrentRow(1)

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
        self.record_hotkey = QKeySequenceEdit(QKeySequence(self.cfg["record_hotkey"]))
        self.record_hotkey.setMaximumSequenceLength(1)
        form.addRow(t("general.replay_seconds"), self.replay_seconds)
        form.addRow("", self.replay_hint)
        form.addRow(t("general.record_hotkey"), self.record_hotkey)
        form.addRow("", _label(t("general.record_hint"), "hint"))
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
        video = QFormLayout()
        video.setVerticalSpacing(14)
        video.setHorizontalSpacing(20)
        video.addRow(t("rec.quality"), self.quality)
        video.addRow(t("rec.fps"), self.fps)
        video.addRow(t("rec.output"), self.output_height)
        video.addRow(t("rec.encoder"), self.encoder)
        video.addRow(t("rec.bitrate"), self.bitrate)
        self.custom_video = QWidget()  # the detailed controls, shown only for "Custom"
        self.custom_video.setObjectName("page")
        self.custom_video.setLayout(video)
        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.presets: dict[str, Tile] = {}
        group = QButtonGroup(page)
        current = preset_of(self.cfg)
        for i, name in enumerate((*VIDEO_PRESETS, "custom")):
            tile = self.presets[name] = Tile(t(f"preset.{name}"), t(f"preset.{name}.hint"))
            tile.setChecked(name == current)
            tile.clicked.connect(lambda _=False, chosen=name: self._use_preset(chosen))
            group.addButton(tile)
            tiles.addWidget(tile, i // 2, i % 2)
        layout.addWidget(_label(t("rec.preset")))
        layout.addLayout(tiles)
        layout.addWidget(self.custom_video)
        self.custom_video.setVisible(current == "custom")
        layout.addSpacing(6)
        # Which events also become their own clip after a recording.
        offered = ("ace", "multikill", "kill", "headshot", "first_blood", "objective", "goal", "save", "win")
        self.auto_clips: dict[str, QPushButton] = {}
        chosen = set(self.cfg["auto_clips"])
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, kind in enumerate(offered):
            box = self.auto_clips[kind] = QPushButton(t(f"event.{kind}"))
            box.setObjectName("tile")
            box.setCheckable(True)
            box.setIcon(QIcon(icons.pixmap(kind, 18)))
            box.setIconSize(QSize(18, 18))
            box.setChecked(kind in chosen)
            grid.addWidget(box, i // 3, i % 3)
        layout.addWidget(_label(t("rec.auto_clips")))
        layout.addLayout(grid)
        layout.addWidget(_label(t("rec.auto_clips_hint"), "hint"))
        layout.addSpacing(6)
        form.addRow(t("rec.pre"), self.pre)
        form.addRow(t("rec.post"), self.post)
        form.addRow(t("rec.max_clip"), self.max_clip)
        form.addRow(t("rec.debounce"), self.debounce)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _use_preset(self, name: str) -> None:
        """A preset fills in the detailed controls (which is what gets saved); Custom reveals them."""
        self.custom_video.setVisible(name == "custom")
        for key, value in VIDEO_PRESETS.get(name, {}).items():
            widget = {"quality": self.quality, "fps": self.fps, "output_height": self.output_height, "bitrate": self.bitrate}[key]
            widget.setValue(value) if key == "bitrate" else widget.setCurrentIndex(max(0, widget.findData(value)))

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

    # -- Storage ---------------------------------------------------------------

    def _storage(self) -> QWidget:
        page, layout = self._page("nav.storage")
        self.space = QVBoxLayout()
        self.space.setSpacing(8)
        holder = QWidget()
        holder.setObjectName("page")
        holder.setLayout(self.space)
        layout.addWidget(holder)
        self._fill_space()
        layout.addSpacing(12)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(20)
        self.cleanup_days = _spin(self.cfg["cleanup_days"], 0, 3650)
        self.cleanup_gb = _spin(self.cfg["cleanup_gb"], 0, 100000)
        form.addRow(t("rec.cleanup_days"), self.cleanup_days)
        form.addRow(t("rec.cleanup_gb"), self.cleanup_gb)
        self.min_free = _spin(self.cfg["min_free_gb"], 0, 1000)
        self.min_free.setSpecialValueText(t("rec.min_free_off"))
        form.addRow(t("rec.min_free"), self.min_free)
        form.addRow("", _label(t("rec.min_free_hint"), "hint"))
        layout.addLayout(form)
        layout.addWidget(_label(t("rec.cleanup_hint"), "hint"))
        layout.addSpacing(8)
        self.save_folder = QLineEdit(self.cfg["save_folder"] or str(clips.DEFAULT_ROOT))
        self.save_folder.setReadOnly(True)  # chosen with the folder dialog, so it always exists
        browse, default, show = QPushButton(t("rec.browse")), QPushButton(t("rec.use_default")), QPushButton(t("storage.open"))
        browse.clicked.connect(self._browse_folder)
        default.clicked.connect(lambda: self.save_folder.setText(str(clips.DEFAULT_ROOT)))
        show.clicked.connect(lambda: os.startfile(self.save_folder.text()) if Path(self.save_folder.text()).is_dir() else None)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.save_folder, 1)
        row.addWidget(show)
        row.addWidget(browse)
        row.addWidget(default)
        layout.addWidget(_label(t("rec.save_folder")))
        layout.addLayout(row)
        self.folder_note = _label(t("rec.save_hint"), "hint")
        layout.addWidget(self.folder_note)
        layout.addStretch(1)
        return page

    def _fill_space(self) -> None:
        """How much room each game's videos take, and how much is left on the drive."""
        rows = library.usage(library.scan(clips.ROOT))
        total = sum(size for _, size, _ in rows)
        free = clips.free_bytes()
        self.space.addWidget(_label(t("storage.total", size=size_text(total), free=size_text(free) if free is not None else "?"), "gameName"))
        biggest = rows[0][1] if rows else 1
        for game, size, count in rows[:10]:
            name = QLabel(game)
            picture = QLabel()
            exe = next((g["exe"] for g in self._effective().values() if (g.get("name") or g["exe"]).casefold() == game.casefold()), None)
            if exe and (icon := gameicons.get(exe)):
                picture.setPixmap(icon.scaled(20, 20, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            picture.setFixedWidth(22)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(1000 * size / biggest))
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            amount = QLabel(t("storage.row", size=size_text(size), count=count))
            amount.setObjectName("muted")
            line = QHBoxLayout()
            line.setSpacing(10)
            line.addWidget(picture)
            name.setFixedWidth(170)
            line.addWidget(name)
            line.addWidget(bar, 1)
            amount.setFixedWidth(150)
            line.addWidget(amount)
            box = QWidget()
            box.setObjectName("page")
            box.setLayout(line)
            self.space.addWidget(box)
        if not rows:
            self.space.addWidget(_label(t("storage.empty"), "hint"))

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
        layout.addSpacing(10)
        self.laugh = QCheckBox(t("audio.laugh"))
        self.laugh.setChecked(self.cfg["voice_detect"])
        self.laugh_minutes = _spin(self.cfg["laugh_clip_minutes"], 0, 15)
        funny = QFormLayout()
        funny.setHorizontalSpacing(20)
        funny.addRow(t("audio.laugh_minutes"), self.laugh_minutes)
        layout.addSpacing(10)
        layout.addWidget(self.laugh)
        layout.addLayout(funny)
        layout.addWidget(_label(t("audio.laugh_hint"), "hint"))
        layout.addStretch(1)

        def sync() -> None:
            self.mic_device.setEnabled(self.mic_enabled.isChecked())
            self.mic_volume.setEnabled(self.mic_enabled.isChecked())
            self.laugh.setEnabled(self.mic_enabled.isChecked())
            self.laugh_minutes.setEnabled(self.mic_enabled.isChecked() and self.laugh.isChecked())

        self.mic_enabled.toggled.connect(sync)
        self.laugh.toggled.connect(sync)
        sync()
        return page

    # -- Sharing ---------------------------------------------------------------

    def _sharing(self) -> QWidget:
        page, layout = self._page("nav.sharing")
        layout.addWidget(_label(t("share.intro"), "hint"))
        self.toast_window = QCheckBox(t("share.toast"))
        self.toast_window.setChecked(self.cfg["toast_window"])
        layout.addWidget(self.toast_window)
        layout.addWidget(_label(t("share.toast_hint"), "hint"))
        layout.addSpacing(10)
        layout.addWidget(_label(t("share.webhook")))
        self.webhook = QLineEdit(self.cfg["discord_webhook"])
        self.webhook.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)  # it is a secret: hidden once typed
        self.webhook.setAccessibleName(t("share.webhook"))
        self.webhook.setClearButtonEnabled(True)
        layout.addWidget(self.webhook)
        self.webhook_note = _label(t("share.webhook_hint"), "hint")
        layout.addWidget(self.webhook_note)
        layout.addSpacing(10)
        self.update_check = QCheckBox(t("share.update"))
        self.update_check.setChecked(self.cfg["update_check"])
        layout.addWidget(self.update_check)
        layout.addWidget(_label(t("share.update_hint"), "hint"))
        layout.addStretch(1)
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
        again, save, fix = QPushButton(t("health.again")), QPushButton(t("health.zip")), QPushButton(t("health.setup"))
        again.clicked.connect(self._fill_health)
        fix.clicked.connect(self._open_setup)
        save.clicked.connect(self._save_zip)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(again)
        row.addWidget(fix)
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

    def _open_setup(self) -> None:
        from .setup import SetupDialog

        SetupDialog(self.cfg, self).exec()
        self._fill_health()

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
        self.game_list = QListWidget()  # shown in the sidebar under "My games"
        self.game_list.setObjectName("rail")
        self.game_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.game_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.game_list.setIconSize(QSize(22, 22))
        self.icon_finder = gameicons.Finder(self)
        self.icon_finder.done.connect(lambda: self._fill_games(self._current_game()))
        self.game_list.currentItemChanged.connect(lambda *_: self._game_chosen())
        self.scan_result = _label("", "hint")
        self.scan_result.setContentsMargins(16, 4, 12, 0)
        page = QWidget()
        page.setObjectName("page")
        self.detail = QVBoxLayout(page)
        self.detail.setContentsMargins(28, 22, 28, 8)
        self.detail.setSpacing(10)
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
            item = QListWidgetItem(isolate(f"{name} ({game['exe']})") if names.count(name) > 1 else name)  # e.g. two Apex exes
            item.setIcon(QIcon(gameicons.get(game["exe"]) or gameicons.blank()))  # a blank keeps the names in one column
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.game_list.addItem(item)
            if key == (select or "").lower():
                self.game_list.setCurrentItem(item)
        self.game_list.blockSignals(False)
        self.icon_finder.find([g["exe"] for g in games.values()])  # for games with no picture yet
        self._show_game()

    def _current_game(self) -> str | None:
        item = self.game_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

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
        self.detail.addWidget(_label(game.get("name") or exe, "heading"))
        self.detail.addWidget(_label(exe, "muted"))

        tiles = QGridLayout()
        tiles.setSpacing(8)
        group = QButtonGroup(self.detail)
        for i, m in enumerate(("session", "match", "events")):
            tile = Tile(t(f"mode.{m}"), t(f"mode.{m}.help"))
            tile.setChecked(m == ("session" if game["mode"] == "hybrid" else game["mode"]))  # hybrid is session under an old name
            tile.clicked.connect(lambda _=False, chosen=m: set_game(self.cfg, exe, mode=chosen))
            group.addButton(tile)
            tiles.addWidget(tile, i, 0)  # full width: the explanations need the room
        modes = QWidget()
        modes.setObjectName("page")
        modes.setLayout(tiles)
        self.detail.addWidget(_label(t("games.mode"), "muted"))
        self.detail.addWidget(modes)
        capture = _combo([(t("capture.auto"), None)] + [(t(f"capture.{k}"), k) for k in KINDS], game.get("capture"))

        capture.currentIndexChanged.connect(lambda: set_game(self.cfg, exe, capture=capture.currentData()))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(16)
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
            boxes: dict[str, QPushButton] = {}

            def on_toggle() -> None:
                set_game(self.cfg, exe, disabled_events=[e for e, b in boxes.items() if not b.isChecked()])

            grid = QGridLayout()
            grid.setSpacing(8)
            events = [e for e in plugin.event_types if e not in BOUNDARIES]
            for i, event in enumerate(events):
                box = QPushButton(t(f"event.{event}"))
                box.setObjectName("tile")
                box.setCheckable(True)
                box.setIcon(QIcon(icons.pixmap(event, 18)))
                box.setIconSize(QSize(18, 18))
                box.setChecked(event not in disabled)
                box.toggled.connect(on_toggle)
                boxes[event] = box
                grid.addWidget(box, i // 2, i % 2)
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

    def _add_game(self) -> None:
        dialog = AddGameDialog(self, set(self._effective()))
        if dialog.exec() and (games := dialog.chosen()):
            for game in games:
                set_game(self.cfg, game["exe"], name=game["name"])
            self.scan_result.setText(t("games.added", count=len(games)))
            self._fill_games(games[0]["exe"])

    # -- Save ------------------------------------------------------------------

    def _save(self) -> None:
        clip_key = self.hotkey.keySequence().toString() or self.cfg["hotkey"]
        replay_key = self.replay_hotkey.keySequence().toString() or self.cfg["replay_hotkey"]
        record_key = self.record_hotkey.keySequence().toString() or self.cfg["record_hotkey"]
        if len({clip_key, replay_key, record_key}) < 3:  # one key cannot do two things: say so instead of silently breaking one
            self.rail.setCurrentRow(0)
            return self.replay_hint.setText(t("general.hotkey_same"))
        webhook = self.webhook.text().strip()
        if webhook and not config.WEBHOOK.fullmatch(webhook):
            self.rail.setCurrentRow(6)  # Sharing and updates
            return self.webhook_note.setText(t("share.webhook_bad"))
        if self.autostart.isChecked() != (self._autostart == "on"):  # only touch the registry when the box was changed
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except OSError as e:
                self.rail.setCurrentRow(0)
                return self.autostart_note.setText(t("general.autostart_failed", error=e))
        self.cfg.update(
            replay_hotkey=replay_key,
            record_hotkey=record_key,
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
            toast_window=self.toast_window.isChecked(),
            update_check=self.update_check.isChecked(),
            discord_webhook=webhook,
            voice_detect=self.laugh.isChecked(),
            # Types not offered as tiles (set by hand in the file) are kept.
            auto_clips=[k for k in self.cfg["auto_clips"] if k not in self.auto_clips] + [k for k, box in self.auto_clips.items() if box.isChecked()],
            laugh_clip_minutes=self.laugh_minutes.value(),
            save_folder="" if Path(self.save_folder.text()) == clips.DEFAULT_ROOT else self.save_folder.text(),
        )
        self.accept()