"""Clip library window: browse, filter, play, trim, export, delete."""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QFile, QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QProgressDialog,
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)  # fmt: skip

from ..i18n import t
from ..storage import clips, config, highlights, library
from ..storage.library import Clip
from . import theme

THUMB = QSize(256, 144)
CACHE = config.DIR / "thumbs"


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def size_text(size: int) -> str:
    return f"{size / 1024**3:.1f} GB" if size >= 1024**3 else f"{max(1, round(size / 1024**2))} MB"


def when_text(when: datetime.datetime, today: datetime.date | None = None) -> str:
    today = today or datetime.date.today()
    days = (today - when.date()).days
    day = t("library.today") if days == 0 else t("library.yesterday") if days == 1 else when.strftime("%Y-%m-%d")
    return f"{day} {when:%H:%M}"


def caption(clip: Clip, today: datetime.date | None = None) -> str:
    """Two lines under a thumbnail: the game, then when / how long / what happened."""
    parts = [when_text(clip.when, today)]
    if clip.seconds:
        parts.append(clock(clip.seconds))
    kinds = list(dict.fromkeys(e["type"] for e in clip.events if e["type"] not in ("match_start", "match_end")))
    if kinds:
        parts.append(", ".join(t(f"event.{k}") for k in kinds[:3]))
    elif clip.kind == "recording":
        parts.append(size_text(clip.size))
    star = "★ " if clip.favorite else ""
    return f"{star}{clip.game}\n{'   '.join(parts)}"


def summary_text(clip: Clip) -> str:
    """One line about the selected video: length, score line, result, tags."""
    parts = []
    stats = library.summary(clip)
    if clip.seconds:
        parts.append(clock(clip.seconds))
    if stats:
        if "goals" in stats:
            parts.append(t("summary.gas", goals=stats["goals"], assists=stats["assists"], saves=stats["saves"]))
        else:
            parts.append(t("summary.kda", kills=stats["kills"], deaths=stats["deaths"], assists=stats["assists"]))
        if stats["result"]:
            parts.append(t(f"summary.{stats['result']}"))
    if clip.tags:
        parts.append(t("summary.tags", tags=", ".join(clip.tags)))
    return "     ".join(parts)


def placeholder(game: str) -> QPixmap:
    pixmap = QPixmap(THUMB)
    pixmap.fill(QColor(theme.current["panel"]))
    painter = QPainter(pixmap)
    painter.setPen(QColor(theme.current["line"]))
    font = QFont("Segoe UI", 44)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, game[:1].upper())
    painter.end()
    return pixmap


class _Signals(QObject):
    done = Signal(str, str)  # video path, thumbnail path ("" if none)


class _ReelSignals(QObject):
    progress = Signal(float)
    done = Signal(str, str)


class _Thumbnailer(QRunnable):
    def __init__(self, path: Path, signals: _Signals):
        super().__init__()
        self.path, self.signals = path, signals

    def run(self) -> None:
        thumb = library.thumbnail(self.path, CACHE)
        try:
            self.signals.done.emit(str(self.path), str(thumb or ""))
        except RuntimeError:
            pass  # the window was closed while we worked


class LibraryWindow(QDialog):
    def __init__(self, parent: QWidget | None = None, root: Path | None = None):
        super().__init__(parent)
        self.root = root or clips.ROOT
        self.clips: list[Clip] = []
        self.items: dict[str, QListWidgetItem] = {}
        self.setWindowTitle(t("library.title"))
        self.resize(1160, 700)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)  # thumbnails must not compete with a game for CPU
        self.signals = _Signals(self)
        self.signals.done.connect(self._thumb_ready)

        self.game, self.event, self.days, self.kind = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        for label, value in (("library.all_time", None), ("library.today", 1), ("library.week", 7), ("library.month", 30)):
            self.days.addItem(t(label), value)
        for label, value in (("library.everything", None), ("library.clips", "clip"), ("library.recordings", "recording")):
            self.kind.addItem(t(label), value)
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("library.search"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(210)
        self.search.textChanged.connect(self._filter)
        self.favorites = QCheckBox(t("library.favorites"))
        self.favorites.setToolTip(t("library.fav_hint"))
        self.favorites.toggled.connect(self._filter)
        filters = QHBoxLayout()
        filters.setContentsMargins(20, 16, 20, 10)
        filters.setSpacing(10)
        filters.addWidget(self.search)
        for combo in (self.game, self.event, self.days, self.kind):
            combo.setMinimumWidth(130)
            combo.currentIndexChanged.connect(self._filter)
            filters.addWidget(combo)
        filters.addWidget(self.favorites)
        filters.addStretch(1)
        filters.addWidget(self.count)

        self.grid = QListWidget()
        self.grid.setObjectName("grid")
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(THUMB)
        self.grid.setGridSize(QSize(THUMB.width() + 28, THUMB.height() + 66))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setWordWrap(True)
        self.grid.setUniformItemSizes(True)
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.grid.itemDoubleClicked.connect(lambda _: self._play())
        self.empty = QLabel()
        self.empty.setObjectName("hint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status = QLabel()
        self.status.setObjectName("muted")
        self.buttons: list[QPushButton] = []
        actions = QHBoxLayout()
        actions.setContentsMargins(20, 10, 20, 16)
        actions.setSpacing(10)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setContentsMargins(22, 8, 22, 0)
        for key, handler, name in (
            ("library.play", self._play, "primary"),
            ("library.favorite", self._favorite, ""),
            ("library.tags", self._tags, ""),
            ("library.trim", self._trim, ""),
            ("library.export", self._export, ""),
            ("library.show", self._show, ""),
            ("library.delete", self._delete, "danger"),
        ):
            button = QPushButton(t(key))
            button.setObjectName(name)
            button.clicked.connect(handler)
            self.buttons.append(button)
            actions.addWidget(button)
        self.reel = QPushButton(t("library.reel"))
        self.reel.setToolTip(t("library.reel_hint"))
        self.reel.clicked.connect(self._reel)
        actions.addWidget(self.reel)
        actions.addStretch(1)
        actions.addWidget(self.status, 1)
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(filters)
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.empty, 1)
        layout.addWidget(self.summary)
        layout.addLayout(actions)
        QShortcut(QKeySequence(Qt.Key.Key_F5), self, self.reload)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self._delete)
        self.reload()

    # -- contents ----------------------------------------------------------------

    def reload(self, select: Path | None = None) -> None:
        self.clips = library.scan(self.root)
        chosen = (self.game.currentData(), self.event.currentData())
        for combo, first, values, label in (
            (self.game, "library.all_games", sorted({c.game for c in self.clips}, key=str.lower), str),
            (self.event, "library.all_events", sorted({e for c in self.clips for e in c.event_types}), lambda e: t(f"event.{e}")),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(t(first), None)
            for value in values:
                combo.addItem(label(value), value)
            combo.blockSignals(False)
        self.game.setCurrentIndex(max(0, self.game.findData(chosen[0])))
        self.event.setCurrentIndex(max(0, self.event.findData(chosen[1])))

        self.grid.clear()
        self.items.clear()
        for clip in self.clips:
            item = QListWidgetItem(QIcon(placeholder(clip.game)), caption(clip))
            item.setData(Qt.ItemDataRole.UserRole, clip)
            item.setToolTip(str(clip.path))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.grid.addItem(item)
            self.items[str(clip.path)] = item
            self.pool.start(_Thumbnailer(clip.path, self.signals))
            if select is not None and clip.path == select:
                self.grid.setCurrentItem(item)
        self._filter()

    def _thumb_ready(self, video: str, thumb: str) -> None:
        item = self.items.get(video)
        if item is not None and thumb:
            pixmap = QPixmap(thumb).scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x, y = (pixmap.width() - THUMB.width()) // 2, (pixmap.height() - THUMB.height()) // 2
            item.setIcon(QIcon(pixmap.copy(x, y, THUMB.width(), THUMB.height())))

    def _filter(self) -> None:
        shown, total = 0, 0
        for i in range(self.grid.count()):
            item = self.grid.item(i)
            clip = item.data(Qt.ItemDataRole.UserRole)
            visible = library.matches(clip, self.game.currentData(), self.event.currentData(), self.days.currentData(),
                                      self.kind.currentData(), text=self.search.text(), favorite=self.favorites.isChecked())
            item.setHidden(not visible)
            if visible:
                shown, total = shown + 1, total + clip.size
            elif item.isSelected():
                item.setSelected(False)
        self.count.setText(t("library.count", count=shown, size=size_text(total)) if shown else "")
        self.grid.setVisible(shown > 0)
        self.empty.setVisible(shown == 0)
        self.empty.setText(t("library.no_match" if self.clips else "library.empty"))
        self._selection_changed()

    def selected(self) -> Clip | None:
        items = self.grid.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _selection_changed(self) -> None:
        clip = self.selected()
        for button in self.buttons:
            button.setEnabled(clip is not None)
        self.summary.setText(summary_text(clip) if clip else "")
        self.buttons[1].setText(t("library.unfavorite" if clip and clip.favorite else "library.favorite"))

    def _favorite(self) -> None:
        if clip := self.selected():
            library.set_meta(clip.path, favorite=not clip.favorite)
            self.reload(clip.path)

    def _tags(self) -> None:
        if clip := self.selected():
            text, ok = QInputDialog.getText(self, t("library.tags"), t("library.tags_prompt"), text=", ".join(clip.tags))
            if ok:
                library.set_meta(clip.path, tags=text.replace("،", ",").split(","))  # Arabic comma too
                self.reload(clip.path)

    # -- actions -----------------------------------------------------------------

    def _play(self) -> None:
        if clip := self.selected():
            os.startfile(clip.path)

    def _show(self) -> None:
        if clip := self.selected():
            subprocess.Popen(["explorer", "/select,", str(clip.path)])

    def _attempt(self, work, done_key: str) -> None:
        """Run a slow file action with a wait cursor; report the result in the status line."""
        # ponytail: stream copies of long recordings can take a few seconds on the UI thread;
        # move to the thread pool with a progress line if that becomes annoying.
        self.status.setText(t("library.working"))
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.repaint()
        try:
            result = work()
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            self.status.setText(t("library.failed", error=e))
            return
        finally:
            self.unsetCursor()
        self.reload()
        self.status.setText(t(done_key, path=result, name=Path(result).name))

    def _trim(self) -> None:
        if clip := self.selected():
            from .editor import EditorDialog  # imported here: the library opens without it

            dialog = EditorDialog(clip, self, self.root)
            dialog.exec()
            if dialog.finished_file is not None:
                made = dialog.finished_file
                self.reload(made if made.parent == clip.path.parent else clip.path)
                self.status.setText(t("library.trimmed", name=made.name))

    def visible(self) -> list[Clip]:
        return [self.grid.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.grid.count()) if not self.grid.item(i).isHidden()]

    def _reel(self) -> None:
        """Join the best moments of everything currently shown (so the filters choose the material)."""
        segments = highlights.pick(highlights.candidates(self.visible()))
        if not segments:
            return self.status.setText(t("reel.none"))
        shown = self.visible()
        name = shown[0].game if len({c.game for c in shown}) == 1 else "Laqta"
        length = sum(s.length for s in segments)
        signals = _ReelSignals(self)
        box = QProgressDialog(t("reel.working", count=len(segments), length=clock(length)), t("button.cancel"), 0, 1000, self)
        box.setWindowTitle(t("library.reel"))
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setMinimumDuration(0)
        box.setAutoClose(False)
        box.setAutoReset(False)
        job = highlights.ReelJob(segments, highlights.reel_path(name, self.root),
                                 signals.progress.emit, lambda path, why: signals.done.emit(str(path or ""), why))

        def done(path: str, why: str) -> None:
            box.close()
            box.deleteLater()
            if path:
                self.status.setText(t("reel.done", name=Path(path).name, folder=str(Path(path).parent)))
            else:
                self.status.setText(t("edit.cancelled") if why == "cancelled" else t("library.failed", error=why))

        signals.progress.connect(lambda fraction: box.setValue(int(fraction * 1000)))
        signals.done.connect(done)
        box.canceled.connect(job.cancel)
        self.status.setText("")
        self._reel_job = job  # keep it alive while it runs
        job.start()

    def _export(self) -> None:
        if clip := self.selected():
            self._attempt(lambda: library.export(clip.path, self.root), "library.exported")

    def _delete(self) -> None:
        if clip := self.selected():
            # Recycle Bin, so a slip is recoverable and no confirmation dialog is needed.
            if not QFile.moveToTrash(str(clip.path)):
                return self.status.setText(t("library.failed", error=clip.path.name))
            QFile.moveToTrash(str(clip.path.with_suffix(".json")))
            self.reload()
            self.status.setText(t("library.deleted"))
