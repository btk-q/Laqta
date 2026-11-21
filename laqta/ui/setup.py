"""The first-run screen: the three things Laqta needs, each with its state and one button
that fixes it. Shown at the first start if anything is missing, and from the Health page."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import health
from ..i18n import t
from ..obs_controller import setup as obs_setup
from ..storage import tools
from . import theme

STEPS = ("obs", "websocket", "ffmpeg")


def states(facts: dict) -> dict[str, str]:
    """Each step as "ok" or what is wrong with it. Pure."""
    obs = "ok" if facts["obs"] else "missing"
    if not facts["obs"]:
        websocket = "wait"  # nothing to switch on until OBS exists
    else:
        websocket = {"on": "ok", "off": "off", "unknown": "never"}[facts["websocket"]]
    return {"obs": obs, "websocket": websocket, "ffmpeg": "ok" if facts["ffmpeg"] else "missing"}


def needed(facts: dict) -> bool:
    return any(state != "ok" for state in states(facts).values())


class SetupDialog(QDialog):
    def __init__(self, cfg: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle(t("setup.title"))
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 18)
        layout.setSpacing(14)
        heading = QLabel(t("setup.heading"))
        heading.setObjectName("heading")
        intro = QLabel(t("setup.intro"))
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(intro)
        self.rows: dict[str, tuple[QLabel, QLabel, QPushButton]] = {}
        for step in STEPS:
            badge, text, button = QLabel(), theme.lead(QLabel()), QPushButton()
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedWidth(76)
            text.setWordWrap(True)
            button.clicked.connect(lambda _=False, s=step: self._act(s))
            title = theme.lead(QLabel(t(f"setup.{step}")))
            title.setObjectName("checkTitle")
            words = QVBoxLayout()
            words.setSpacing(2)
            words.addWidget(title)
            words.addWidget(text)
            row = QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            row.addLayout(words, 1)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            layout.addLayout(row)
            self.rows[step] = (badge, text, button)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(self.note)
        self.done_button = QPushButton()
        self.done_button.setObjectName("primary")
        self.done_button.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.done_button)
        layout.addLayout(bottom)
        self.timer = QTimer(self)  # an install or OBS's first start finishes on its own: keep looking
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        self.state = states(health.gather(self.cfg))
        for step, (badge, text, button) in self.rows.items():
            state = self.state[step]
            ok = state == "ok"
            badge.setText(t("health.status.ok") if ok else t("setup.needed"))
            badge.setObjectName("badge_ok" if ok else "badge_warn")
            badge.style().unpolish(badge)
            badge.style().polish(badge)
            text.setText(t(f"setup.{step}.{state}"))
            text.setObjectName("hint" if ok else "")
            button.setVisible(not ok and state != "wait")
            if not ok and state != "wait":
                button.setText(t(f"setup.{step}.{state}.button"))
        ready = self.state["obs"] == self.state["websocket"] == "ok"
        self.done_button.setText(t("setup.done" if ready else "setup.later"))
        if all(state == "ok" for state in self.state.values()):
            self.note.setText(t("setup.all_ok"))

    def _act(self, step: str) -> None:
        state = self.state[step]
        if state == "missing":  # OBS or ffmpeg: install it, or show where to get it
            what = "obs" if step == "obs" else "ffmpeg"
            if tools.install(what) is None:
                webbrowser.open(tools.PAGES[what])
                return self.note.setText(t("setup.page_opened"))
            return self.note.setText(t("setup.installing"))
        if state == "never":  # OBS writes its settings the first time it runs
            try:
                obs_setup.launch_obs()
            except Exception as e:
                return self.note.setText(str(e))
            return self.note.setText(t("setup.started_obs"))
        if state == "off":
            try:
                turned_on = obs_setup.enable_ws()
            except (OSError, ValueError) as e:
                return self.note.setText(t("setup.ws_failed", error=e))
            self.note.setText(t("setup.ws_on") if turned_on else t("setup.close_obs"))
        self.refresh()
