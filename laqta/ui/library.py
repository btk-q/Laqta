"""Clip library window: browse, filter, play, trim, export, delete."""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QFile, QMimeData, QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QProgressDialog,
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListView, QListWidget,
    QButtonGroup, QFrame, QGridLayout, QListWidgetItem, QScrollArea, QMessageBox, QPushButton, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)  # fmt: skip

from ..game_detector.detector import load_games
from ..i18n import is_rtl, t
from ..storage import clips, config, highlights, library, share
from ..storage.library import Clip
from . import gameicons, icons, theme

THUMB = QSize(256, 144)
HEADING_ROLE = Qt.ItemDataRole.UserRole + 1  # (game, day) on the grid's heading rows
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


def day_text(day: datetime.date, today: datetime.date | None = None) -> str:
    days = ((today or datetime.date.today()) - day).days
    return t("library.today") if days == 0 else t("library.yesterday") if days == 1 else day.strftime("%Y-%m-%d")


def grouped(clips: list[Clip]) -> list[tuple[str, datetime.date, list[Clip]]]:
    """Videos as the window shows them: one group per game per day, newest day first, and
    inside a day the game played most recently first."""
    groups: dict[tuple[datetime.date, str], list[Clip]] = {}
    for clip in sorted(clips, key=lambda c: c.when, reverse=True):
        groups.setdefault((clip.when.date(), clip.game), []).append(clip)
    return [(game, day, members) for (day, game), members in groups.items()]


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
        elif stats["kills"] or stats["deaths"] or stats["assists"]:  # a line of zeros says nothing
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


def rounded(picture: QPixmap, size: QSize, radius: int) -> QPixmap:
    """The picture cropped to fill `size`, with rounded corners."""
    scaled = picture.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
    painter.end()
    return out


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


def event_kinds(clip: Clip) -> list[str]:
    """What happened in a video, most telling first, without the match boundaries."""
    kinds = list(dict.fromkeys(e["type"] for e in clip.events if e["type"] not in ("match_start", "match_end")))
    return sorted(kinds, key=lambda kind: -highlights.WEIGHTS.get(kind, 1.0))


class Card(QStyledItemDelegate):
    """Paints one video in the grid: the picture with its length on it, the game, when, and
    small tags for what happened. Plain list rows could not show any of that at a glance."""

    PAD, RADIUS = 10, 8
    SIZE = QSize(THUMB.width() + 2 * PAD, THUMB.height() + 2 * PAD + 74)

    HEADING = 44

    def sizeHint(self, option, index) -> QSize:
        if index.data(Qt.ItemDataRole.UserRole) is None:  # a heading takes a whole row of the grid
            return QSize(max(200, self.parent().viewport().width() - 28), self.HEADING)
        return self.SIZE

    def _heading(self, painter: QPainter, option, index, lead) -> None:
        game, day = index.data(HEADING_ROLE)
        c = theme.current
        painter.save()
        box = QRectF(option.rect).adjusted(self.PAD + 2, 12, -self.PAD, 0)
        name = QFont(option.font)
        name.setPixelSize(16)
        name.setWeight(QFont.Weight.DemiBold)
        picture = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(picture, QPixmap) and not picture.isNull():  # the game's own icon, before its name
            side = 24
            x = box.right() - side if lead == Qt.AlignmentFlag.AlignRight else box.x()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(x, box.center().y() - side / 2, side, side).toRect(), picture)
            box = box.adjusted(0, 0, -(side + 8), 0) if lead == Qt.AlignmentFlag.AlignRight else box.adjusted(side + 8, 0, 0, 0)
        painter.setFont(name)
        painter.setPen(QColor(c["text"]))
        painter.drawText(box, lead | Qt.AlignmentFlag.AlignVCenter, game)
        used = painter.fontMetrics().horizontalAdvance(game) + 12
        when = QFont(option.font)
        when.setPixelSize(13)
        painter.setFont(when)
        painter.setPen(QColor(c["muted"]))
        rest = box.adjusted(0, 0, -used, 0) if lead == Qt.AlignmentFlag.AlignRight else box.adjusted(used, 0, 0, 0)
        painter.drawText(rest, lead | Qt.AlignmentFlag.AlignVCenter, day)
        painter.restore()

    def paint(self, painter: QPainter, option, index) -> None:
        clip: Clip = index.data(Qt.ItemDataRole.UserRole)
        c = theme.current
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        lead = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
        if clip is None:
            return self._heading(painter, option, index, lead)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected or option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(QPen(QColor(c["accent"]), 1.5) if selected else Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["accent_dim"] if selected else c["panel"]))
            painter.drawRoundedRect(box, 12, 12)

        # the picture, with rounded corners
        thumb = QRectF(option.rect.x() + self.PAD, option.rect.y() + self.PAD, THUMB.width(), THUMB.height())
        path = QPainterPath()
        path.addRoundedRect(thumb, self.RADIUS, self.RADIUS)
        painter.setClipPath(path)
        painter.drawPixmap(thumb.toRect(), index.data(Qt.ItemDataRole.DecorationRole).pixmap(THUMB))
        painter.setClipping(False)

        small = QFont(option.font)
        small.setPixelSize(11)
        small.setBold(True)
        painter.setFont(small)
        metrics = painter.fontMetrics()
        if clip.seconds:  # length, on the picture, where video sites have taught everyone to look
            text = clock(clip.seconds)
            badge = QRectF(0, 0, metrics.horizontalAdvance(text) + 12, 20)
            badge.moveBottomRight(thumb.bottomRight() - QPointF(6, 6))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 190))
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        if clip.favorite:
            star = QRectF(thumb.x() + 6, thumb.y() + 6, 22, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["accent_fill"]))
            painter.drawEllipse(star)
            painter.setPen(QColor(c["on_accent"]))
            painter.drawText(star, Qt.AlignmentFlag.AlignCenter, "★")

        # words under it
        line = QRectF(thumb.x() + 2, thumb.bottom() + 8, thumb.width() - 4, 20)
        title = QFont(option.font)
        title.setPixelSize(14)
        title.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title)
        painter.setPen(QColor(c["text"]))
        painter.drawText(line, lead | Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(f"{t('card.' + clip.kind) if clip.kind in ('clip', 'recording') else clip.kind}  {clip.when:%H:%M}", Qt.TextElideMode.ElideRight, int(line.width())))
        sub = QFont(option.font)
        sub.setPixelSize(12)
        painter.setFont(sub)
        painter.setPen(QColor(c["muted"]))
        line.translate(0, 20)
        stats = library.summary(clip)
        if stats and (stats["kills"] or stats["deaths"] or stats["assists"]) and "goals" not in stats:
            about = f"{t('card.kda', kills=stats['kills'], deaths=stats['deaths'], assists=stats['assists'])}   {size_text(clip.size)}"
        else:
            about = size_text(clip.size)
        painter.drawText(line, lead | Qt.AlignmentFlag.AlignVCenter, about)

        # what happened, as small tags from the leading edge
        painter.setFont(small)
        metrics = painter.fontMetrics()
        x, y = (thumb.right() - 2 if rtl else thumb.x() + 2), line.bottom() + 5
        for kind in event_kinds(clip)[:3]:
            text = t(f"event.{kind}")
            width = metrics.horizontalAdvance(text) + 30
            if (x - width < thumb.x()) if rtl else (x + width > thumb.right()):
                break
            tag = QRectF(x - width if rtl else x, y, width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["line"]))
            painter.drawRoundedRect(tag, 10, 10)
            icons.draw(painter, kind, QRectF(tag.right() - 18 if rtl else tag.x() + 2, tag.y() + 2, 16, 16))
            painter.setPen(QColor(c["text"]))
            painter.drawText(tag.adjusted(4, 0, -20, 0) if rtl else tag.adjusted(20, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, text)
            x += -(width + 5) if rtl else width + 5
        painter.restore()


class EventStrip(QWidget):
    """The selected video as one line, with a symbol where each event happened. Click one to
    open the trim window on that moment."""

    picked = Signal(float)

    def __init__(self):
        super().__init__()
        self.events: list[dict] = []
        self.seconds = 0.0
        self.setFixedHeight(30)
        self.setMouseTracking(True)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # time runs left to right in every language

    def show_clip(self, clip: Clip | None) -> None:
        self.seconds = float(clip.seconds or 0) if clip else 0.0
        self.events = [e for e in (clip.events if clip else ()) if e["type"] not in ("match_start", "match_end")
                       and 0 <= e.get("offset", -1) <= self.seconds]
        self.setVisible(bool(self.events))
        self.update()

    def _x(self, event: dict) -> float:
        return 10 + (self.width() - 20) * event["offset"] / max(self.seconds, 0.001)

    def _near(self, x: float) -> dict | None:
        scored = [(abs(x - self._x(e)), i) for i, e in enumerate(self.events)]
        return self.events[min(scored)[1]] if scored and min(scored)[0] <= 10 else None

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.current["line"]))
        p.drawRoundedRect(QRectF(4, 13, self.width() - 8, 4), 2, 2)
        for event in self.events:
            icons.draw(p, event["type"], QRectF(self._x(event) - 9, 6, 18, 18))
        p.end()

    def mouseMoveEvent(self, event) -> None:
        found = self._near(event.position().x())
        self.setCursor(Qt.CursorShape.PointingHandCursor if found else Qt.CursorShape.ArrowCursor)
        self.setToolTip(f"{clock(found['offset'])}  {t('event.' + found['type'])}" if found else "")

    def mousePressEvent(self, event) -> None:
        if found := self._near(event.position().x()):
            self.picked.emit(float(found["offset"]))


class LibraryWindow(QDialog):
    PREVIEW = QSize(308, 173)

    def __init__(self, parent: QWidget | None = None, root: Path | None = None, webhook: str = "", on_settings=None,
                 settings_factory=None, games: dict | None = None):
        super().__init__(parent)
        self.root = root or clips.ROOT
        self.webhook = webhook  # "" = sharing to Discord is not set up, and the button is not shown
        self.clips: list[Clip] = []
        self.items: dict[str, QListWidgetItem] = {}
        self.thumbs: dict[str, str] = {}  # video -> its thumbnail file, for the side panel
        self.settings_factory = settings_factory  # makes the settings view; with it, this is the one main window
        self.embedded = None  # the settings view while it is open (edits live in it until Save or Cancel)
        # Game name -> program name, to put each game's own icon beside its name.
        self.exe_of = {(g.get("name") or g["exe"]).casefold(): g["exe"] for g in (games or load_games([], "session")).values()}
        self.icon_finder = gameicons.Finder(self)
        self.icon_finder.done.connect(lambda: self.reload(self.selected().path if self.selected() else None))
        self.setWindowTitle(t("library.title"))
        self.setWindowFlags(Qt.WindowType.Window)  # a real window: minimise, maximise, its own taskbar button
        self.resize(1280, 830)
        self.setMinimumSize(900, 560)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)  # thumbnails must not compete with a game for CPU
        self.signals = _Signals(self)
        self.signals.done.connect(self._thumb_ready)

        # -- header: what Laqta is, and what it is doing right now -------------------
        logo = QLabel()
        logo.setPixmap(QPixmap(str(Path(__file__).with_name("icon.png"))).scaled(
            34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand = QLabel("Laqta" if not is_rtl() else "لقطة")
        brand.setObjectName("brand")
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.state = QLabel()
        self.state.setObjectName("state")
        pill = QWidget()
        pill.setObjectName("pill")
        in_pill = QHBoxLayout(pill)
        in_pill.setContentsMargins(12, 6, 14, 6)
        in_pill.setSpacing(8)
        in_pill.addWidget(self.dot)
        in_pill.addWidget(self.state)
        header = QWidget()
        header.setObjectName("header")
        top = QHBoxLayout(header)
        top.setContentsMargins(20, 12, 20, 12)
        top.setSpacing(12)
        top.addWidget(logo)
        top.addWidget(brand)
        top.addSpacing(14)
        self.nav: dict[str, QPushButton] = {}
        if settings_factory:  # two views in one window: your clips, and the settings
            group = QButtonGroup(self)
            for name, label, slot in (("clips", "menu.library", self.show_clips), ("settings", "library.settings", self.show_settings)):
                button = self.nav[name] = QPushButton(t(label))
                button.setObjectName("nav")
                button.setCheckable(True)
                button.clicked.connect(slot)
                group.addButton(button)
                top.addWidget(button)
            self.nav["clips"].setChecked(True)
        top.addStretch(1)
        top.addWidget(pill)
        if on_settings and not settings_factory:
            settings = QPushButton(t("library.settings"))
            settings.clicked.connect(on_settings)
            top.addWidget(settings)
        self.set_state(t("status.idle"), "idle")

        # -- filters ---------------------------------------------------------------------
        self.game, self.event, self.days, self.kind = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        for label, value in (("library.all_time", None), ("library.today", 1), ("library.week", 7), ("library.month", 30)):
            self.days.addItem(t(label), value)
        for label, value in (("library.everything", None), ("library.clips", "clip"), ("library.recordings", "recording")):
            self.kind.addItem(t(label), value)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("library.search"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.textChanged.connect(self._filter)
        self.favorites = QCheckBox(t("library.favorites"))
        self.favorites.setToolTip(t("library.fav_hint"))
        self.favorites.toggled.connect(self._filter)
        filters = QHBoxLayout()
        filters.setContentsMargins(20, 14, 20, 6)
        filters.setSpacing(10)
        filters.addWidget(self.search, 1)
        for combo in (self.game, self.event, self.days, self.kind):
            combo.setMinimumWidth(150)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            combo.currentIndexChanged.connect(self._filter)
            filters.addWidget(combo)
        filters.addWidget(self.favorites)

        # -- the videos ------------------------------------------------------------------
        self.grid = QListWidget()
        self.grid.setObjectName("grid")
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setSelectionMode(QListView.SelectionMode.ExtendedSelection)  # Ctrl- and Shift-click pick several
        self.grid.setItemDelegate(Card(self.grid))
        self.grid.setIconSize(THUMB)
        self.grid.setSpacing(4)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setMouseTracking(True)  # for the hover highlight
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.grid.verticalScrollBar().valueChanged.connect(lambda _: self._want_thumbnails())
        self._asked: set[str] = set()  # videos whose picture has been asked for in this window
        self.grid.itemDoubleClicked.connect(lambda _: self._play())
        self.empty = QLabel()
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)

        # -- side panel: the selected video and everything you can do with it -------------
        self.preview = QLabel()
        self.preview.setObjectName("preview")
        self.preview.setFixedSize(self.PREVIEW)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel()
        self.title.setObjectName("gameName")
        self.title.setWordWrap(True)
        if is_rtl():  # a game's name is usually Latin text, which Qt would otherwise push to the left edge
            self.title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter)
        self.about = theme.lead(QLabel())
        self.about.setObjectName("muted")
        self.about.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.strip = EventStrip()
        self.strip.picked.connect(lambda at: self._play(at=max(0.0, at - 3)))  # a symbol: watch from just before it
        self.viewer = None  # the player window, made on first use
        self.buttons: list[QPushButton] = []
        made: dict[str, QPushButton] = {}
        for key, handler, name in (
            ("library.play", self._play, "primary"),
            ("library.favorite", self._favorite, ""),
            ("library.tags", self._tags, ""),
            ("library.trim", self._trim, ""),
            ("library.export", self._export, ""),
            ("library.show", self._show, ""),
            ("library.copy", self._copy, ""),
            ("library.post", self._post, ""),
            ("library.delete", self._delete, "danger"),
        ):
            button = made[key] = QPushButton(t(key))
            button.setObjectName(name)
            button.clicked.connect(handler)
            self.buttons.append(button)
        self.post_button = made["library.post"]
        self.post_button.setVisible(bool(webhook))
        made["library.play"].setMinimumHeight(38)
        made["library.trim"].setMinimumHeight(38)
        pairs = QGridLayout()
        pairs.setSpacing(8)
        for i, key in enumerate(("library.favorite", "library.tags", "library.export", "library.copy", "library.show", "library.post")):
            pairs.addWidget(made[key], i // 2, i % 2)
        chosen = QWidget()
        side = QVBoxLayout(chosen)
        side.setContentsMargins(16, 16, 16, 16)
        side.setSpacing(8)
        side.addWidget(self.preview)
        side.addSpacing(4)
        side.addWidget(self.title)
        side.addWidget(self.about)
        side.addWidget(self.summary)
        side.addWidget(self.strip)
        side.addSpacing(8)
        side.addWidget(made["library.play"])
        side.addWidget(made["library.trim"])
        side.addSpacing(6)
        side.addLayout(pairs)
        side.addStretch(1)
        side.addWidget(made["library.delete"])
        nothing = QLabel(t("library.pick"))
        nothing.setObjectName("empty")
        nothing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nothing.setWordWrap(True)
        nothing.setContentsMargins(24, 0, 24, 0)
        scroll = QScrollArea()  # on a short window the actions scroll instead of squeezing into each other
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(chosen)
        chosen.setObjectName("detailBody")
        self.panel = QStackedWidget()
        self.panel.setObjectName("detail")
        self.panel.setFixedWidth(self.PREVIEW.width() + 44)
        self.panel.addWidget(nothing)
        self.panel.addWidget(scroll)
        # Several videos selected: what can be done to all of them at once.
        many = QWidget()
        many.setObjectName("detailBody")
        group = QVBoxLayout(many)
        group.setContentsMargins(16, 24, 16, 16)
        group.setSpacing(8)
        self.many_title = QLabel()
        self.many_title.setObjectName("gameName")
        self.many_about = QLabel()
        self.many_about.setObjectName("muted")
        self.many_about.setWordWrap(True)
        group.addWidget(self.many_title)
        group.addWidget(self.many_about)
        group.addSpacing(10)
        for key, handler, name in (("library.favorite_all", self._favorite_all, ""), ("library.export_all", self._export_all, ""),
                                   ("library.reel_these", self._reel_selected, ""), ("library.delete_all", self._delete, "danger")):
            button = QPushButton(t(key))
            button.setObjectName(name)
            button.clicked.connect(handler)
            group.addWidget(button)
        group.addStretch(1)
        hint = QLabel(t("library.many_hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        group.addWidget(hint)
        self.panel.addWidget(many)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.grid, 1)
        body.addWidget(self.empty, 1)
        body.addWidget(self.panel)

        # -- footer --------------------------------------------------------------------------
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.reel = QPushButton(t("library.reel"))
        self.reel.setToolTip(t("library.reel_hint"))
        self.reel.clicked.connect(self._reel)
        footer = QWidget()
        footer.setObjectName("footer")
        bottom = QHBoxLayout(footer)
        bottom.setContentsMargins(20, 10, 20, 10)
        bottom.setSpacing(16)
        bottom.addWidget(self.count)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.reel)

        clips_page = QWidget()
        clips_page.setObjectName("page")
        inside = QVBoxLayout(clips_page)
        inside.setContentsMargins(0, 0, 0, 0)
        inside.setSpacing(0)
        inside.addLayout(filters)
        inside.addLayout(body, 1)
        inside.addWidget(footer)
        self.settings_page = QWidget()
        self.settings_page.setObjectName("page")
        self.settings_box = QVBoxLayout(self.settings_page)
        self.settings_box.setContentsMargins(0, 0, 0, 0)
        self.views = QStackedWidget()
        self.views.addWidget(clips_page)
        self.views.addWidget(self.settings_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.views, 1)
        QShortcut(QKeySequence(Qt.Key.Key_F5), self, self.reload)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self._delete)
        self.reload()

    # -- the two views -------------------------------------------------------------------

    def show_clips(self) -> None:
        self.views.setCurrentIndex(0)
        if self.nav:
            self.nav["clips"].setChecked(True)

    def show_settings(self) -> None:
        """Open the settings inside this window. Edits stay in the view (also while you look at
        your clips) until Save changes or Cancel."""
        if not self.settings_factory:
            return
        if self.embedded is None:
            self.embedded = view = self.settings_factory()
            view.setWindowFlags(Qt.WindowType.Widget)  # a part of this window, not a window of its own
            view.finished.connect(self._settings_finished)
            self.settings_box.addWidget(view)
            view.show()
        self.views.setCurrentIndex(1)
        self.nav["settings"].setChecked(True)

    def _settings_finished(self, result: int) -> None:
        view, self.embedded = self.embedded, None
        if view is not None:
            self.settings_box.removeWidget(view)
            view.deleteLater()
        self.show_clips()
        self.status.setText(t("library.saved") if result else "")

    def closeEvent(self, event) -> None:
        if self.viewer is not None:
            self.viewer.close()  # stops playback and releases the file
        super().closeEvent(event)

    def set_state(self, text: str, kind: str) -> None:
        """What Laqta is doing right now: "idle", "busy" (getting ready, or a problem) or "rec"."""
        color = {"idle": theme.current["muted"], "busy": theme.STATUS["warn"], "rec": theme.REC}[kind]
        self.dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self.state.setText(text)

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
        self.icon_finder.find([self.exe_of[c.game.casefold()] for c in self.clips if c.game.casefold() in self.exe_of])
        for game, day, members in grouped(self.clips):
            heading = QListWidgetItem()
            heading.setFlags(Qt.ItemFlag.NoItemFlags)  # not a video: cannot be selected
            heading.setData(HEADING_ROLE, (game, day_text(day)))
            if (exe := self.exe_of.get(game.casefold())) and (picture := gameicons.get(exe)):
                heading.setData(Qt.ItemDataRole.DecorationRole, picture)
            self.grid.addItem(heading)
            for clip in members:
                item = QListWidgetItem(QIcon(placeholder(clip.game)), caption(clip))
                item.setData(Qt.ItemDataRole.UserRole, clip)
                item.setToolTip(str(clip.path))
                self.grid.addItem(item)
                self.items[str(clip.path)] = item
                if select is not None and clip.path == select:
                    self.grid.setCurrentItem(item)
        self._filter()

    def _thumb_ready(self, video: str, thumb: str) -> None:
        item = self.items.get(video)
        if item is not None and thumb:
            self.thumbs[video] = thumb
            if (chosen := self.selected()) and str(chosen.path) == video:
                self._selection_changed()
            pixmap = QPixmap(thumb).scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x, y = (pixmap.width() - THUMB.width()) // 2, (pixmap.height() - THUMB.height()) // 2
            item.setIcon(QIcon(pixmap.copy(x, y, THUMB.width(), THUMB.height())))

    def _filter(self) -> None:
        shown, total, heading = 0, 0, None
        for i in range(self.grid.count()):
            item = self.grid.item(i)
            clip = item.data(Qt.ItemDataRole.UserRole)
            if clip is None:  # a heading: shown only if something under it is
                heading = item
                heading.setHidden(True)
                continue
            visible = library.matches(clip, self.game.currentData(), self.event.currentData(), self.days.currentData(),
                                      self.kind.currentData(), text=self.search.text(), favorite=self.favorites.isChecked())
            item.setHidden(not visible)
            if visible:
                shown, total = shown + 1, total + clip.size
                heading.setHidden(False)
            elif item.isSelected():
                item.setSelected(False)
        free = clips.free_bytes(self.root)
        room = t("library.free", free=f"{free / 1024**3:.0f}") if free is not None else ""
        self.count.setText("     ".join(filter(None, (t("library.count", count=shown, size=size_text(total)) if shown else "", room))))
        self.grid.setVisible(shown > 0)
        self.empty.setVisible(shown == 0)
        self.empty.setText(t("library.no_match" if self.clips else "library.empty"))
        self._selection_changed()
        QTimer.singleShot(0, self._want_thumbnails)  # once the grid has laid itself out

    def _want_thumbnails(self) -> None:
        """Make pictures only for the videos on screen (and a screenful either side). With hundreds
        of videos, making them all at once left the ones being looked at waiting behind the rest."""
        view = self.grid.viewport().rect()
        near = view.adjusted(0, -view.height(), 0, view.height())
        for path, item in self.items.items():
            if path not in self._asked and not item.isHidden() and near.intersects(self.grid.visualItemRect(item)):
                self._asked.add(path)
                self.pool.start(_Thumbnailer(Path(path), self.signals))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._want_thumbnails()

    def selected(self) -> Clip | None:
        """The one video selected, or None when there are none or several."""
        chosen = self.chosen()
        return chosen[0] if len(chosen) == 1 else None

    def chosen(self) -> list[Clip]:
        """Every selected video, in the order shown."""
        rows = sorted(self.grid.selectedItems(), key=self.grid.row)
        return [item.data(Qt.ItemDataRole.UserRole) for item in rows if item.data(Qt.ItemDataRole.UserRole) and not item.isHidden()]

    def _selection_changed(self) -> None:
        chosen = self.chosen()
        clip = chosen[0] if len(chosen) == 1 else None
        for button in self.buttons:
            button.setEnabled(clip is not None)
        self.panel.setCurrentIndex(2 if len(chosen) > 1 else 1 if clip else 0)
        if len(chosen) > 1:
            self.many_title.setText(t("library.many", count=len(chosen)))
            games = sorted({c.game for c in chosen}, key=str.lower)
            self.many_about.setText(f"{size_text(sum(c.size for c in chosen))}\n{', '.join(games[:4])}{'…' if len(games) > 4 else ''}")
        self.strip.show_clip(clip)
        # One fact per line in the narrow panel; the length moves up beside the date.
        facts = summary_text(clip).split("     ") if clip else []
        self.summary.setText("\n".join(facts[1:] if clip and clip.seconds else facts))
        self.summary.setVisible(bool(self.summary.text()))
        if clip:
            self.title.setText(clip.game)
            length = f"{clock(clip.seconds)}   " if clip.seconds else ""
            self.about.setText(f"{when_text(clip.when)}\n{length}{size_text(clip.size)}")
            picture = QPixmap(self.thumbs.get(str(clip.path), "")) if str(clip.path) in self.thumbs else placeholder(clip.game)
            self.preview.setPixmap(rounded(picture, self.PREVIEW, 8))
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

    def _play(self, at: float | bool = 0.0) -> None:
        if clip := self.selected():
            try:
                from .player import PlayerWindow  # imported here: Qt Multimedia loads only when a video is played
            except ImportError:  # a broken install: still let the video be watched
                return os.startfile(clip.path)

            if self.viewer is None:
                self.viewer = PlayerWindow(self)
                self.viewer.trim.connect(lambda chosen, where: self._trim(where))
                self.viewer.marks_changed.connect(lambda chosen: self.reload(chosen.path))
            stats = summary_text(clip).split("     ")
            self.viewer.show_clip(clip, "     ".join([when_text(clip.when), *stats[1:]]), at if isinstance(at, float) else 0.0)

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

    def _trim(self, at: float | bool | None = None) -> None:
        if clip := self.selected():
            from .editor import EditorDialog  # imported here: the library opens without it

            if self.viewer is not None:
                self.viewer.player.pause()

            # `at`: an event picked on the strip. (A button click passes False, which means "no event".)
            dialog = EditorDialog(clip, self, self.root, at=at if isinstance(at, float) else None)
            dialog.exec()
            if dialog.finished_file is not None:
                made = dialog.finished_file
                self.reload(made if made.parent == clip.path.parent else clip.path)
                self.status.setText(t("library.trimmed", name=made.name))

    def visible(self) -> list[Clip]:
        items = (self.grid.item(i) for i in range(self.grid.count()))
        return [item.data(Qt.ItemDataRole.UserRole) for item in items if not item.isHidden() and item.data(Qt.ItemDataRole.UserRole)]

    def _reel(self, material: list[Clip] | None = None) -> None:
        """Join the best moments of the chosen videos, or of everything shown (so the filters choose)."""
        shown = material or self.visible()
        segments = highlights.pick(highlights.candidates(shown))
        if not segments:
            return self.status.setText(t("reel.none"))
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

    def _copy(self) -> None:
        """Put the file itself on the clipboard, as Explorer's Copy does: paste into a chat or a folder."""
        if clip := self.selected():
            data = QMimeData()
            data.setUrls([QUrl.fromLocalFile(str(clip.path))])
            QGuiApplication.clipboard().setMimeData(data)
            self.status.setText(t("library.copied", name=clip.path.name))

    def set_webhook(self, webhook: str) -> None:
        self.webhook = webhook
        self.post_button.setVisible(bool(webhook))

    def _post(self) -> None:
        """Send one clip to the user's Discord channel. The only thing in Laqta that leaves the PC."""
        clip = self.selected()
        if clip is None or not self.webhook:
            return
        size = clip.path.stat().st_size
        if size > share.DISCORD_LIMIT:  # a remux would not shrink it: say what will, before asking anything
            return self.status.setText(t("library.post_big", name=clip.path.name, size=f"{size / 1024 / 1024:.0f}"))
        asked = QMessageBox.question(self, t("library.post_title"), t("library.post_confirm", name=clip.path.name))
        if asked != QMessageBox.StandardButton.Yes:
            return

        def work() -> Path:
            share.post(self.webhook, share.for_sharing(clip.path, self.root), clip.game)
            return clip.path

        self._attempt(work, "library.posted")

    def _export(self) -> None:
        if clip := self.selected():
            self._attempt(lambda: library.export(clip.path, self.root), "library.exported")

    def _delete(self) -> None:
        """To the Recycle Bin, so a slip is recoverable and no confirmation is needed. Several at once
        ask first all the same: one stray Ctrl+A should not empty the window."""
        chosen = self.chosen()
        if not chosen:
            return
        if len(chosen) > 1:
            asked = QMessageBox.question(self, t("library.delete_all"), t("library.delete_confirm", count=len(chosen), size=size_text(sum(c.size for c in chosen))))
            if asked != QMessageBox.StandardButton.Yes:
                return
        if self.viewer is not None and self.viewer.clip in chosen:
            self.viewer.close()  # the player holds the file open: let go of it first
        failed = []
        for clip in chosen:
            if QFile.moveToTrash(str(clip.path)):
                QFile.moveToTrash(str(clip.path.with_suffix(".json")))
            else:
                failed.append(clip.path.name)
        self.reload()
        if failed:
            return self.status.setText(t("library.failed", error=", ".join(failed[:3])))
        self.status.setText(t("library.deleted") if len(chosen) == 1 else t("library.deleted_many", count=len(chosen)))

    def _favorite_all(self) -> None:
        """Make them all favourites; if they already all are, take the mark off."""
        chosen = self.chosen()
        mark = not all(c.favorite for c in chosen)
        for clip in chosen:
            library.set_meta(clip.path, favorite=mark)
        self.reload()
        self.status.setText(t("library.favorited" if mark else "library.unfavorited", count=len(chosen)))

    def _export_all(self) -> None:
        chosen = self.chosen()
        if not chosen:
            return

        def work() -> Path:
            made = [library.export(clip.path, self.root) for clip in chosen]
            return made[-1].parent

        self._attempt(work, "library.exported_many")

    def _reel_selected(self) -> None:
        self._reel(self.chosen())
