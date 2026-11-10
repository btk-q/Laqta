"""Recording state machine. Pure logic: OBS, the clock and all side effects are injected,
and every method must be called from the one core thread.

Modes (game["mode"]):
  session  record from game start until exit
  events   keep OBS's replay buffer running; save a clip around each event
"""

from __future__ import annotations

import functools
import logging
import time
from pathlib import Path
from typing import Callable

from ..obs_controller.client import ObsDown, ObsError
from ..obs_controller.setup import ObsBusy
from .events import GameEvent

log = logging.getLogger(__name__)

RETRY = 3.0  # seconds between attempts to get OBS back
LIVENESS = 5.0  # how often to ping OBS while a game is active
SAVE_TIMEOUT = 10.0  # how long OBS gets to report a saved replay
DUPLICATE = 1.0  # same event type within this window is one event
STATUS = {"session": "GetRecordStatus", "events": "GetReplayBufferStatus"}
START = {"session": "StartRecord", "events": "StartReplayBuffer"}


def _obs_guard(method):
    @functools.wraps(method)
    def wrapper(self, *args):
        try:
            return method(self, *args)
        except ObsDown as e:
            log.warning("OBS went away: %s", e)
            self._down()
        except ObsError as e:
            log.warning("%s", e)
            self.notify(str(e))

    return wrapper


class Recorder:
    def __init__(
        self,
        obs,
        connect: Callable[[], None],  # make `obs` usable (launch + connect); raises ObsDown
        prepare: Callable[[dict], None],  # point OBS at this game; raises ObsBusy
        on_clip: Callable[[Path, dict, list[GameEvent], float, float], None],  # path, game, events, seconds to keep, pre-roll
        on_recording: Callable[[Path, dict], None],
        notify: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        pre: float = 15.0,
        post: float = 5.0,
        max_clip: float = 60.0,
        exit_debounce: float = 10.0,
        min_confidence: float = 0.5,
    ):
        self.obs, self.connect, self.prepare = obs, connect, prepare
        self.on_clip, self.on_recording, self.notify, self.clock = on_clip, on_recording, notify, clock
        self.pre, self.post, self.max_clip = pre, post, max_clip
        self.exit_debounce, self.min_confidence = exit_debounce, min_confidence

        self.game: dict | None = None
        self.ready = False  # OBS is recording/buffering for self.game
        self._interrupted = False  # we lost OBS mid-game; our output may still be running
        self._retry_at: float | None = None
        self._exit_at: float | None = None
        self._live_at = 0.0
        self._window: list[float] | None = None  # [first event time, save deadline]
        self._events: list[GameEvent] = []
        self._last: dict[str, float] = {}
        self._pending: tuple[dict, list[GameEvent], float] | None = None  # save requested, not yet reported
        self._save_timeout: float | None = None

    @property
    def replay_seconds(self) -> int:
        """Replay buffer length OBS must be configured with."""
        return int(self.pre + self.max_clip)

    @property
    def state(self) -> str:
        if self.game is None:
            return "idle"
        if self._exit_at is not None:
            return "exit_debounce"
        return "active" if self.ready else "wait_obs"

    def next_deadline(self) -> float | None:
        """When tick() next has something to do."""
        times = [self._exit_at, self._save_timeout]
        if self.game is not None:
            times.append(self._live_at if self.ready else self._retry_at)
        if self._window:
            times.append(self._window[1])
        return min((t for t in times if t is not None), default=None)

    # -- inputs ---------------------------------------------------------------

    @_obs_guard
    def game_started(self, game: dict) -> None:
        if self.game is not None:
            if self._exit_at is not None and game["exe"] == self.game["exe"]:
                self._exit_at = None  # it came back (launcher restart): keep recording
            return  # first game wins; also absorbs duplicate triggers
        self.game = game
        self._begin()

    def game_exited(self) -> None:
        if self.game is not None and self._exit_at is None:
            self._exit_at = self.clock() + self.exit_debounce

    def event(self, ev: GameEvent) -> None:
        if not self.ready or self.game["mode"] != "events":
            return
        if ev.type != "manual":
            if ev.type in self.game.get("disabled_events", ()) or ev.confidence < self.min_confidence:
                return
        last = self._last.get(ev.type)
        if last is not None and ev.t - last < DUPLICATE:
            return
        self._last[ev.type] = ev.t
        if self._window is None:
            self._window, self._events = [ev.t, ev.t + self.post], [ev]
        else:
            # Merge: push the save back, but never past what the replay buffer still holds.
            self._window[1] = min(ev.t + self.post, self._window[0] + self.max_clip)
            self._events.append(ev)

    @_obs_guard
    def obs_event(self, name: str, data: dict) -> None:
        if name == "ReplayBufferSaved" and self._pending:
            (game, events, keep), self._pending, self._save_timeout = self._pending, None, None
            self.on_clip(Path(data["savedReplayPath"]), game, events, keep, self.pre)
            if self.game is None:
                self.obs.call("StopReplayBuffer")
        elif name == "ExitStarted":
            self._down()

    @_obs_guard
    def tick(self) -> None:
        now = self.clock()
        if self._save_timeout is not None and now >= self._save_timeout:
            self._pending = self._save_timeout = None
            self.notify("OBS did not save the clip")
            if self.game is None:
                self.obs.call("StopReplayBuffer")
        if self.game is None:
            return
        if self._exit_at is not None and now >= self._exit_at:
            self._finish()
        elif not self.ready:
            if now >= self._retry_at:
                self._begin()
        else:
            if self._window and now >= self._window[1]:
                self._save()
            if now >= self._live_at:
                self._live_at = now + LIVENESS
                self.obs.call("GetVersion")

    # -- internals ------------------------------------------------------------

    def _begin(self) -> None:
        mode = self.game["mode"]
        self._retry_at = self.clock() + RETRY  # used if anything below fails
        try:
            self.connect()
            running = self.obs.call(STATUS[mode])["outputActive"]
            if not (running and self._interrupted):  # else: adopt our own output after a socket blip
                if running and mode == "events":
                    self.obs.call("StopReplayBuffer")  # stale buffer with the wrong settings
                    return  # stopping is asynchronous; the retry starts it fresh
                self.prepare(self.game)
                self.obs.call(START[mode])
        except ObsBusy as e:
            self.notify(str(e))
            return
        self.ready, self._interrupted = True, False
        self._live_at = self.clock() + LIVENESS

    def _save(self) -> None:
        first, deadline = self._window
        self._window = None
        self.obs.call("SaveReplayBuffer")
        # ponytail: one save in flight at a time; a second within SAVE_TIMEOUT replaces the first's metadata.
        self._pending = (self.game, self._events, self.pre + deadline - first)
        self._save_timeout = self.clock() + SAVE_TIMEOUT

    def _finish(self) -> None:
        game, was_ready = self.game, self.ready
        try:
            if was_ready:
                if self._window:
                    self._save()  # flush a clip that was still waiting for its post-roll
                if game["mode"] == "session":
                    self.on_recording(Path(self.obs.call("StopRecord")["outputPath"]), game)
                elif not self._pending:
                    self.obs.call("StopReplayBuffer")
        finally:
            self.game, self.ready, self._interrupted = None, False, False
            self._exit_at = self._retry_at = self._window = None
            self._last.clear()

    @_obs_guard
    def stop_now(self) -> None:
        """Laqta is quitting: stop without waiting for the debounce."""
        if self.game is not None:
            self._exit_at = self.clock()
            self.tick()

    def _down(self) -> None:
        if self._window or self._pending:
            self.notify("Clip lost: OBS went away")
        self._window = self._pending = self._save_timeout = None
        if self.game is not None:
            self._interrupted = self._interrupted or self.ready
            self.ready = False
            self._retry_at = self.clock() + RETRY
