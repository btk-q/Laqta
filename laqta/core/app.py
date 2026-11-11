"""The core thread: the only thread that talks to OBS. Everything else (UI, hotkey,
OBS events, plugins) reaches it through `inbox`."""

from __future__ import annotations

import copy
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from ..game_detector.detector import Detector, game_window, load_games
from ..i18n import t
from ..obs_controller import setup
from ..obs_controller.audio import Audio
from ..obs_controller.capture import Capture
from ..obs_controller import encoders
from ..obs_controller.client import Obs, ObsDown, ObsError
from ..plugins import PLUGINS
from ..storage import clips, config, library
from .events import GameEvent, GamePlugin
from .recorder import GB, Recorder

log = logging.getLogger(__name__)

POLL = 2.0  # seconds between process scans
CAPTURE_CHECK = 4.0  # seconds between looks at the picture until it is confirmed not black


def resolve_video(cfg: dict, game: dict) -> dict:
    """This game's video settings: its own where set, otherwise the global ones."""
    def pick(key: str):
        own = game.get(key)
        return cfg[key] if own in (None, "", "auto") or (key != "output_height" and own == 0) else own

    encoder = pick("encoder")
    return {"fps": pick("fps"), "output_height": pick("output_height"), "bitrate": pick("bitrate") or None,
            "encoder": None if encoder == "auto" else encoder, "quality": cfg["quality"]}


def skipped_percent(before: int, after: int, seconds: float, fps: int) -> float:
    """Share of a recording's frames the encoder could not keep up with."""
    frames = max(1.0, seconds * fps)
    return max(0.0, min(100.0, 100.0 * (after - before) / frames))


def with_usable_mode(game: dict) -> dict:
    """Full Match needs a plugin that reports match starts; without one nothing would ever be
    recorded, so fall back to recording the whole session."""
    plugin = PLUGINS.get(game.get("plugin"))
    if game["mode"] == "match" and (plugin is None or "match_start" not in plugin.event_types):
        return {**game, "mode": "session"}
    return game


class Core(threading.Thread):
    def __init__(
        self,
        cfg: dict,
        on_state: Callable[[dict], None],
        notify: Callable[[str], None],
        on_prompt: Callable[[dict], None] = lambda app: None,  # an unknown fullscreen app appeared
    ):
        super().__init__(name="laqta-core", daemon=True)
        self.inbox: queue.Queue[tuple] = queue.Queue()
        self.cfg = cfg
        clips.set_root(cfg["save_folder"])
        self.on_state, self.notify, self.on_prompt = on_state, notify, on_prompt
        self.detector = Detector(load_games(cfg["games"], cfg["default_mode"]), asked=cfg["asked"])
        self.obs = Obs(4455, "", on_event=lambda name, data: self.inbox.put(("obs", name, data)))
        self.recorder = Recorder(
            self.obs,
            connect=self._connect,
            prepare=self._prepare,
            on_clip=self._on_clip,
            on_recording=self._on_recording,
            notify=notify,
            pre=cfg["pre_seconds"],
            post=cfg["post_seconds"],
            max_clip=cfg["max_clip_seconds"],
            exit_debounce=cfg["exit_debounce_seconds"],
            free_space=clips.free_bytes,
            min_free=cfg["min_free_gb"] * GB,
        )
        self.capture = Capture(self.obs, setup.NAME, config.DIR / "capture.json", notify)
        self.audio = Audio(self.obs)
        self._fps, self._skipped = 60, 0
        self._capture_check_at: float | None = None
        self.plugin: GamePlugin | None = None
        self._plugin_game: dict | None = None  # game we already tried to start a plugin for
        self._obs_error: str | None = None
        self._last_state: dict | None = None

    # Called from other threads -------------------------------------------------

    def clip_now(self) -> None:
        self.inbox.put(("event", GameEvent("manual", time.monotonic(), source="manual")))

    def replay_now(self) -> None:
        self.inbox.put(("replay", GameEvent("replay", time.monotonic(), source="manual")))

    def quit(self) -> None:
        self.inbox.put(("quit",))

    def apply(self, cfg: dict) -> None:
        """New settings from the UI. Takes effect for the next game (timings: the next clip)."""
        self.inbox.put(("config", copy.deepcopy(cfg)))

    # Core thread ---------------------------------------------------------------

    def _connect(self) -> None:
        if self.obs.alive:
            return
        try:
            try:
                port, password, enabled = setup.ws_settings()
            except (OSError, ValueError) as e:
                raise ObsDown(t("toast.obs_first_run")) from e
            if not enabled:
                raise ObsDown(t("toast.enable_ws"))
            self.obs._conn.update(port=port, password=password)
            setup.connect(self.obs, wait=0)  # one attempt; the recorder retries every few seconds
        except ObsDown as e:
            if str(e) != self._obs_error and setup.obs_running():
                self.notify(str(e))  # OBS is up but we cannot get in: the user has to act
            self._obs_error = str(e)
            raise
        self._obs_error = None

    def _prepare(self, game: dict) -> None:
        cfg, video = self.cfg, resolve_video(self.cfg, game)
        window = None
        for _ in range(12):  # a game's window can open a moment after its process: wait up to 3 s
            window = game_window(game["exe"])
            if window:
                break
            time.sleep(0.25)
        used = setup.ensure_laqta(
            self.obs, clips.game_dir(game), self.recorder.replay_seconds, video["fps"], video["quality"],
            mic=cfg["mic_enabled"], store=config.DIR / "obs.json", encoder=video["encoder"],
            bitrate=video["bitrate"], output_height=video["output_height"], window=window,
        )
        if video["encoder"] and used != video["encoder"]:
            self.notify(t("toast.encoder_fallback", wanted=encoders.NAMES[video["encoder"]], used=encoders.NAMES[used]))
        self._fps = video["fps"]
        self._skipped = self._skipped_frames()
        self.capture.start(game, setup.pick_monitor(self.obs.call("GetMonitorList")["monitors"], window))
        self.audio.start(game, cfg["mic_enabled"], cfg["mic_device"], cfg["mic_volume"])
        self._capture_check_at = time.monotonic() + CAPTURE_CHECK

    def _check_capture(self) -> None:
        """A few seconds into a game, make sure OBS is not recording a black screen, and aim
        game-only audio at the game once its window exists."""
        if self._capture_check_at is None or time.monotonic() < self._capture_check_at:
            return
        if self.recorder.state != "active":
            self._capture_check_at = None if self.recorder.game is None else time.monotonic() + CAPTURE_CHECK
            return
        try:
            more = self.capture.check()
            more = self.audio.check() or more
        except (ObsDown, ObsError) as e:
            log.warning("capture check failed: %s", e)
            more = True
        self._capture_check_at = time.monotonic() + CAPTURE_CHECK if more else None

    def _on_clip(self, path: Path, game: dict, events: list[GameEvent], keep: float, pre: float) -> None:
        # ponytail: trims on the core thread (stream copy, well under a second); move to a
        # worker if clips ever get long enough to delay the next save.
        try:
            clips.finish_clip(path, game, events, keep, pre)
        except Exception:
            log.exception("could not trim or describe %s; the full replay is kept", path)
        self.notify(t("toast.clip_saved", name=path.name))
        self._cleanup()

    def _on_recording(self, path: Path, game: dict, marks: list[GameEvent], start: float) -> None:
        try:
            clips.write_sidecar(path, game, marks, start, seconds=round(time.monotonic() - start, 1), kind="recording")
        except OSError:
            log.exception("could not write event marks for %s", path)
        self.notify(t("toast.recording_saved", name=path.name))
        self._report_skipped(time.monotonic() - start)
        self._cleanup()

    def _skipped_frames(self) -> int:
        try:
            return int(self.obs.call("GetStats").get("outputSkippedFrames", 0))
        except (ObsDown, ObsError):
            return self._skipped

    def _report_skipped(self, seconds: float) -> None:
        """Tell the user when the encoder could not keep up: the recording will stutter."""
        before, self._skipped = self._skipped, self._skipped_frames()
        percent = skipped_percent(before, self._skipped, seconds, self._fps)
        log.info("recording skipped %.1f%% of frames (encoder lag)", percent)
        if percent >= 2.0:
            self.notify(t("toast.skipped", percent=f"{percent:.0f}"))

    def _cleanup(self) -> None:
        try:
            library.cleanup(self.cfg["cleanup_days"], self.cfg["cleanup_gb"])
        except Exception:
            log.exception("auto-cleanup failed")

    def _handle(self, kind: str, *args) -> None:
        rec = self.recorder
        if kind == "event":
            ev = args[0]
            if ev.type == "manual":
                if rec.state != "active":
                    return self.notify(t("toast.no_game"))
                if rec.game["mode"] == "session" or not (rec.recording or rec.game["mode"] == "events"):
                    return self.notify(t("toast.hotkey_idle"))
                if rec.game["mode"] != "events":
                    self.notify(t("toast.marked"))
            rec.event(ev)
        elif kind == "obs":
            rec.obs_event(*args)
        elif kind == "replay":
            if not rec.instant_replay(args[0], self.cfg["replay_seconds"]):
                self.notify(t("toast.hotkey_idle") if rec.state == "active" else t("toast.no_game"))
            elif rec.game["mode"] != "events":
                self.notify(t("toast.marked"))
        elif kind == "config":
            self.cfg = cfg = args[0]
            clips.set_root(cfg["save_folder"])  # applies to the next game; a running recording stays put
            self.detector.games = load_games(cfg["games"], cfg["default_mode"])
            self.detector.asked |= {exe.lower() for exe in cfg["asked"]}
            rec.pre, rec.post, rec.max_clip = cfg["pre_seconds"], cfg["post_seconds"], cfg["max_clip_seconds"]
            rec.exit_debounce = cfg["exit_debounce_seconds"]
            rec.min_free = cfg["min_free_gb"] * GB

    def _start_game(self, game: dict) -> None:
        self.recorder.game_started(with_usable_mode(game))

    def _sync_plugin(self) -> None:
        """Run the game's plugin exactly while the recorder has that game."""
        game = self.recorder.game
        if game is None:
            if self.plugin is not None:
                self.plugin.stop()
            self.plugin = self._plugin_game = None
        elif self._plugin_game is not game:
            self._plugin_game = game
            cls = PLUGINS.get(game.get("plugin"))
            if cls is None:
                return
            try:
                plugin = cls()
                if message := plugin.setup(game):
                    self.notify(message)
                plugin.start(lambda ev: self.inbox.put(("event", ev)), game)
                self.plugin = plugin
            except Exception as e:  # a broken plugin must not stop the recording itself
                log.exception("plugin %s failed to start", cls.name)
                self.notify(t("toast.plugin_failed", game=game.get("name"), error=e))

    def run(self) -> None:
        next_poll = 0.0
        self._cleanup()
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
                        self._sync_plugin()
                        self.obs.close()
                        return
                    self._handle(*msg)
                if time.monotonic() >= next_poll:
                    next_poll = time.monotonic() + POLL
                    change = self.detector.poll()
                    if change and change[0] == "game_started":
                        self._start_game(change[1])
                    elif change and change[0] == "game_exited":
                        self.recorder.game_exited()
                    elif change:
                        self.on_prompt(change[1])
                self.recorder.tick()
                self._check_capture()
                self._sync_plugin()
            except Exception:
                log.exception("core loop error")  # never let one bad message kill recording
            self._publish()

    def _publish(self) -> None:
        rec = self.recorder
        state = {
            "state": rec.state,
            "game": (rec.game or {}).get("name"),
            "mode": (rec.game or {}).get("mode"),
            "recording": rec.recording,
            "obs": self.obs.alive,
            "error": self._obs_error if rec.game else None,
        }
        if state != self._last_state:
            self._last_state = state
            log.info("state: %s", state)
            self.on_state(state)
