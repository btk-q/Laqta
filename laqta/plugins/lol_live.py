"""League of Legends via Riot's Live Client Data API.

The game itself serves https://127.0.0.1:2999/liveclientdata/* while a match is running
(documented by Riot, no key needed). We poll its event list once a second; nothing is read
from or injected into the game.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from ..core.events import GameEvent, GamePlugin

log = logging.getLogger(__name__)

BASE = "https://127.0.0.1:2999/liveclientdata/"
POLL = 1.0
OBJECTIVES = {"DragonKill": "dragon", "BaronKill": "baron", "HeraldKill": "herald", "TurretKilled": "turret", "InhibKilled": "inhibitor"}


def _name(value) -> str:
    """Riot IDs come as 'Name#TAG' in some fields and bare 'Name' in others."""
    return str(value or "").split("#")[0].strip().casefold()


# -- event list -> events (pure) --------------------------------------------------


class Tracker:
    """Feed it the full `eventdata` list from each poll; it returns what is new and ours."""

    def __init__(self):
        self.last_id: int | None = None  # highest EventID already handled
        self.in_match = False

    def update(self, events: list[dict], me: str, now: float) -> list[GameEvent]:
        out: list[GameEvent] = []
        me = _name(me)

        def emit(type: str, **data) -> None:
            out.append(GameEvent(type, now, data=data))

        if self.last_id is None:
            # First look at this match. If we joined late, do not replay old kills as new clips.
            self.last_id = max((e.get("EventID", -1) for e in events), default=-1)
            self.in_match = not any(e.get("EventName") == "GameEnd" for e in events)
            if self.in_match:
                emit("match_start")
            return out

        for e in sorted(events, key=lambda e: e.get("EventID", -1)):
            if e.get("EventID", -1) <= self.last_id:
                continue
            self.last_id = e["EventID"]
            name = e.get("EventName")
            mine = _name(e.get("KillerName")) == me
            if name == "ChampionKill":
                if mine:
                    emit("kill", victim=e.get("VictimName"))
                elif _name(e.get("VictimName")) == me:
                    emit("death", killer=e.get("KillerName"))
                elif me in map(_name, e.get("Assisters") or []):
                    emit("assist")
            elif name == "Multikill" and mine:
                emit("multikill", count=e.get("KillStreak"))
            elif name == "Ace" and _name(e.get("Acer")) == me:
                emit("ace")
            elif name == "FirstBlood" and _name(e.get("Recipient")) == me:
                emit("first_blood")
            elif name in OBJECTIVES and mine:
                emit("objective", kind=OBJECTIVES[name], stolen=str(e.get("Stolen", "False")) == "True")
            elif name == "GameEnd" and self.in_match:
                result = str(e.get("Result", "")).lower()
                if result in ("win", "lose"):
                    emit(result)
                emit("match_end")
                self.in_match = False
        return out

    def gone(self, now: float) -> list[GameEvent]:
        """The API stopped answering: the match process is gone."""
        ended, self.last_id, self.in_match = self.in_match, None, False
        return [GameEvent("match_end", now)] if ended else []


# -- plugin -----------------------------------------------------------------------


def fetch(path: str, timeout: float = 2.0):
    """JSON from the live client, or None while no match is running."""
    # The game presents a self-signed certificate on loopback; Riot's own examples skip
    # verification. Only ever used for 127.0.0.1.
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout, context=context) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


class LolPlugin(GamePlugin):
    name = "lol"
    event_types = (
        "kill", "multikill", "ace", "first_blood", "death", "assist", "objective",
        "win", "lose", "match_start", "match_end",
    )  # fmt: skip

    def __init__(self, fetch: Callable[[str], object] = fetch):
        self._fetch = fetch
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, emit: Callable[[GameEvent], None], game: dict) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(emit,), name="laqta-lol", daemon=True)
        self._thread.start()

    def poll(self, tracker: Tracker, me: str | None, now: float) -> tuple[list[GameEvent], str | None]:
        """One polling step; returns (events, local player name once known)."""
        data = self._fetch("eventdata")
        if not isinstance(data, dict):
            return tracker.gone(now), None
        if dump := os.environ.get("LAQTA_LOL_DUMP"):  # path: keep the latest raw event list, for test fixtures
            with open(dump, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        if not me:
            me = self._fetch("activeplayername")
            if not isinstance(me, str) or not me:
                return [], None  # still on the loading screen
        return tracker.update(data.get("Events") or [], me, now), me

    def _run(self, emit: Callable[[GameEvent], None]) -> None:
        tracker, me = Tracker(), None
        while not self._stop.wait(POLL):
            try:
                # ponytail: events are stamped with poll time (up to POLL late); use EventTime
                # against gamestats.gameTime if clips ever need to be tighter.
                events, me = self.poll(tracker, me, time.monotonic())
                for event in events:
                    emit(event)
            except Exception:
                log.exception("LoL poll failed")

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
