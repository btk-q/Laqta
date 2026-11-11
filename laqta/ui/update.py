"""The "a new version is available" window: patch notes, download progress, install."""

from __future__ import annotations

import tempfile
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .. import __version__
from ..i18n import t
from ..storage import update


class _Signals(QObject):
    progress = Signal(float)
    done = Signal(str, str)  # zip path, or "" and the reason


class UpdateDialog(QDialog):
    """Accepted = the new version is unpacked and waiting for Laqta to close. Rejected = the
    user chose to quit instead (or, after a failure, to carry on with the old version)."""

    def __init__(self, release: update.Release, parent: QWidget | None = None):
        super().__init__(parent)
        self.release, self.proceed = release, False  # proceed: carry on without updating
        self._cancelled = False
        self.setWindowTitle(t("update.title"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(560, 480)
        heading = QLabel(t("update.heading", new=release.version, old=__version__))
        heading.setObjectName("heading")
        heading.setWordWrap(True)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(release.notes or t("update.no_notes"))
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        self.status = QLabel(t("update.required", size=f"{release.size / 1024 / 1024:.0f}"))
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        self.go = QPushButton(t("update.now"))
        self.go.setObjectName("primary")
        self.go.setDefault(True)
        self.go.clicked.connect(self._start)
        self.quit = QPushButton(t("update.quit"))
        self.quit.clicked.connect(self.reject)
        self.later = QPushButton(t("update.continue"))  # offered only when updating failed: never lock anyone out
        self.later.clicked.connect(self._continue)
        self.later.hide()
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(self.later)
        buttons.addStretch(1)
        buttons.addWidget(self.quit)
        buttons.addWidget(self.go)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(self.notes, 1)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self.signals = _Signals(self)
        self.signals.progress.connect(lambda fraction: self.bar.setValue(int(fraction * 1000)))
        self.signals.done.connect(self._downloaded)

    def _continue(self) -> None:
        self.proceed = True
        self.reject()

    def _fail(self, why: str) -> None:
        self.bar.hide()
        self.status.setText(t("update.failed", error=why))
        self.go.setEnabled(True)
        self.go.setText(t("update.retry"))
        self.later.show()

    def _start(self) -> None:
        app = update.app_dir()
        if app is None:  # running from source: there is no exe to replace
            webbrowser.open(update.PAGE)
            return self._fail(t("update.from_source"))
        self.go.setEnabled(False)
        self.later.hide()
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText(t("update.downloading"))
        folder = Path(tempfile.gettempdir()) / "laqta-download"

        def work() -> None:
            try:
                path = update.download(self.release, folder, self.signals.progress.emit, cancelled=lambda: self._cancelled)
                self.signals.done.emit(str(path), "")
            except OSError as e:
                self.signals.done.emit("", str(e))
            except RuntimeError:
                pass  # the window was closed while downloading

        threading.Thread(target=work, name="laqta-update", daemon=True).start()

    def _downloaded(self, path: str, why: str) -> None:
        if not path:
            return self._fail(why)
        try:
            update.install(Path(path), update.app_dir())
        except OSError as e:
            return self._fail(str(e))
        self.accept()  # the caller quits Laqta; the waiting script swaps the files and starts it again

    def reject(self) -> None:
        self._cancelled = True
        super().reject()
