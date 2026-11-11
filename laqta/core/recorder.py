"""Recording state machine. Pure logic: OBS, the clock and all side effects are injected,
and every method must be called from the one core thread.

Modes (game["mode"]):
  session  record from game start until exit, remembering when events happened
  hybrid   the same as session now (kept so older settings files still load)
  match    record from each match_start event to match_end (needs a plugin), with event marks
  events   keep OBS's replay buffer running; save a clip around each event
"""

from __future__ import annotations

import functools
import logging
import time
from pathlib import Path
from typing import Callable

from ..i18n import t
from ..obs_controller.client import ObsDown, ObsError
from ..obs_controller.setup import ObsBusy
from .events import GameEvent

log = logging.getLogger(__name__)

MODES = ("session", "hybrid", "match", "events")
RETRY = 3.0  # seconds between attempts to get OBS back
LIVENESS = 5.0  # how often to ping OBS while a game is active
SAVE_TIMEOUT = 10.0  # how long OBS gets to report a saved replay
DISK_RETRY = 30.0  # how often to look again while the disk is too full to record
GB = 1024**3
DUPLICATE = 1.0  # same event type within this window is one event
BOUNDARIES = ("match_start", "match_end")


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
        on_recording: Callable[[Path, dict, list[GameEvent], float], None],  # path, game, marked events, clock at start
        notify: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        pre: float = 15.0,
        post: float = 5.0,
        max_clip: float = 60.0,
        exit_debounce: float = 10.0,
        min_confidence: float = 0.5,
        free_space: Callable[[], float | None] = lambda: None,  # bytes free where recordings go; None = unknown
        min_free: float = 0.0,  # bytes; with less than this nothing is recorded. 0 = no guard
    ):
        self.obs, self.connect, self.prepare = obs, connect, prepare
        self.on_clip, self.on_recording, self.notify, self.clock = on_clip, on_recording, notify, clock
        self.pre, self.post, self.max_clip = pre, post, max_clip
        self.exit_debounce, self.min_confidence = exit_debounce, min_confidence
        self.free_space, self.min_free = free_space, min_free
        self._low_disk = False  # not recording because the disk is nearly full

        self.game: dict | None = None
        self.ready = False  # OBS is connected and set up for self.game
        self._interrupted = False  # we lost OBS mid-game; our output may still be running
        self._retry_at: float | None = None
        self._exit_at: float | None = None
        self._live_at = 0.0
        self._last: dict[str, float] = {}
        # events mode
        self._window: list[float] | None = None  # [first event time, save deadline]
        self._events: list[GameEvent] = []
        self._pending: tuple[dict, list[GameEvent], float, float] | None = None  # game, events, keep, pre-roll
        self._save_timeout: float | None = None
        # recording modes
        self._rec_start: float | None = None  # clock when the current recording started
        self._marks: list[GameEvent] = []
        self._in_match = False
        self._stop_at: float | None = None  # match ended; stop after the post-roll

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
        if self._low_disk:
            return "low_disk"
        return "active" if self.ready else "wait_obs"

    @property
    def recording(self) -> bool:
        return self.ready and self._rec_start is not None

    def next_deadline(self) -> float | None:
        """When tick() next has something to do."""
        times = [self._exit_at, self._save_timeout, self._stop_at]
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

    @_obs_guard
    def event(self, ev: GameEvent) -> None:
        if self.game is None:
            return
        mode = self.game["mode"]
        if ev.type in BOUNDARIES and mode == "match":
            # Remembered even while OBS is down, so a reconnect knows whether to record.
            self._in_match = ev.type == "match_start"
            if not self.ready:
                return
            if self._in_match:
                self._stop_at = None  # next match began inside the previous one's post-roll
                if self._rec_start is None:
                    self._start_record()
            elif self._rec_start is not None:
                self._stop_at = ev.t + self.post
        if not self.ready or not self._accept(ev):
            return
        if mode == "events":
            if ev.type in BOUNDARIES:
                return
            if self._window is None:
                self._window, self._events = [ev.t, ev.t + self.post], [ev]
            else:
                # Merge: push the save back, but never past what the replay buffer still holds.
                self._window[1] = min(ev.t + self.post, self._window[0] + self.max_clip)
                self._events.append(ev)
        elif self._rec_start is not None:
            self._marks.append(ev)  # every recording mode keeps marks: a recording without them cannot be searched

    @_obs_guard
    def obs_event(self, name: str, data: dict) -> None:
        if name == "ReplayBufferSaved" and self._pending:
            (game, events, keep, pre), self._pending, self._save_timeout = self._pending, None, None
            self.on_clip(Path(data["savedReplayPath"]), game, events, keep, pre)
            if self.game is None:
                self.obs.call("StopReplayBuffer")
        elif name == "ExitStarted":
            self._down()

    @_obs_guard
    def tick(self) -> None:
        now = self.clock()
        if self._save_timeout is not None and now >= self._save_timeout:
            self._pending = self._save_timeout = None
            self.notify(t("toast.clip_timeout"))
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
            if self._stop_at is not None and now >= self._stop_at:
                self._stop_record()
            if now >= self._live_at:
                self._live_at = now + LIVENESS
                if self._disk_short() is not None:
                    return self._disk_stop()
                self.obs.call("GetVersion")

    @_obs_guard
    def instant_replay(self, ev: GameEvent, seconds: float) -> bool:
        """The "what just happened" key: keep the last `seconds`, ending now, with no post-roll.
        In events mode that is a clip at once; while recording it is a mark. False = nothing to do."""
        if self.game is None or not self.ready:
            return False
        if self.game["mode"] == "events":
            if self._pending:
                return False  # OBS is still writing the previous clip
            events = [*(self._events if self._window else []), ev]  # an open clip is covered by this one
            self._window = None
            keep = min(float(seconds), float(self.replay_seconds))
            self.obs.call("SaveReplayBuffer")
            self._pending = (self.game, events, keep, keep - (ev.t - events[0].t))
            self._save_timeout = self.clock() + SAVE_TIMEOUT
            return True
        if self._rec_start is not None:
            self._marks.append(ev)
            return True
        return False

    @_obs_guard
    def stop_now(self) -> None:
        """Laqta is quitting: stop without waiting for the debounce."""
        if self.game is not None:
            self._exit_at = self.clock()
            self.tick()

    # -- internals ------------------------------------------------------------

    def _accept(self, ev: GameEvent) -> bool:
        if ev.type != "manual":
            if ev.type in self.game.get("disabled_events", ()) or ev.confidence < self.min_confidence:
                return False
        last = self._last.get(ev.type)
        if last is not None and ev.t - last < DUPLICATE:
            return False
        self._last[ev.type] = ev.t
        return True

    def _begin(self) -> None:
        mode = self.game["mode"]
        buffered = mode == "events"
        self._retry_at = self.clock() + RETRY  # used if anything below fails
        try:
            self.connect()
            running = self.obs.call("GetReplayBufferStatus" if buffered else "GetRecordStatus")["outputActive"]
            if running and self._interrupted and (buffered or self._rec_start is not None):
                pass  # socket blip: our own output is still running, adopt it (tick stops it if the disk is full)
            elif (free := self._disk_short()) is not None:
                if not self._low_disk:  # say it once, then look again quietly
                    self.notify(t("toast.disk_refuse", free=f"{free / GB:.1f}"))
                self._low_disk, self._retry_at = True, self.clock() + DISK_RETRY
                return
            elif running and buffered:
                self.obs.call("StopReplayBuffer")  # stale buffer with the wrong settings
                return  # stopping is asynchronous; the retry starts it fresh
            else:
                self._rec_start = self._stop_at = None
                self.prepare(self.game)  # refuses (ObsBusy) if the user is recording
                if buffered:
                    self.obs.call("StartReplayBuffer")
                elif mode != "match" or self._in_match:
                    self._start_record()
        except ObsBusy as e:
            self.notify(str(e))
            return
        self.ready, self._interrupted, self._low_disk = True, False, False
        self._live_at = self.clock() + LIVENESS

    def _start_record(self) -> None:
        self.obs.call("StartRecord")
        self._rec_start, self._marks = self.clock(), []

    def _stop_record(self) -> None:
        start, marks = self._rec_start, self._marks
        self._rec_start, self._marks, self._stop_at = None, [], None
        path = Path(self.obs.call("StopRecord")["outputPath"])
        self.on_recording(path, self.game, marks, start)

    def _save(self) -> None:
        first, deadline = self._window
        self._window = None
        self.obs.call("SaveReplayBuffer")
        # ponytail: one save in flight at a time; a second within SAVE_TIMEOUT replaces the first's metadata.
        self._pending = (self.game, self._events, self.pre + deadline - first, self.pre)
        self._save_timeout = self.clock() + SAVE_TIMEOUT

    def _finish(self) -> None:
        try:
            if self.ready:
                self._stop_outputs()
        finally:
            self.game, self.ready, self._interrupted, self._in_match = None, False, False, False
            self._low_disk = False
            self._exit_at = self._retry_at = self._window = self._stop_at = self._rec_start = None
            self._marks = []
            self._last.clear()

    def _stop_outputs(self) -> None:
        if self._window:
            self._save()  # flush a clip that was still waiting for its post-roll
        if self._rec_start is not None:
            self._stop_record()
        elif self.game["mode"] == "events" and not self._pending:
            self.obs.call("StopReplayBuffer")

    def _disk_short(self) -> float | None:
        """Bytes free if that is under the limit, else None."""
        free = self.free_space() if self.min_free else None
        return free if free is not None and free < self.min_free else None

    def _disk_stop(self) -> None:
        """Stop while there is still room to close the file properly: a disk that fills up
        mid-write costs the whole recording. Recording starts again once space is back."""
        self._low_disk, self.ready = True, False
        self._retry_at = self.clock() + DISK_RETRY
        self.notify(t("toast.disk_stop"))
        self._stop_outputs()

    def _down(self) -> None:
        if self._window or self._pending:
            self.notify(t("toast.clip_lost"))
        self._window = self._pending = self._save_timeout = None
        if self.game is not None:
            self._interrupted = self._interrupted or self.ready
            self.ready = False
            self._retry_at = self.clock() + RETRY
