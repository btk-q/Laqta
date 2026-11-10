"""Counter-Strike 2 via Valve's Game State Integration.

CS2 itself POSTs JSON to a local URL listed in a cfg file in its own cfg folder. We only
listen on 127.0.0.1 and compare each payload with the previous one: nothing is read from
or injected into the game.

A playing client only receives its *own* player's data, so every event here is about the
local player. After death `player` becomes whoever is being spectated, hence the
steamid check before any counter is trusted.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import winreg
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from ..core.events import GameEvent, GamePlugin
from ..storage import config

log = logging.getLogger(__name__)

PORT = 37650
CFG_NAME = "gamestate_integration_laqta.cfg"
APP_ID = "730"
CFG_TEMPLATE = """"Laqta"
{{
 "uri"       "http://127.0.0.1:{port}/"
 "timeout"   "5.0"
 "buffer"    "0.1"
 "throttle"  "0.1"
 "heartbeat" "10.0"
 "auth" {{ "token" "{token}" }}
 "data"
 {{
  "provider"            "1"
  "map"                 "1"
  "round"               "1"
  "player_id"           "1"
  "player_state"        "1"
  "player_match_stats"  "1"
 }}
}}
"""


# -- payload -> events (pure) ---------------------------------------------------


class Tracker:
    """Feed it GSI payloads in order; it returns the events each one implies."""

    def __init__(self):
        self.in_match = False
        self.round_phase: str | None = None
        self.team: str | None = None  # local player's side, kept while spectating
        self.local: dict | None = None  # last counters seen for the local player

    def update(self, payload: dict, now: float) -> list[GameEvent]:
        out: list[GameEvent] = []

        def emit(type: str, **data) -> None:
            out.append(GameEvent(type, now, data=data))

        game_map = payload.get("map")
        phase = game_map.get("phase") if game_map else None
        player = payload.get("player") or {}
        is_local = bool(player.get("steamid")) and player.get("steamid") == (payload.get("provider") or {}).get("steamid")
        if is_local and player.get("team"):
            self.team = player["team"]

        if phase == "live" and not self.in_match:
            self.in_match = True
            emit("match_start", map=game_map.get("name"), mode=game_map.get("mode"))

        if is_local and phase != "warmup":
            state, stats = player.get("state") or {}, player.get("match_stats") or {}
            now_counts = {
                "kills": state.get("round_kills", 0),
                "headshots": state.get("round_killhs", 0),
                "deaths": stats.get("deaths", 0),
                "assists": stats.get("assists", 0),
            }
            before = self.local
            if before is not None:  # only diff local-vs-local samples
                kills = now_counts["kills"]
                if kills > before["kills"]:  # round_kills resets to 0 each round; a drop is not a kill
                    emit("kill", round_kills=kills)
                    if now_counts["headshots"] > before["headshots"]:
                        emit("headshot")
                    if kills >= 5:
                        emit("ace")
                    elif kills >= 2:
                        emit("multikill", count=kills)
                if now_counts["deaths"] > before["deaths"]:
                    emit("death")
                if now_counts["assists"] > before["assists"]:
                    emit("assist")
            self.local = now_counts
        # While spectating we keep the last local counters; they resume when we respawn.

        round_ = payload.get("round") or {}
        round_phase = round_.get("phase")
        if round_phase == "over" and self.round_phase != "over" and round_.get("win_team") and self.team:
            emit("round_win" if round_["win_team"] == self.team else "round_lose")
        if round_phase is not None or game_map is None:
            self.round_phase = round_phase

        if self.in_match and (phase == "gameover" or game_map is None):
            if phase == "gameover" and self.team:
                mine = (game_map.get("team_ct" if self.team == "CT" else "team_t") or {}).get("score")
                theirs = (game_map.get("team_t" if self.team == "CT" else "team_ct") or {}).get("score")
                if mine is not None and theirs is not None and mine != theirs:
                    emit("win" if mine > theirs else "lose")
            emit("match_end")
            self.in_match, self.local = False, None
        return out


# -- cfg install ------------------------------------------------------------------


def steam_libraries() -> list[Path]:
    """Steam library folders, read from Steam's own files (no process access)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return []
    libraries = [steam]
    try:
        vdf = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libraries += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    return list(dict.fromkeys(libraries))


def cfg_dir(libraries: list[Path] | None = None) -> Path | None:
    for library in steam_libraries() if libraries is None else libraries:
        if (library / "steamapps" / f"appmanifest_{APP_ID}.acf").exists():
            # The folder is still called "csgo" in CS2.
            path = library / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "cfg"
            if path.is_dir():
                return path
    return None


def token(path: Path | None = None) -> str:
    """Shared secret CS2 sends with every payload, so other local programs cannot fake events."""
    path = path or config.DIR / "gsi_token.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(16), encoding="ascii")
    return path.read_text(encoding="ascii").strip()


def install_cfg(folder: Path, secret: str, port: int = PORT) -> bool:
    """Write our cfg into CS2's cfg folder. Returns True if it changed (CS2 reads it at launch)."""
    target, wanted = folder / CFG_NAME, CFG_TEMPLATE.format(port=port, token=secret)
    if target.exists() and target.read_text(encoding="utf-8") == wanted:
        return False
    target.write_text(wanted, encoding="utf-8")
    return True


# -- plugin -----------------------------------------------------------------------


class Cs2Plugin(GamePlugin):
    name = "cs2"
    event_types = (
        "kill", "headshot", "multikill", "ace", "death", "assist",
        "round_win", "round_lose", "win", "lose", "match_start", "match_end",
    )  # fmt: skip

    def __init__(self):
        self._server: HTTPServer | None = None

    def setup(self, game: dict) -> str | None:
        folder = cfg_dir()
        if folder is None:
            return "CS2 install not found in Steam; event detection is off"
        try:
            if install_cfg(folder, token()):
                return "CS2 event detection installed. Restart CS2 once to turn it on."
        except OSError as e:
            return f"Could not write the CS2 integration file: {e}"
        return None

    def start(self, emit: Callable[[GameEvent], None], game: dict) -> None:
        tracker, secret = Tracker(), token()
        dump = os.environ.get("LAQTA_GSI_DUMP")  # path: append every raw payload, for making test fixtures

        class Handler(BaseHTTPRequestHandler):
            timeout = 5  # seconds a client may stall; the server handles one request at a time

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 1_000_000:
                        return self.send_error(400)
                    payload = json.loads(self.rfile.read(length))
                    sent = str((payload.get("auth") or {}).get("token", ""))
                    if not hmac.compare_digest(sent, secret):
                        return self.send_error(403)
                except (ValueError, AttributeError):
                    return self.send_error(400)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                if dump:
                    payload.pop("auth", None)
                    with open(dump, "a", encoding="utf-8") as f:
                        f.write(json.dumps(payload) + "\n")
                try:
                    for event in tracker.update(payload, time.monotonic()):
                        emit(event)
                except (AttributeError, TypeError, ValueError):
                    log.warning("ignored a GSI payload with an unexpected shape")

            def log_message(self, *args):
                pass

        # Plain (single-threaded) HTTPServer on purpose: payloads must reach the tracker in order.
        self._server = HTTPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=self._server.serve_forever, name="laqta-cs2-gsi", daemon=True).start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
