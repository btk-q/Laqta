"""Turn a stream of plain kills into multi-kills (and, where it is safe to, aces).

For games whose only signal is "a kill happened" (screen-reading plugins). Games with an
official API that already reports multi-kills (CS2, League) do not use this.

Pure: feed it events in order, get back the extra events they imply.
"""

from __future__ import annotations

from ..core.events import GameEvent

RESETS = ("death", "round_win", "round_lose", "win", "lose", "match_start", "match_end")


class Streak:
    def __init__(self, window: float = 10.0, ace: int | None = None):
        # window: a kill this many seconds after the previous one continues the streak.
        # ace: kills in one streak that make an ace. None = never claim one. Only set it for a
        # game that also emits round events, or five quick kills in a deathmatch become an "ace".
        self.window, self.ace = window, ace
        self.count, self._last, self._weakest = 0, 0.0, 1.0

    def feed(self, ev: GameEvent) -> list[GameEvent]:
        if ev.type in RESETS:
            self.count = 0
            return []
        if ev.type != "kill":
            return []
        continuing = self.count and ev.t - self._last <= self.window
        self.count = self.count + 1 if continuing else 1
        self._weakest = min(self._weakest, ev.confidence) if continuing else ev.confidence
        self._last = ev.t
        if self.count < 2:
            return []
        # A streak is only as sure as its least certain kill.
        if self.ace and self.count >= self.ace:
            self.count = 0
            return [GameEvent("ace", ev.t, self._weakest, ev.source)]
        return [GameEvent("multikill", ev.t, self._weakest, ev.source, {"count": self.count})]
