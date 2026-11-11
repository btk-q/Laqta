"""Rocket League via the game's own Stats API.

Rocket League can stream match events as JSON over a local TCP port (default 49123). It is
off by default; `PacketSendRate` in the user's own TAStatsAPI.ini turns it on. We connect to
127.0.0.1 and read: nothing is read from or injected into the game process.

STATUS: written from the published description of the format, not yet from a capture made on
this PC. Set LAQTA_RL_DUMP to a file path to save the raw stream of a match for fixtures.

The game does not say which player is you. We recognise you by the Steam accounts signed in
on this PC (their ids appear in each player's PrimaryId), or by the name set for the game.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import socket
import threading
import time
import winreg
from pathlib import Path
from typing import Callable

from ..core.events import GameEvent, GamePlugin
from ..i18n import t

log = logging.getLogger(__name__)

PORT = 49123
RATE = 2  # state updates a second. Events (goals, saves) are sent as they happen regardless.
RETRY = 3.0
SECTION = "[TAGame.MatchStatsExporter_TA]"
STARTS = ("MatchCreated", "MatchInitialized", "CountdownBegin", "RoundStarted", "UpdateState")
STATS = {"save": "save", "epicsave": "save", "demolish": "demolition", "demolition": "demolition"}


# -- stream -> messages (pure) --------------------------------------------------------------


class Decoder:
    """The game sends JSON objects back to back with nothing between them."""

    def __init__(self):
        self._buffer = ""
        self._json = json.JSONDecoder()

    def feed(self, data: bytes) -> list[dict]:
        self._buffer += data.decode("utf-8", errors="replace")
        out = []
        while True:
            self._buffer = self._buffer.lstrip()
            if not self._buffer:
                break
            try:
                message, end = self._json.raw_decode(self._buffer)
            except ValueError:
                if len(self._buffer) > 1_000_000 or not self._buffer.startswith("{"):
                    self._buffer = ""  # not the start of a message: we joined mid-stream or it is not JSON
                break  # otherwise the rest of this message has not arrived yet
            self._buffer = self._buffer[end:]
            if isinstance(message, dict):
                out.append(message)
        return out


# -- messages -> events (pure) --------------------------------------------------------------


class Tracker:
    """Feed it messages in order; it returns the events each one implies for the local player."""

    def __init__(self, ids: set[str] = frozenset(), name: str = ""):
        self.ids, self.name = {i for i in ids if i}, name.strip().casefold()
        self.me: tuple[str, int] | None = None  # (name as the game spells it, team) once seen
        self.in_match = self.ended = False

    def _is_me(self, player: dict) -> bool:
        if self.name:  # a name typed by the user wins: it also covers Epic accounts
            return str(player.get("Name", "")).strip().casefold() == self.name or self.name in str(player.get("PrimaryId", "")).casefold()
        return any(part in self.ids for part in str(player.get("PrimaryId", "")).split("|"))

    def _mine(self, who) -> bool:
        """Goal and stat messages name a player by name and team only."""
        return bool(self.me) and isinstance(who, dict) and (who.get("Name"), who.get("TeamNum")) == self.me

    def update(self, message: dict, now: float) -> list[GameEvent]:
        out: list[GameEvent] = []

        def emit(type: str, **data) -> None:
            out.append(GameEvent(type, now, data=data))

        name = message.get("Event") or message.get("event")
        data = message.get("Data") if "Data" in message else message.get("data")
        if isinstance(data, str):  # some builds send the data as JSON inside a string
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        if not isinstance(data, dict):
            data = {}

        if name in ("MatchCreated", "MatchInitialized"):
            self.ended = False
        if name in STARTS and not self.in_match and not self.ended:
            self.in_match, self.me = True, None
            emit("match_start")

        if name == "UpdateState":
            for player in data.get("Players") or ():
                if isinstance(player, dict) and self._is_me(player):
                    self.me = (player.get("Name"), player.get("TeamNum"))
        elif name == "GoalScored":
            if self._mine(data.get("Scorer")):
                emit("goal", speed=data.get("GoalSpeed"))
            elif self._mine(data.get("Assister")):
                emit("assist")
        elif name == "StatfeedEvent":
            # Goals and assists also arrive here; they are taken from GoalScored only.
            kind = STATS.get(str(data.get("EventName") or data.get("Type") or "").replace(" ", "").casefold())
            if kind and self._mine(data.get("MainTarget")):
                emit(kind)
        elif name == "MatchEnded" and self.in_match:
            winner = data.get("WinnerTeamNum")
            if self.me and isinstance(winner, int):
                emit("win" if winner == self.me[1] else "lose")
            emit("match_end")
            self.in_match, self.ended = False, True  # the podium still sends state; that is not a new match
        elif name == "MatchDestroyed":
            if self.in_match:
                emit("match_end")  # left before the end
            self.in_match = self.ended = False
        return out

    def gone(self, now: float) -> list[GameEvent]:
        """The connection dropped: the game closed."""
        ended, self.in_match, self.ended = self.in_match, False, False
        return [GameEvent("match_end", now)] if ended else []


# -- the game's settings file ---------------------------------------------------------------


def documents() -> Path:
    """The user's real Documents folder (it may be redirected, for example into OneDrive)."""
    buffer = ctypes.create_unicode_buffer(260)
    ok = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0  # 5 = CSIDL_PERSONAL
    return Path(buffer.value) if ok and buffer.value else Path.home() / "Documents"


def ini_path() -> Path:
    return documents() / "My Games" / "Rocket League" / "TAGame" / "Config" / "TAStatsAPI.ini"


def read_ini(path: Path) -> dict[str, int]:
    """Port and PacketSendRate as the game has them. Raises OSError if the file is not there."""
    text = path.read_text(encoding="utf-8", errors="replace")
    found = {key: int(value) for key, value in re.findall(r"(?m)^\s*(Port|PacketSendRate)\s*=\s*(\d+)\s*$", text)}
    return {"Port": found.get("Port", PORT), "PacketSendRate": found.get("PacketSendRate", 0)}


def enable(path: Path, rate: int = RATE) -> bool:
    """Turn the Stats API on in the user's own settings file, changing only that one line.
    Returns True if the file changed (the game reads it at launch). Leaves a rate the user
    chose alone, and never touches the game's install folder."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if read_ini(path)["PacketSendRate"] > 0:
        return False
    line = f"PacketSendRate={rate}"
    if re.search(r"(?m)^\s*PacketSendRate\s*=", text):
        text = re.sub(r"(?m)^\s*PacketSendRate\s*=.*$", line, text, count=1)
    elif SECTION in text:
        text = text.replace(SECTION, f"{SECTION}\n{line}", 1)
    else:
        text = f"{SECTION}\n{line}\n\n{text}"
    path.write_text(text, encoding="utf-8")
    return True


def steam_ids() -> set[str]:
    """SteamIDs of the accounts that have signed in to Steam on this PC, from Steam's own file."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        return set(re.findall(r'(?m)^\s*"(7656\d{13})"\s*$', (steam / "config" / "loginusers.vdf").read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return set()


# -- plugin -----------------------------------------------------------------------------------


class RocketLeaguePlugin(GamePlugin):
    name = "rocket_league"
    event_types = ("goal", "assist", "save", "demolition", "win", "lose", "match_start", "match_end")
    wants_player = True  # Settings shows a "your name in this game" box

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._port = PORT

    def setup(self, game: dict) -> str | None:
        path = ini_path()
        try:
            self._port = read_ini(path)["Port"] or PORT
            if enable(path):
                return t("toast.rl_enabled")
        except OSError:
            return t("toast.rl_no_ini")
        if not game.get("player") and not steam_ids():
            return t("toast.rl_who")
        return None

    def start(self, emit: Callable[[GameEvent], None], game: dict) -> None:
        self._stop.clear()
        tracker = Tracker(steam_ids(), str(game.get("player") or ""))
        self._thread = threading.Thread(target=self._run, args=(emit, tracker), name="laqta-rocket-league", daemon=True)
        self._thread.start()

    def _run(self, emit: Callable[[GameEvent], None], tracker: Tracker) -> None:
        dump = os.environ.get("LAQTA_RL_DUMP")  # path: append the raw stream, for making test fixtures
        while not self._stop.is_set():
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=2) as link:
                    log.info("connected to Rocket League's Stats API")
                    link.settimeout(1.0)  # so stop() is noticed within a second
                    decoder = Decoder()
                    while not self._stop.is_set():
                        try:
                            data = link.recv(65536)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        if dump:
                            with open(dump, "ab") as f:
                                f.write(data)
                        for message in decoder.feed(data):
                            for event in tracker.update(message, time.monotonic()):
                                emit(event)
            except OSError:
                pass  # game not up yet, or it closed
            except Exception:
                log.exception("Rocket League stream handling failed")
            for event in tracker.gone(time.monotonic()):
                emit(event)
            self._stop.wait(RETRY)

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
