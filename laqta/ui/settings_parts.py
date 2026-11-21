"""Pieces of the settings window that stand on their own: edits to the settings (no Qt), the
"Add a game" window, the choice tile, and small widget helpers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..game_detector import library_scan
from ..game_detector.detector import GAMES_JSON, NOT_GAMES, processes
from ..i18n import t
from . import theme

# -- config edits (no Qt: unit-tested) ------------------------------------------------


def builtin_exes() -> set[str]:
    import json

    return {g["exe"].lower() for g in json.loads(GAMES_JSON.read_text(encoding="utf-8"))}


# Video quality in one choice. "custom" is whatever does not match one of these.
VIDEO_PRESETS = {
    "light": {"quality": "Stream", "fps": 30, "output_height": 720, "bitrate": 0},
    "balanced": {"quality": "Small", "fps": 60, "output_height": 1080, "bitrate": 0},
    "high": {"quality": "HQ", "fps": 60, "output_height": 0, "bitrate": 0},
}


def preset_of(values: dict) -> str:
    """Which preset these video settings are, or "custom"."""
    return next((name for name, preset in VIDEO_PRESETS.items() if all(values.get(k) == v for k, v in preset.items())), "custom")


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


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return theme.lead(label)


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


def addable_games(known: set[str], installed: list[dict], running: list[str]) -> list[dict]:
    """What the "Add a game" window offers: installed games first, then other programs running
    right now, without anything already in the list or anything that is clearly not a game."""
    offered: dict[str, dict] = {}
    for game in installed:
        key = game["exe"].lower()
        if key not in known and key not in offered:
            offered[key] = {"exe": game["exe"], "name": game["name"], "where": "installed"}
    for exe in sorted(set(running), key=str.lower):
        key = exe.lower()
        if key.endswith(".exe") and key not in known and key not in offered and key not in NOT_GAMES:
            offered[key] = {"exe": exe, "name": Path(exe).stem, "where": "running"}
    return list(offered.values())


class AddGameDialog(QDialog):
    """Search the games installed on this PC (Steam, Epic, GOG) and the programs running now,
    and add any of them. Nothing is looked up online: the list is this PC's own."""

    def __init__(self, parent: QWidget, known: set[str]):
        super().__init__(parent)
        self.setWindowTitle(t("games.add_title"))
        self.resize(460, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(t("games.search"))
        self.filter.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:  # ponytail: scanned on the UI thread, about a second; a worker thread if big libraries stall it
            installed = library_scan.scan(known)
        finally:
            self.unsetCursor()
        section = None
        for game in addable_games(known, installed, list(processes().values())):
            if game["where"] != section:
                section = game["where"]
                head = QListWidgetItem(t(f"games.{section}"))
                head.setFlags(Qt.ItemFlag.NoItemFlags)  # a heading, not a choice
                bold = head.font()
                bold.setBold(True)
                head.setFont(bold)
                self.list.addItem(head)
            item = QListWidgetItem(game["name"] if game["where"] == "running" else f"{game['name']}   ({game['exe']})")
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.list.addItem(item)
        self.note = _label(t("games.add_hint") if self.list.count() else t("games.add_none"), "hint")
        self.filter.textChanged.connect(self._apply_filter)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        browse, add, cancel = QPushButton(t("games.browse")), QPushButton(t("button.add")), QPushButton(t("button.cancel"))
        add.setObjectName("primary")
        add.setDefault(True)
        add.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        browse.clicked.connect(self._browse)
        self.browsed: dict | None = None
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(browse)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(add)
        layout.addWidget(self.filter)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.note)
        layout.addLayout(buttons)
        self.filter.setFocus()

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            game = item.data(Qt.ItemDataRole.UserRole)
            # Headings stay while searching is off; a search shows matches only.
            item.setHidden(bool(text) and (game is None or text not in f"{game['name']} {game['exe']}".lower()))

    def _browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, t("games.browse"), "", "Programs (*.exe)")
        if chosen:
            self.browsed = {"exe": Path(chosen).name, "name": Path(chosen).stem}
            self.accept()

    def chosen(self) -> list[dict]:
        if self.browsed:
            return [self.browsed]
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems() if not item.isHidden() and item.data(Qt.ItemDataRole.UserRole)]


class Tile(QPushButton):
    """A choice shown as a block with a title and a line of explanation; checked = chosen."""

    def __init__(self, title: str, hint: str = ""):
        super().__init__()
        self.setObjectName("tile")
        self.setCheckable(True)
        self.setAccessibleName(title)
        self.setAccessibleDescription(hint)
        inside = QVBoxLayout(self)
        inside.setContentsMargins(12, 9, 12, 9)
        inside.setSpacing(2)
        for text, name in ((title, "tileTitle"), (hint, "tileHint")):
            if text:
                label = _label(text, name)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                inside.addWidget(label)
        inside.addStretch(1)
        self.setMinimumHeight(78 if hint else 40)
