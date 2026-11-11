"""Trim and export window: a filmstrip with draggable start/end handles and event markers,
a still preview of the moment under your hand, and the export presets.

The preview is a still frame, not playback: a video player would add about 150 MB to the app.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from ..i18n import t
from ..storage import config, export, library
from ..storage.library import Clip
from . import icons, theme

CACHE = config.DIR / "thumbs"
MIN_LENGTH = 0.5  # seconds a selection may not go below
BOUNDARIES = ("match_start", "match_end")


def stamp(seconds: float) -> str:
    """0:07.5, 12:34.0, 1:02:05.0"""
    seconds = max(0.0, round(seconds, 1))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:04.1f}" if hours else f"{int(minutes)}:{secs:04.1f}"


class Selection:
    """The chosen stretch of a video and the arithmetic of the strip that shows it. No Qt."""

    def __init__(self, duration: float):
        self.duration = max(duration, MIN_LENGTH)
        self.start, self.end = 0.0, self.duration
        self.view = (0.0, self.duration)  # the stretch the strip is showing; narrower when zoomed in

    def zoom(self) -> None:
        """Show the selection with as much again on each side, so short cuts in long videos are workable."""
        length = max(self.end - self.start, 3.0)
        lo, hi = max(0.0, self.start - length), min(self.duration, self.end + length)
        self.view = (lo, max(hi, lo + MIN_LENGTH))

    def unzoom(self) -> None:
        self.view = (0.0, self.duration)

    @property
    def zoomed(self) -> bool:
        return self.view != (0.0, self.duration)

    def set_start(self, value: float) -> None:
        self.start = max(0.0, min(value, self.end - MIN_LENGTH))

    def set_end(self, value: float) -> None:
        self.end = min(self.duration, max(value, self.start + MIN_LENGTH))

    def around(self, moment: float, before: float = 10.0, after: float = 5.0) -> None:
        """Select a clip-sized stretch around an event."""
        self.start, self.end = 0.0, self.duration
        self.set_end(moment + after)
        self.set_start(moment - before)

    def time_at(self, x: float, width: float) -> float:
        lo, hi = self.view
        return max(lo, min(hi, lo + (hi - lo) * x / max(width, 1.0)))

    def x_at(self, seconds: float, width: float) -> float:
        """Pixel for a moment; moments outside the view land just off either edge."""
        lo, hi = self.view
        return width * (seconds - lo) / (hi - lo)

    def handle_near(self, x: float, width: float, reach: float = 12.0) -> str | None:
        """'start' or 'end' if x is within reach of that handle (the nearer one when both are)."""
        gaps = {"start": abs(x - self.x_at(self.start, width)), "end": abs(x - self.x_at(self.end, width))}
        nearest = min(gaps, key=gaps.get)
        return nearest if gaps[nearest] <= reach else None

    def marker_near(self, x: float, width: float, markers: list[dict], reach: float = 7.0) -> dict | None:
        scored = [(abs(x - self.x_at(m["offset"], width)), i) for i, m in enumerate(markers)]
        return markers[min(scored)[1]] if scored and min(scored)[0] <= reach else None


class _Signals(QObject):
    still = Signal(int, str)  # tile index (or -token for the preview), image path
    progress = Signal(float)
    done = Signal(str, str)  # output path ("" on failure), reason


class _Still(QRunnable):
    def __init__(self, path: Path, seconds: float, width: int, tag: int, signals: _Signals):
        super().__init__()
        self.args, self.tag, self.signals = (path, seconds, width), tag, signals

    def run(self) -> None:
        image = export.frame(*self.args, CACHE)
        try:
            self.signals.still.emit(self.tag, str(image or ""))
        except RuntimeError:
            pass  # the window closed while ffmpeg worked


BAND = 22  # height of the row of event symbols above the filmstrip


class Timeline(QWidget):
    changed = Signal()  # the selection moved
    seek = Signal(float)  # show this moment in the preview
    rezoomed = Signal()  # the strip shows a different stretch: its tiles must be fetched again

    def __init__(self, duration: float, markers: list[dict]):
        super().__init__()
        self.sel = Selection(duration)
        self.markers = [m for m in markers if 0 <= m.get("offset", -1) <= duration and m["type"] not in BOUNDARIES]
        self.tiles: dict[int, QPixmap] = {}
        self.count = max(6, min(24, int(duration // 2) or 6))
        self.playhead = 0.0
        self._drag: str | None = None
        self._active = "start"  # which handle the arrow keys move
        self.setMinimumHeight(106)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # time runs left to right in every language
        self.setAccessibleName(t("edit.timeline"))

    def tile_times(self) -> list[float]:
        lo, hi = self.sel.view
        return [lo + (i + 0.5) * (hi - lo) / self.count for i in range(self.count)]

    def set_zoom(self, on: bool) -> None:
        (self.sel.zoom if on else self.sel.unzoom)()
        self.tiles.clear()  # the strip now shows different moments
        self.update()
        self.rezoomed.emit()

    def set_tile(self, index: int, pixmap: QPixmap) -> None:
        self.tiles[index] = pixmap
        self.update()

    # -- painting ---------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        c, w, h = theme.current, self.width(), self.height()
        strip = QRectF(0, BAND, w, h - BAND)  # the band above the strip holds the event markers
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(strip, QColor(c["panel"]))
        tile_w = w / self.count
        for i, pixmap in self.tiles.items():
            target = QRectF(i * tile_w, strip.top(), tile_w + 1, strip.height())
            scaled = pixmap.scaled(target.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            sx, sy = (scaled.width() - target.width()) / 2, (scaled.height() - target.height()) / 2
            p.drawPixmap(target, scaled, QRectF(sx, sy, target.width(), target.height()))
        x0, x1 = self.sel.x_at(self.sel.start, w), self.sel.x_at(self.sel.end, w)
        dim = QColor(c["ink"])
        dim.setAlpha(190)
        p.fillRect(QRectF(0, strip.top(), x0, strip.height()), dim)  # outside the selection
        p.fillRect(QRectF(x1, strip.top(), w - x1, strip.height()), dim)
        p.setPen(QPen(QColor(c["accent"]), 2))
        p.drawRect(QRectF(x0, strip.top() + 1, max(x1 - x0, 1), strip.height() - 2))
        for name, x in (("start", x0), ("end", x1)):
            grip = QRectF(x - 5, strip.top() + 8, 10, strip.height() - 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["text"] if self.hasFocus() and self._active == name else c["accent"]))
            p.drawRoundedRect(grip, 4, 4)
        for marker in self.markers:  # each event: a line in its colour, topped by its symbol
            x = self.sel.x_at(marker["offset"], w)
            if not -10 <= x <= w + 10:
                continue  # outside the zoomed stretch
            p.setPen(QPen(QColor(icons.color(marker["type"])), 1.5))
            p.drawLine(QPointF(x, BAND - 2), QPointF(x, h))
            icons.draw(p, marker["type"], QRectF(x - 9, 1, 18, 18))
        px = self.sel.x_at(self.playhead, w)
        p.setPen(QPen(QColor("#FFFFFF"), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(px, strip.top()), QPointF(px, h))
        p.end()

    # -- input -------------------------------------------------------------------

    def _show(self, seconds: float) -> None:
        self.playhead = seconds
        self.seek.emit(seconds)
        self.update()

    def _move(self, handle: str, seconds: float) -> None:
        (self.sel.set_start if handle == "start" else self.sel.set_end)(seconds)
        self._active = handle
        self.changed.emit()
        self._show(self.sel.start if handle == "start" else self.sel.end)

    def mousePressEvent(self, event) -> None:
        x = event.position().x()
        if event.position().y() <= BAND and (marker := self.sel.marker_near(x, self.width(), self.markers)):
            return self._show(marker["offset"])  # a click on a marker jumps to that event
        self._drag = self.sel.handle_near(x, self.width())
        if self._drag:
            self._move(self._drag, self.sel.time_at(x, self.width()))
        else:
            self._show(self.sel.time_at(x, self.width()))

    def mouseDoubleClickEvent(self, event) -> None:
        if marker := self.sel.marker_near(event.position().x(), self.width(), self.markers, reach=10):
            self.sel.around(marker["offset"])  # double-click a marker: select a clip around it
            self.changed.emit()
            self.set_zoom(True)
            self._show(marker["offset"])

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._drag:
            return self._move(self._drag, self.sel.time_at(x, self.width()))
        marker = self.sel.marker_near(x, self.width(), self.markers)
        self.setToolTip(f"{stamp(marker['offset'])}  {t('event.' + marker['type'])}" if marker else "")
        self.setCursor(Qt.CursorShape.SizeHorCursor if self.sel.handle_near(x, self.width()) else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None

    def keyPressEvent(self, event) -> None:
        step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        current = self.sel.start if self._active == "start" else self.sel.end
        if event.key() == Qt.Key.Key_Left:
            self._move(self._active, current - step)
        elif event.key() == Qt.Key.Key_Right:
            self._move(self._active, current + step)
        elif event.key() == Qt.Key.Key_Tab:
            self._active = "end" if self._active == "start" else "start"
            self.update()
        else:
            super().keyPressEvent(event)


class EditorDialog(QDialog):
    """Opened from the library for one video. `finished_file` is the last file it made, if any."""

    def __init__(self, clip: Clip, parent: QWidget | None = None, root: Path | None = None, at: float | None = None):
        super().__init__(parent)
        self.clip, self.root = clip, root
        self.info = export.probe(clip.path) or export.Info(clip.seconds or library.duration(clip.path) or 0.0, 1920, 1080, 1)
        self.finished_file: Path | None = None
        self.job: export.Job | None = None
        self._token = 0
        self.setWindowTitle(f"{t('edit.title')} — {clip.game}")
        self.resize(900, 640)

        self.signals = _Signals(self)
        self.signals.still.connect(self._still_ready)
        self.signals.progress.connect(lambda fraction: self.bar.setValue(int(fraction * 1000)))
        self.signals.done.connect(self._job_done)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(300)
        self.preview.setObjectName("preview")
        self.timeline = Timeline(self.info.duration, list(clip.events))
        self.timeline.changed.connect(self._selection_changed)
        self.timeline.seek.connect(self._seek)
        self.timeline.rezoomed.connect(self._load_tiles)
        self._strip = 0  # counts strip reloads, so tiles for an older zoom are ignored
        self.times = QLabel()
        self.times.setObjectName("muted")
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(160)  # a frame costs ffmpeg about half a second: wait until the hand rests
        self.debounce.timeout.connect(self._load_preview)

        self.exact = QCheckBox(t("edit.exact"))
        self.exact.setToolTip(t("edit.exact_hint"))
        self.no_mic = QCheckBox(t("edit.no_mic"))
        self.no_mic.setEnabled(self.info.audio_tracks >= 2)
        self.no_mic.setToolTip(t("edit.no_mic_hint"))
        options = QHBoxLayout()
        options.setSpacing(22)
        options.addWidget(self.exact)
        options.addWidget(self.no_mic)
        options.addStretch(1)
        options.addWidget(self.times)
        self.zoom = QPushButton(t("edit.zoom"))
        self.zoom.setCheckable(True)
        self.zoom.setToolTip(t("edit.zoom_hint"))
        self.zoom.toggled.connect(self.timeline.set_zoom)
        options.addWidget(self.zoom)

        self.buttons: list[QPushButton] = []
        actions = QHBoxLayout()
        actions.setSpacing(10)
        for key, preset, name in (("edit.save_copy", "trim", "primary"), ("edit.vertical", "vertical", ""),
                                  ("edit.discord", "discord", ""), ("edit.hd", "1080p60", "")):
            button = QPushButton(t(key))
            button.setObjectName(name)
            button.clicked.connect(lambda _=False, p=preset: self._run(p))
            self.buttons.append(button)
            actions.addWidget(button)
        actions.addStretch(1)
        close = QPushButton(t("edit.close"))
        close.clicked.connect(self.close)
        actions.addWidget(close)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.stop = QPushButton(t("button.cancel"))
        self.stop.clicked.connect(lambda: self.job and self.job.cancel())
        self.status = QLabel(t("edit.hint"))
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        working = QHBoxLayout()
        working.setSpacing(12)
        working.addWidget(self.bar, 1)
        working.addWidget(self.stop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.timeline)
        if self.timeline.markers:  # what each symbol means, and how many of each there are
            legend = QHBoxLayout()
            legend.setSpacing(6)
            kinds = [m["type"] for m in self.timeline.markers]
            for kind in dict.fromkeys(kinds):
                badge = QLabel()
                badge.setPixmap(icons.pixmap(kind, 16))
                name = QLabel(f"{t('event.' + kind)} {kinds.count(kind)}" if kinds.count(kind) > 1 else t("event." + kind))
                name.setObjectName("muted")
                legend.addWidget(badge)
                legend.addWidget(name)
                legend.addSpacing(12)
            legend.addStretch(1)
            layout.addLayout(legend)
        layout.addLayout(options)
        layout.addLayout(actions)
        layout.addLayout(working)
        layout.addWidget(self.status)
        self._busy(False)
        self._selection_changed()
        self._load_tiles()
        if at is not None and 0 <= at <= self.info.duration:  # opened on one event: select a clip around it
            self.timeline.sel.around(at)
            self.timeline.playhead = at
            self._selection_changed()
            self._seek(at)
        else:
            self._seek(0.0)

    def _load_tiles(self) -> None:
        self._strip += 1
        self.zoom.blockSignals(True)
        self.zoom.setChecked(self.timeline.sel.zoomed)  # a double-click on a marker zooms too
        self.zoom.blockSignals(False)
        for index, seconds in enumerate(self.timeline.tile_times()):
            self.pool.start(_Still(self.clip.path, seconds, 192, self._strip * 1000 + index, self.signals))

    # -- preview ---------------------------------------------------------------------

    def _seek(self, seconds: float) -> None:
        self._wanted = seconds
        self.debounce.start()

    def _load_preview(self) -> None:
        self._token += 1
        # Ahead of the queued strip tiles: the preview is what the user is waiting for.
        self.pool.start(_Still(self.clip.path, self._wanted, 960, -self._token, self.signals), 1)

    def _still_ready(self, tag: int, image: str) -> None:
        if not image:
            return
        if tag >= 0:
            if tag // 1000 == self._strip:
                self.timeline.set_tile(tag % 1000, QPixmap(image))
            return
        if -tag == self._token:  # ignore a frame for a spot the hand has already left
            self.preview.setPixmap(QPixmap(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation))

    def _selection_changed(self) -> None:
        sel = self.timeline.sel
        self.times.setText(t("edit.range", start=stamp(sel.start), end=stamp(sel.end), length=stamp(sel.end - sel.start)))

    # -- exports ----------------------------------------------------------------------

    def _busy(self, on: bool) -> None:
        for button in self.buttons:
            button.setEnabled(not on)
        self.bar.setVisible(on)
        self.stop.setVisible(on)
        self.bar.setValue(0)

    def _run(self, preset: str) -> None:
        sel = self.timeline.sel
        if preset == "trim" and not self.exact.isChecked():
            try:  # fast cut: instant, lossless, lands on keyframes
                return self._finished(library.trim(self.clip.path, sel.start, sel.end))
            except (OSError, ValueError) as e:
                return self.status.setText(t("library.failed", error=e))
        self._busy(True)
        self.status.setText(t("library.working"))
        self.job = export.Job(
            self.clip.path, "exact" if preset == "trim" else preset, sel.start, sel.end, self.info,
            on_progress=self.signals.progress.emit,
            on_done=lambda path, why: self.signals.done.emit(str(path or ""), why),
            without_mic=self.no_mic.isChecked(), root=self.root,
        )
        self.job.start()

    def _job_done(self, path: str, why: str) -> None:
        self._busy(False)
        self.job = None
        if path:
            self._finished(Path(path))
        else:
            self.status.setText(t("edit.cancelled") if why == "cancelled" else t("library.failed", error=why))

    def _finished(self, path: Path) -> None:
        self.finished_file = path
        size = f"{path.stat().st_size / 1024**2:.1f} MB"
        self.status.setText(t("edit.done", name=path.name, size=size, folder=str(path.parent)))

    def closeEvent(self, event) -> None:
        if self.job is not None:
            self.job.cancel()  # do not leave ffmpeg encoding behind a closed window
            self.job.join(15)
        super().closeEvent(event)
