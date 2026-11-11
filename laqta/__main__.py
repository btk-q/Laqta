from __future__ import annotations

import argparse
import logging
import queue
import sys
import time
import logging.handlers

from . import __version__
from .obs_controller import setup
from .obs_controller.client import Obs, ObsDown, ObsError

from .storage import clips, config

ENABLE_WS = (
    "OBS WebSocket server is turned off.\n"
    "In OBS: Tools > WebSocket Server Settings > tick 'Enable WebSocket server' > OK, then run this again."
)


def obs_check(watch: bool) -> int:
    """End-to-end check against the real OBS: record 5 s, save a replay, print both paths."""
    try:
        port, password, enabled = setup.ws_settings()
    except (OSError, ValueError):
        print("Could not read OBS WebSocket settings. Start OBS once, then:\n" + ENABLE_WS)
        return 1
    if not enabled:
        print(ENABLE_WS)
        return 1

    events: queue.Queue[tuple[str, dict]] = queue.Queue()
    obs = Obs(port, password, on_event=lambda name, data: events.put((name, data)))
    try:
        setup.connect(obs)
        version = obs.call("GetVersion")
        print(f"connected: OBS {version['obsVersion']}, obs-websocket {version['obsWebSocketVersion']}")
        setup.ensure_laqta(obs, clips.ROOT / "_check")
        print("Laqta profile, scene collection and capture source ready")

        obs.call("StartRecord")
        time.sleep(5)
        print("recording:", obs.call("StopRecord")["outputPath"])

        if not obs.call("GetReplayBufferStatus")["outputActive"]:
            obs.call("StartReplayBuffer")
        time.sleep(4)
        obs.call("SaveReplayBuffer")
        deadline = time.monotonic() + 10
        while True:
            name, data = events.get(timeout=max(0.1, deadline - time.monotonic()))
            if name == "ReplayBufferSaved":
                print("replay:", data["savedReplayPath"])
                break
        obs.call("StopReplayBuffer")
    except queue.Empty:
        print("FAILED: OBS did not report a saved replay within 10 s")
        return 1
    except (ObsDown, ObsError, setup.ObsBusy) as e:
        print(f"FAILED: {e}")
        return 1

    while watch:
        print("watching OBS; close or kill it to test reconnect (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(2)
                obs.call("GetVersion")
        except ObsDown:
            print("OBS went away, reconnecting...")
            setup.connect(obs, wait=60)
            print("reconnected")
        except KeyboardInterrupt:
            break
    obs.close()
    return 0


def vision_test(plugin_name: str, video: str, fps: float) -> int:
    from .plugins import PLUGINS
    from .plugins.vision import VisionPlugin, run_video

    cls = PLUGINS.get(plugin_name)
    if cls is None or not issubclass(cls, VisionPlugin):
        print(f"{plugin_name!r} is not a vision plugin")
        return 1
    signals = [s for s in cls().signals() if s.templates]
    if not signals:
        print(f"{plugin_name}: no template images installed yet, nothing to detect")
        return 1
    count = 0
    for t, event in run_video(signals, video, fps, cls().streak()):
        count += 1
        print(f"{int(t // 60):02d}:{t % 60:04.1f}  {event.type:<10} score {event.confidence:.2f}")
    print(f"{count} event(s)")
    return 0


def clock_seconds(text: str) -> float:
    """"754", "12:34" or "1:02:03" -> seconds."""
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def calibrate(plugin_name: str, video: str, at: str, cut: str | None, out: str | None) -> int:
    """Look at one moment of a recording the way a vision plugin does, or cut a template from it."""
    from pathlib import Path

    from .plugins import PLUGINS
    from .plugins import vision

    cls = PLUGINS.get(plugin_name)
    if cls is None or not issubclass(cls, vision.VisionPlugin):
        print(f"{plugin_name!r} is not a vision plugin")
        return 1
    signals = cls().signals()
    if cut and not getattr(cls, "assets", None):
        print(f"{plugin_name} does not say where its templates live (set `assets` on the plugin class)")
        return 1
    try:
        frame = vision.frame_at(Path(video), clock_seconds(at))
        if cut:
            event, _, numbers = cut.partition(":")
            signal = next((s for s in signals if s.event == event), None)
            if signal is None:
                print(f"{plugin_name} has no signal {event!r}; it has: {', '.join(s.event for s in signals)}")
                return 1
            box = tuple(int(n) for n in numbers.split(","))
            if len(box) != 4:
                raise ValueError("--cut needs event:x,y,width,height")
            print("template saved:", vision.cut_template(frame, signal, box, cls.assets))
            signals = cls().signals()  # reload, so the report below includes the new template
        folder = Path(out or f"calibrate-{plugin_name}").resolve()
        print(f"frame {frame.shape[1]}x{frame.shape[0]} at {at}")
        for line in vision.inspect(signals, frame, folder):
            print(" ", line)
        print(f"saved frame.png (regions outlined) and region_<event>.png to {folder}")
        print("Box coordinates for --cut are pixels inside region_<event>.png (x,y,width,height).")
    except (OSError, ValueError) as e:
        print(f"FAILED: {e}")
        return 1
    return 0


def update_now(install: bool, restart: bool) -> int:
    """The updater without the window: for a broken install, and for testing the real thing."""
    import tempfile
    from pathlib import Path

    from .storage import update

    release = update.check()
    if release is None:
        print(f"laqta {__version__} is the latest version (or GitHub could not be reached)")
        return 0
    print(f"laqta {release.version} is available ({release.size / 1024 / 1024:.0f} MB)\n\n{release.notes}\n")
    if not install:
        return 0
    app = update.app_dir()
    if app is None:
        print("This copy runs from source: update it with git.")
        return 1
    try:
        update.install(update.download(release, Path(tempfile.gettempdir()) / "laqta-download"), app, restart)
    except OSError as e:
        print(f"FAILED: {e}")
        return 1
    print("downloaded and checked; the files are swapped as soon as this program exits")
    return 0


def diagnose(out: str | None) -> int:
    """Print the health checks and write the diagnostic zip, for when the window will not open."""
    import datetime
    from pathlib import Path

    from .core import health
    from .i18n import t

    try:
        cfg = config.load()
    except (ValueError, OSError) as e:
        print(f"settings file problem, using defaults: {e}")
        cfg = dict(config.DEFAULTS)
    clips.set_root(cfg["save_folder"])
    results = health.checks(health.gather(cfg))
    for check in results:
        print(f"[{check.status:<4}] {t('health.' + check.key)}: {check.text}")
    dest = Path(out or f"Laqta diagnostics {datetime.date.today().isoformat()}.zip").resolve()
    print("saved:", health.write_zip(dest, cfg, results))
    return 1 if any(check.status == "bad" for check in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="laqta")
    parser.add_argument("--version", action="version", version=f"laqta {__version__}")
    parser.add_argument("--tray", action="store_true", help="start in the tray without opening the window (used at Windows sign-in)")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("obs-check", help="verify Laqta can drive OBS on this machine")
    check.add_argument("--watch", action="store_true", help="stay connected and prove reconnect")
    vision = sub.add_parser("vision-test", help="run a vision plugin over a recording and list what it detects")
    vision.add_argument("plugin")
    vision.add_argument("video")
    vision.add_argument("--fps", type=float, default=3.0)
    cal = sub.add_parser("calibrate", help="inspect one moment of a recording as a vision plugin sees it, or cut a template image from it")
    cal.add_argument("plugin")
    cal.add_argument("video")
    cal.add_argument("--at", required=True, help="the moment to look at: seconds, mm:ss or h:mm:ss")
    cal.add_argument("--cut", help="save a template: event:x,y,width,height inside region_<event>.png")
    cal.add_argument("--out", help="folder for the pictures (default: ./calibrate-<plugin>)")
    upd = sub.add_parser("update", help="check GitHub for a newer Laqta; with --install, download and install it")
    upd.add_argument("--install", action="store_true")
    upd.add_argument("--no-restart", action="store_true", help="do not start Laqta again after installing")
    diag = sub.add_parser("diagnose", help="check that everything Laqta needs is in place and save a diagnostic zip")
    diag.add_argument("--out", help="where to write the zip (default: the current folder)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # a console that cannot show a character must not crash a command
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    if args.command == "obs-check":
        logging.basicConfig(level=logging.INFO, format=fmt)
        return obs_check(args.watch)
    if args.command == "update":
        return update_now(args.install, not args.no_restart)
    if args.command == "calibrate":
        return calibrate(args.plugin, args.video, args.at, args.cut, args.out)
    if args.command == "diagnose":
        return diagnose(args.out)
    if args.command == "vision-test":
        return vision_test(args.plugin, args.video, args.fps)

    config.DIR.mkdir(parents=True, exist_ok=True)
    log_file = logging.handlers.RotatingFileHandler(config.DIR / "laqta.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    # The packaged app has no console: sys.stderr is None there.
    handlers = [log_file, *([logging.StreamHandler()] if sys.stderr else [])]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    try:
        cfg = config.load()
    except (ValueError, OSError) as e:  # tomllib.TOMLDecodeError is a ValueError
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, f"{config.PATH}\n\n{e}", "Laqta: settings file problem", 0x10)
        return 1
    from .ui import tray  # imported late so obs-check works without Qt

    return tray.run(cfg, show=not args.tray)


if __name__ == "__main__":
    sys.exit(main())
