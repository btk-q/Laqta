"""How one video is shown: its card in the grid, the strip of event symbols, and the words
and pictures that go with them. No windows here."""

from __future__ import annotations

import datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QWidget

from ..i18n import t
from ..storage import highlights, library
from ..storage.library import Clip
from . import icons, theme


THUMB = QSize(256, 144)
HEADING_ROLE = Qt.ItemDataRole.UserRole + 1  # (game, day) on the grid's heading rows
FLAT_ROLE = Qt.ItemDataRole.UserRole + 2  # in a list sorted by size or length: the card names its game and day


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


ORDERS = ("newest", "oldest", "biggest", "longest")


def grouped(clips: list[Clip], order: str = "newest") -> list[tuple[str, datetime.date | None, list[Clip]]]:
    """Videos as the window shows them. By date: one group per game per day (newest or oldest
    day first, and within a day by time). By size or length: one list, biggest or longest first,
    under one heading that says so (the day is then None)."""
    if order in ("biggest", "longest"):
        key = (lambda c: c.size) if order == "biggest" else (lambda c: c.seconds or 0)
        ranked = sorted(clips, key=lambda c: (-key(c), -c.when.timestamp()))
        return [(t(f"library.sort_{order}"), None, ranked)] if ranked else []
    groups: dict[tuple[datetime.date, str], list[Clip]] = {}
    for clip in sorted(clips, key=lambda c: c.when, reverse=order != "oldest"):
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
        day = day or ""
        c = theme.current
        painter.save()
        box = QRectF(option.rect).adjusted(self.PAD + 2, 12, -self.PAD, 0)
        name = QFont(option.font)
        name.setPixelSize(16)
        name.setWeight(QFont.Weight.DemiBold)
        picture = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(picture, QPixmap) and not picture.isNull():  # the game's own icon, before its name
            side = 24
            x = box.right() - side if lead & Qt.AlignmentFlag.AlignRight else box.x()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(x, box.center().y() - side / 2, side, side).toRect(), picture)
            box = box.adjusted(0, 0, -(side + 8), 0) if lead & Qt.AlignmentFlag.AlignRight else box.adjusted(side + 8, 0, 0, 0)
        painter.setFont(name)
        painter.setPen(QColor(c["text"]))
        painter.drawText(box, lead | Qt.AlignmentFlag.AlignVCenter, game)
        used = painter.fontMetrics().horizontalAdvance(game) + 12
        when = QFont(option.font)
        when.setPixelSize(13)
        painter.setFont(when)
        painter.setPen(QColor(c["muted"]))
        rest = box.adjusted(0, 0, -used, 0) if lead & Qt.AlignmentFlag.AlignRight else box.adjusted(used, 0, 0, 0)
        painter.drawText(rest, lead | Qt.AlignmentFlag.AlignVCenter, day)
        painter.restore()

    def paint(self, painter: QPainter, option, index) -> None:
        clip: Clip = index.data(Qt.ItemDataRole.UserRole)
        c = theme.current
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        # Absolute: a Latin game name in Arabic mode would otherwise be pushed to the other side.
        lead = (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute) if rtl else Qt.AlignmentFlag.AlignLeft
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

        if self.parent().property("ticking"):  # the clips window's Select mode: a tick box in the top corner
            tick = QRectF(0, 0, 24, 24)
            tick.moveTopRight(thumb.topRight() + QPointF(-6, 6))
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor(c["accent_fill"]) if selected else QColor(0, 0, 0, 120))
            painter.drawRoundedRect(tick, 6, 6)
            if selected:  # drawn as lines, so it does not depend on the font having a ✓
                painter.setPen(QPen(QColor(c["on_accent"]), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                x, y = tick.x(), tick.y()
                painter.drawPolyline([QPointF(x + 6, y + 12.5), QPointF(x + 10.5, y + 17), QPointF(x + 18, y + 8)])

        # words under it
        line = QRectF(thumb.x() + 2, thumb.bottom() + 8, thumb.width() - 4, 20)
        title = QFont(option.font)
        title.setPixelSize(14)
        title.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title)
        painter.setPen(QColor(c["text"]))
        painter.drawText(line, lead | Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(f"{clip.game}  {when_text(clip.when)}" if index.data(FLAT_ROLE) else f"{t('card.' + clip.kind) if clip.kind in ('clip', 'recording') else clip.kind}  {clip.when:%H:%M}", Qt.TextElideMode.ElideRight, int(line.width())))
        sub = QFont(option.font)
        sub.setPixelSize(12)
        painter.setFont(sub)
        painter.setPen(QColor(c["muted"]))
        line.translate(0, 20)
        stats = library.summary(clip)
        champion = library.match_info(clip).get("champion")
        if stats and (stats["kills"] or stats["deaths"] or stats["assists"]) and "goals" not in stats:
            kda = t('card.kda', kills=stats['kills'], deaths=stats['deaths'], assists=stats['assists'])
            about = f"{champion}   {kda}" if champion else f"{kda}   {size_text(clip.size)}"
        elif champion:
            about = f"{champion}   {size_text(clip.size)}"
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
