"""Spot a known game among running processes. Reads process names only:
no handles into the game, nothing an anti-cheat cares about."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

import psutil

GAMES_JSON = Path(__file__).with_name("games.json")


def load_games(user_games: Iterable[dict] = (), default_mode: str = "session") -> dict[str, dict]:
    """Built-in DB overlaid with the user's entries, keyed by lower-case exe name.
    A user entry with "remove": true hides a built-in game."""
    games: dict[str, dict] = {}
    for entry in [*json.loads(GAMES_JSON.read_text(encoding="utf-8")), *user_games]:
        key = entry["exe"].lower()
        if entry.get("remove"):
            games.pop(key, None)
        else:
            games[key] = {"mode": default_mode, **games.get(key, {}), **entry}
    return games


def running_exes() -> set[str]:
    return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}


class Detector:
    def __init__(self, games: dict[str, dict], running: Callable[[], set[str]] = running_exes):
        self.games, self.running = games, running
        self.current: dict | None = None

    def poll(self) -> tuple[str, dict] | None:
        """Call every couple of seconds. Returns ("game_started" | "game_exited", game) on a change."""
        # ponytail: full process list each poll (~tens of ms); switch to pid_exists on the
        # active game's pid if this ever shows up in the CPU budget.
        names = self.running()
        if self.current is not None:
            if self.current["exe"].lower() in names:
                return None
            game, self.current = self.current, None
            return "game_exited", game
        for exe, game in self.games.items():
            if exe in names:
                self.current = game
                return "game_started", game
        return None
