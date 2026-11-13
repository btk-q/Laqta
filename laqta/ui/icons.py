"""One small drawn symbol and colour per event type, so a kill, a death and a goal can be told
apart at a glance on a timeline. Drawn with the painter (no image files, sharp at any size).

Colour groups: green = you scored on someone, red = it went against you, blue = you helped,
amber = objectives and goals, gold = results, pink = your laugh, cyan = your shout, accent = your own key.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from . import theme

GREEN, RED, BLUE, AMBER, GOLD, PINK, GREY, CYAN = "#2FBF71", "#F0485C", "#4C9BF5", "#F59E0B", "#EAB308", "#EC5FA8", "#7C7A8C", "#14B8C4"
COLORS = {
    "kill": GREEN, "headshot": GREEN, "multikill": GREEN, "ace": GREEN, "first_blood": GREEN, "demolition": GREEN,
    "death": RED, "lose": GREY, "round_lose": GREY,
    "assist": BLUE, "save": BLUE,
    "objective": AMBER, "goal": AMBER,
    "win": GOLD, "round_win": GOLD,
    "shout": CYAN, "laugh": PINK,
}  # fmt: skip


def color(kind: str) -> str:
    return COLORS.get(kind, theme.current["accent_fill"])  # manual, replay and anything new: the user's accent


def _star(c: QPointF, outer: float, inner: float) -> QPolygonF:
    points = []
    for i in range(10):
        r, a = (outer if i % 2 == 0 else inner), -math.pi / 2 + i * math.pi / 5
        points.append(QPointF(c.x() + r * math.cos(a), c.y() + r * math.sin(a)))
    return QPolygonF(points)


def draw(p: QPainter, kind: str, box: QRectF) -> None:
    """A filled circle in the event's colour with its white symbol inside `box` (a square)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color(kind)))
    p.drawEllipse(box)
    # Symbols are designed on a 16x16 grid centred on (8, 8).
    p.translate(box.x(), box.y())
    p.scale(box.width() / 16, box.height() / 16)
    white = QColor("#FFFFFF")
    pen = QPen(white, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    mid = QPointF(8, 8)

    def lines(*pairs) -> None:
        for (x0, y0), (x1, y1) in pairs:
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def solid(shape) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawPolygon(shape) if isinstance(shape, QPolygonF) else p.drawPath(shape)

    if kind in ("kill", "headshot"):  # crosshair; a headshot has its centre filled
        p.drawEllipse(mid, 3.2, 3.2)
        lines(((8, 2.8), (8, 5)), ((8, 11), (8, 13.2)), ((2.8, 8), (5, 8)), ((11, 8), (13.2, 8)))
        if kind == "headshot":
            p.setBrush(white)
            p.drawEllipse(mid, 1.2, 1.2)
    elif kind == "multikill":  # two targets
        p.drawEllipse(QPointF(6.2, 8), 2.6, 2.6)
        p.drawEllipse(QPointF(9.8, 8), 2.6, 2.6)
    elif kind == "ace":
        solid(_star(mid, 5, 2.1))
    elif kind == "first_blood":  # a drop
        path = QPainterPath(QPointF(8, 3))
        path.cubicTo(QPointF(11.5, 7.5), QPointF(12, 9.5), QPointF(8, 12.6))
        path.cubicTo(QPointF(4, 9.5), QPointF(4.5, 7.5), QPointF(8, 3))
        solid(path)
    elif kind == "death":
        pen.setWidthF(1.9)
        p.setPen(pen)
        lines(((5, 5), (11, 11)), ((11, 5), (5, 11)))
    elif kind == "assist":
        pen.setWidthF(1.9)
        p.setPen(pen)
        lines(((8, 4.5), (8, 11.5)), ((4.5, 8), (11.5, 8)))
    elif kind == "objective":  # a flag
        lines(((5.2, 3.5), (5.2, 12.5)))
        solid(QPolygonF([QPointF(5.9, 3.8), QPointF(11.8, 5.9), QPointF(5.9, 8.2)]))
    elif kind == "goal":  # a ball
        p.drawEllipse(mid, 4.2, 4.2)
        p.setBrush(white)
        p.drawEllipse(mid, 1.4, 1.4)
    elif kind == "save":  # a shield
        path = QPainterPath(QPointF(8, 3.2))
        path.lineTo(12, 4.8)
        path.cubicTo(QPointF(12, 9), QPointF(10.5, 11.5), QPointF(8, 12.9))
        path.cubicTo(QPointF(5.5, 11.5), QPointF(4, 9), QPointF(4, 4.8))
        path.closeSubpath()
        solid(path)
    elif kind == "demolition":  # a burst
        for i in range(8):
            a = i * math.pi / 4
            lines(((8 + 1.8 * math.cos(a), 8 + 1.8 * math.sin(a)), (8 + 4.8 * math.cos(a), 8 + 4.8 * math.sin(a))))
    elif kind in ("win", "round_win"):  # a crown
        solid(QPolygonF([QPointF(3.6, 11.6), QPointF(3.6, 5.4), QPointF(6.2, 8), QPointF(8, 4.2), QPointF(9.8, 8),
                         QPointF(12.4, 5.4), QPointF(12.4, 11.6)]))
    elif kind in ("lose", "round_lose"):  # a down arrow
        pen.setWidthF(1.9)
        p.setPen(pen)
        lines(((8, 4), (8, 11.5)), ((4.8, 8.4), (8, 11.6)), ((11.2, 8.4), (8, 11.6)))
    elif kind == "replay":  # rewind
        solid(QPolygonF([QPointF(8, 4.5), QPointF(8, 11.5), QPointF(3.6, 8)]))
        solid(QPolygonF([QPointF(12.4, 4.5), QPointF(12.4, 11.5), QPointF(8, 8)]))
    elif kind == "shout":
        pen.setWidthF(2.0)
        p.setPen(pen)
        lines(((8, 3.8), (8, 9)))
        p.setBrush(white)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(8, 11.8), 1.2, 1.2)
    elif kind == "laugh":  # a laughing face: two eyes and an open smile
        p.setBrush(white)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(5.8, 6.2), 1.05, 1.05)
        p.drawEllipse(QPointF(10.2, 6.2), 1.05, 1.05)
        path = QPainterPath(QPointF(4.4, 8.6))
        path.cubicTo(QPointF(5.2, 13.4), QPointF(10.8, 13.4), QPointF(11.6, 8.6))
        path.closeSubpath()
        p.drawPath(path)
    else:  # manual, and anything without its own symbol: a bookmark
        solid(QPolygonF([QPointF(5, 3.6), QPointF(11, 3.6), QPointF(11, 12.4), QPointF(8, 10), QPointF(5, 12.4)]))
    p.restore()


def pixmap(kind: str, size: int = 18) -> QPixmap:
    """The same badge as a picture, for labels and legends."""
    out = QPixmap(size * 2, size * 2)  # drawn at twice the size so it stays sharp on scaled displays
    out.setDevicePixelRatio(2)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    draw(painter, kind, QRectF(0, 0, size, size))
    painter.end()
    return out
