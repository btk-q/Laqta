"""The video player used for watching a video and inside the trim window.

Qt Multimedia does the decoding, on the graphics card where it can, so watching a long
3440x1440 recording costs about what any media player costs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QPolygonF, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from ..i18n import t
from ..storage.library import Clip
from . import icons, theme

BOUNDARIES = ("match_start", "match_end")
STEP = 5.0  # seconds the arrow keys jump
VOLUME = [0.8]  # shared by every player while Laqta runs: change it once, it stays
# ponytail: the volume is not remembered between runs; keep it in config.toml if people ask.


def stamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def glyph(kind: str, color: str = "#FFFFFF", size: int = 16) -> QIcon:
    """Play, pause and speaker symbols, drawn (text characters for these look different on every PC)."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.scale(size / 16, size / 16)
    if kind == "play":
        p.drawPolygon(QPolygonF([QPointF(4.5, 2.5), QPointF(13.5, 8), QPointF(4.5, 13.5)]))
    elif kind == "pause":
        p.drawRoundedRect(QRectF(3.5, 2.5, 3.2, 11), 1, 1)
        p.drawRoundedRect(QRectF(9.3, 2.5, 3.2, 11), 1, 1)
    else:  # speaker
        p.drawPolygon(QPolygonF([QPointF(2, 6), QPointF(5, 6), QPointF(9, 2.5), QPointF(9, 13.5), QPointF(5, 10), QPointF(2, 10)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        from PySide6.QtGui import QPen

        p.setPen(QPen(QColor(color), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(7, 4.5, 6, 7), -60 * 16, 120 * 16)
        p.drawArc(QRectF(7, 2, 9, 12), -55 * 16, 110 * 16)
    p.end()
    return QIcon(pixmap)


def track_name(index: int) -> str:
    """Laqta's recordings carry: 1 everything, 2 the game, 3 the microphone (see obs_controller.audio)."""
    return t(("player.track_all", "player.track_game", "player.track_mic")[index]) if index < 3 else t("player.track_n", n=index + 1)


class SeekBar(QWidget):
    """Where you are in the video, with each event's symbol above the spot it happened.
    Click or drag to jump; hover a symbol to see what it was."""

    moved = Signal(float)  # seconds

    def __init__(self):
        super().__init__()
        self.duration, self.position, self.events = 0.0, 0.0, []
        self._dragging = False
        self.setFixedHeight(34)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # time runs left to right in every language
        self.setAccessibleName(t("player.seek"))

    def set_events(self, events) -> None:
        self.events = [e for e in events if e.get("type") not in BOUNDARIES and e.get("offset", -1) >= 0]
        self.update()

    def _x(self, seconds: float) -> float:
        return 8 + (self.width() - 16) * min(1.0, seconds / self.duration) if self.duration else 8

    def _seconds(self, x: float) -> float:
        return max(0.0, min(self.duration, (x - 8) / max(1, self.width() - 16) * self.duration))

    def _near(self, x: float) -> dict | None:
        scored = [(abs(x - self._x(e["offset"])), i) for i, e in enumerate(self.events)]
        return self.events[min(scored)[1]] if scored and min(scored)[0] <= 8 else None

    def paintEvent(self, _event) -> None:
        c, p = theme.current, QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = 24
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["line"]))
        p.drawRoundedRect(QRectF(8, y - 2.5, self.width() - 16, 5), 2.5, 2.5)
        done = self._x(self.position)
        p.setBrush(QColor(c["accent"]))
        p.drawRoundedRect(QRectF(8, y - 2.5, max(0.0, done - 8), 5), 2.5, 2.5)
        for event in self.events:
            icons.draw(p, event["type"], QRectF(self._x(event["offset"]) - 7, 1, 14, 14))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(done - 6, y - 6, 12, 12))
        p.end()

    def mousePressEvent(self, event) -> None:
        self._dragging = True
        x = event.position().x()
        found = self._near(x) if event.position().y() < 16 else None
        self.moved.emit(max(0.0, found["offset"] - 3) if found else self._seconds(x))  # a symbol: start a little before it

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._dragging:
            return self.moved.emit(self._seconds(x))
        found = self._near(x) if event.position().y() < 16 else None
        self.setToolTip(f"{stamp(found['offset'])}  {t('event.' + found['type'])}" if found else stamp(self._seconds(x)))

    def mouseReleaseEvent(self, _event) -> None:
        self._dragging = False


class Player(QWidget):
    """Video, play/pause, time, optional seek bar, audio track and volume. Space plays and
    pauses, the arrow keys jump five seconds, F or a double-click goes full screen."""

    moved = Signal(float)  # the position changed (playing or a jump), in seconds
    stopped = Signal()  # reached the end, or the end of "play from here to there"

    def __init__(self, seekbar: bool = True):
        super().__init__()
        self.player, self.audio = QMediaPlayer(self), QAudioOutput(self)
        self.audio.setVolume(VOLUME[0])
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(220)
        self.video.setStyleSheet("background: #000000;")
        self.video.mouseDoubleClickEvent = lambda _e: self.full_screen()
        self.video.keyPressEvent = self.keyPressEvent  # keys keep working in full screen
        self.player.setVideoOutput(self.video)
        self.until: float | None = None  # pause here (used by "play the selection")
        self._start, self._autoplay = None, False  # where to go once a newly opened video is ready

        self.button = QPushButton()
        self.button.setObjectName("playButton")
        self.button.setFixedSize(QSize(40, 34))
        self.button.clicked.connect(self.toggle)
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("muted")
        self.bar = SeekBar() if seekbar else None
        self.tracks = QComboBox()
        self.tracks.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.tracks.setToolTip(t("player.track_hint"))
        self.tracks.currentIndexChanged.connect(lambda i: i >= 0 and self.player.setActiveAudioTrack(i))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(VOLUME[0] * 100))
        self.volume.setFixedWidth(90)
        self.volume.setAccessibleName(t("player.volume"))
        self.volume.valueChanged.connect(self._set_volume)
        full = QPushButton(t("player.full"))
        full.clicked.connect(self.full_screen)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.button)
        row.addWidget(self.time)
        if self.bar:
            self.bar.moved.connect(self.seek)
            row.addWidget(self.bar, 1)
        else:
            row.addStretch(1)
        row.addWidget(self.tracks)
        speaker = QLabel()
        speaker.setPixmap(glyph("speaker", theme.current["muted"]).pixmap(16, 16))
        row.addWidget(speaker)
        row.addWidget(self.volume)
        row.addWidget(full)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.video, 1)
        layout.addLayout(row)

        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(lambda ms: self._position(self.player.position()))
        self.player.playbackStateChanged.connect(self._state)
        self.player.tracksChanged.connect(self._fill_tracks)
        self.player.mediaStatusChanged.connect(self._status)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._state()

    # -- control ------------------------------------------------------------------------

    def load(self, path: Path, at: float = 0.0, autoplay: bool = False) -> None:
        url = QUrl.fromLocalFile(str(path))
        if url == self.player.source() and self.player.mediaStatus() not in (QMediaPlayer.MediaStatus.NoMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            self.seek(at)  # already open (another moment of the same video): just jump
            return self.player.play() if autoplay else None
        self._start, self._autoplay = at, autoplay
        self.player.setSource(url)

    def _status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._start is not None:
            start, self._start = self._start, None
            self.seek(start)
            if self._autoplay:
                self.player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stopped.emit()

    def seconds(self) -> float:
        return self.player.position() / 1000

    def seek(self, seconds: float) -> None:
        self.player.setPosition(int(max(0.0, seconds) * 1000))

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.until = None
            self.player.play()

    def play_range(self, start: float, end: float) -> None:
        self.seek(start)
        self.until = end
        self.player.play()

    def pause(self) -> None:
        self.player.pause()

    def full_screen(self) -> None:
        self.video.setFullScreen(not self.video.isFullScreen())

    def _set_volume(self, value: int) -> None:
        VOLUME[0] = value / 100
        self.audio.setVolume(VOLUME[0])

    # -- feedback -----------------------------------------------------------------------

    def _position(self, ms: int) -> None:
        seconds, total = ms / 1000, self.player.duration() / 1000
        if self.until is not None and seconds >= self.until:
            self.until = None
            self.player.pause()
            self.stopped.emit()
        self.time.setText(f"{stamp(seconds)} / {stamp(total)}")
        if self.bar:
            self.bar.duration, self.bar.position = total, seconds
            self.bar.update()
        self.moved.emit(seconds)

    def _state(self, *_args) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.button.setIcon(glyph("pause" if playing else "play", theme.current["on_accent"]))
        self.button.setIconSize(QSize(16, 16))
        self.button.setAccessibleName(t("player.pause" if playing else "player.play"))

    def _fill_tracks(self) -> None:
        count = len(self.player.audioTracks())
        self.tracks.blockSignals(True)
        self.tracks.clear()
        for i in range(count):
            self.tracks.addItem(track_name(i))
        self.tracks.setCurrentIndex(max(0, self.player.activeAudioTrack()))
        self.tracks.blockSignals(False)
        self.tracks.setVisible(count > 1)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.seek(self.seconds() + (STEP if key == Qt.Key.Key_Right else -STEP))
        elif key == Qt.Key.Key_F:
            self.full_screen()
        elif key == Qt.Key.Key_Escape and self.video.isFullScreen():
            self.video.setFullScreen(False)
        else:
            super().keyPressEvent(event)

    def close_media(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())  # let go of the file, so it can be deleted or renamed


class PlayerWindow(QDialog):
    """Watching one video inside Laqta."""

    trim = Signal(object, float)  # the clip, and where the player was: open the trim window there

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(1100, 700)
        self.clip: Clip | None = None
        self.title = QLabel()
        self.title.setObjectName("heading")
        self.about = QLabel()
        self.about.setObjectName("muted")
        self.player = Player()
        trim, folder, outside = QPushButton(t("library.trim")), QPushButton(t("library.show")), QPushButton(t("player.outside"))
        trim.setObjectName("primary")
        trim.clicked.connect(lambda: self.clip and (self.player.pause(), self.trim.emit(self.clip, self.player.seconds())))
        folder.clicked.connect(lambda: self.clip and subprocess.Popen(["explorer", "/select,", str(self.clip.path)]))
        outside.clicked.connect(lambda: self.clip and (self.player.pause(), os.startfile(self.clip.path)))
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(trim)
        buttons.addWidget(folder)
        buttons.addStretch(1)
        buttons.addWidget(outside)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(self.about)
        layout.addWidget(self.player, 1)
        layout.addLayout(buttons)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.player.toggle)

    def show_clip(self, clip: Clip, about: str = "", at: float = 0.0) -> None:
        self.clip = clip
        self.setWindowTitle(f"{clip.game} — {clip.path.name}")
        self.title.setText(clip.game)
        self.about.setText(about)
        if self.player.bar:
            self.player.bar.set_events(clip.events)
        self.player.load(clip.path, at, autoplay=True)
        self.show()
        self.raise_()
        self.activateWindow()
        self.player.setFocus()

    def closeEvent(self, event) -> None:
        self.player.close_media()
        super().closeEvent(event)
