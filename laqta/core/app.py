"""The core thread: the only thread that talks to OBS. Everything else (UI, hotkey,
OBS events, plugins) reaches it through `inbox`."""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from ..game_detector.detector import Detector, load_games
from ..obs_controller import setup
from ..obs_controller.client import Obs, ObsDown
from ..storage import clips
from .events import GameEvent
from .recorder import Recorder

log = logging.getLogger(__name__)

POLL = 2.0  # seconds between process scans
ENABLE_WS = "Turn on OBS: Tools > WebSocket Server Settings > Enable WebSocket server"


class Core(threading.Thread):
    def __init__(self, cfg: dict, on_state: Callable[[dict], None], notify: Callable[[str], None]):
        super().__init__(name="laqta-core", daemon=True)
        self.inbox: queue.Queue[tuple] = queue.Queue()
        self.on_state, self.notify = on_state, notify
        self.detector = Detector(load_games(cfg["games"], cfg["default_mode"]))
        self.obs = Obs(4455, "", on_event=lambda name, data: self.inbox.put(("obs", name, data)))
        self.recorder = Recorder(
            self.obs,
            connect=self._connect,
            prepare=lambda game: setup.ensure_laqta(self.obs, clips.game_dir(game), self.recorder.replay_seconds),
            on_clip=self._on_clip,
            on_recording=lambda path, game: self.notify(f"Recording saved: {path.name}"),
            notify=notify,
            pre=cfg["pre_seconds"],
            post=cfg["post_seconds"],
            max_clip=cfg["max_clip_seconds"],
            exit_debounce=cfg["exit_debounce_seconds"],
        )
        self._obs_error: str | None = None
        self._last_state: dict | None = None

    # Called from other threads -------------------------------------------------

    def clip_now(self) -> None:
        self.inbox.put(("event", GameEvent("manual", time.monotonic(), source="manual")))

    def quit(self) -> None:
        self.inbox.put(("quit",))

    # Core thread ---------------------------------------------------------------

    def _connect(self) -> None:
        if self.obs.alive:
            return
        try:
            try:
                port, password, enabled = setup.ws_settings()
            except (OSError, ValueError) as e:
                raise ObsDown("Start OBS once, then: " + ENABLE_WS) from e
            if not enabled:
                raise ObsDown(ENABLE_WS)
            self.obs._conn.update(port=port, password=password)
            setup.connect(self.obs, wait=0)  # one attempt; the recorder retries every few seconds
        except ObsDown as e:
            if str(e) != self._obs_error and setup.obs_running():
                self.notify(str(e))  # OBS is up but we cannot get in: the user has to act
            self._obs_error = str(e)
            raise
        self._obs_error = None

    def _on_clip(self, path: Path, game: dict, events: list[GameEvent], keep: float, pre: float) -> None:
        # ponytail: trims on the core thread (stream copy, well under a second); move to a
        # worker if clips ever get long enough to delay the next save.
        clips.finish_clip(path, game, events, keep, pre)
        self.notify(f"Clip saved: {path.name}")

    def _handle(self, kind: str, *args) -> None:
        rec = self.recorder
        if kind == "event":
            if rec.state != "active" or rec.game["mode"] != "events":
                if args[0].type == "manual":
                    self.notify("Clip hotkey needs a game running in events mode")
                return
            rec.event(args[0])
        elif kind == "obs":
            rec.obs_event(*args)

    def run(self) -> None:
        next_poll = 0.0
        while True:
            deadline = min(next_poll, self.recorder.next_deadline() or float("inf"))
            try:
                msg = self.inbox.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                msg = None
            try:
                if msg is not None:
                    if msg[0] == "quit":
                        self.recorder.stop_now()
                        self.obs.close()
                        return
                    self._handle(*msg)
                if time.monotonic() >= next_poll:
                    next_poll = time.monotonic() + POLL
                    change = self.detector.poll()
                    if change and change[0] == "game_started":
                        self.recorder.game_started(change[1])
                    elif change:
                        self.recorder.game_exited()
                self.recorder.tick()
            except Exception:
                log.exception("core loop error")  # never let one bad message kill recording
            self._publish()

    def _publish(self) -> None:
        rec = self.recorder
        state = {
            "state": rec.state,
            "game": (rec.game or {}).get("name"),
            "mode": (rec.game or {}).get("mode"),
            "obs": self.obs.alive,
            "error": self._obs_error if rec.game else None,
        }
        if state != self._last_state:
            self._last_state = state
            log.info("state: %s", state)
            self.on_state(state)
