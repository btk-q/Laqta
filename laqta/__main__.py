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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="laqta")
    parser.add_argument("--version", action="version", version=f"laqta {__version__}")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("obs-check", help="verify Laqta can drive OBS on this machine")
    check.add_argument("--watch", action="store_true", help="stay connected and prove reconnect")
    args = parser.parse_args(argv)
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    if args.command == "obs-check":
        logging.basicConfig(level=logging.INFO, format=fmt)
        return obs_check(args.watch)

    config.DIR.mkdir(parents=True, exist_ok=True)
    log_file = logging.handlers.RotatingFileHandler(config.DIR / "laqta.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[log_file, logging.StreamHandler()])
    try:
        cfg = config.load()
    except (ValueError, OSError) as e:  # tomllib.TOMLDecodeError is a ValueError
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, f"{config.PATH}\n\n{e}", "Laqta: settings file problem", 0x10)
        return 1
    from .ui import tray  # imported late so obs-check works without Qt

    return tray.run(cfg)


if __name__ == "__main__":
    sys.exit(main())
