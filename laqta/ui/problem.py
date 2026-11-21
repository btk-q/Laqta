"""What the user sees when something in Laqta breaks: a plain message and a button that saves
the diagnostic file, instead of a raw error box full of code.

Used for errors inside the running app (Qt and background threads) and for a failure to start.
Every problem is written to the log; the window is shown at most once per run, so a fault that
repeats cannot bury the screen in boxes.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from pathlib import Path

log = logging.getLogger(__name__)
_shown = threading.Event()


def describe(error: BaseException) -> str:
    """One line for people: the kind of error and its message, no code."""
    text = str(error).strip().splitlines()[0][:200] if str(error).strip() else ""
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def save_diagnostics(parent=None) -> Path | None:
    """Ask where, then write the diagnostic zip (health checks, log, settings without secrets)."""
    import datetime

    from PySide6.QtWidgets import QFileDialog

    from ..core import health
    from ..i18n import t
    from ..storage import config

    try:
        cfg = config.load()
    except (OSError, ValueError):
        cfg = dict(config.DEFAULTS)
    name = t("health.zip_name", date=datetime.date.today().isoformat()) + ".zip"
    chosen, _ = QFileDialog.getSaveFileName(parent, t("health.zip"), str(Path.home() / "Desktop" / name), "Zip (*.zip)")
    if not chosen:
        return None
    return health.write_zip(Path(chosen), cfg, health.checks(health.gather(cfg)))


def show(error: BaseException, fatal: bool = False) -> None:
    """The message box. On the GUI thread only."""
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..i18n import t

    if QApplication.instance() is None:
        QApplication([])
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(t("problem.title"))
    box.setText(t("problem.fatal" if fatal else "problem.text"))
    box.setInformativeText(describe(error))
    box.setDetailedText("".join(traceback.format_exception(error)))
    save = box.addButton(t("problem.save"), QMessageBox.ButtonRole.ActionRole)
    box.addButton(t("problem.close" if fatal else "problem.continue"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()
    if box.clickedButton() is save:
        try:
            save_diagnostics()
        except Exception:
            log.exception("could not save the diagnostic file")


def install(post) -> None:
    """Catch what nobody else catches. `post(fn)` runs fn on the GUI thread."""

    def report(kind, error, trace) -> None:
        if issubclass(kind, KeyboardInterrupt):
            return sys.__excepthook__(kind, error, trace)
        log.error("unexpected error", exc_info=(kind, error, trace))
        if not _shown.is_set():
            _shown.set()
            post(lambda: show(error))

    sys.excepthook = report
    threading.excepthook = lambda args: report(args.exc_type, args.exc_value, args.exc_traceback)
