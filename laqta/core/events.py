from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, ClassVar


@dataclass(frozen=True)
class GameEvent:
    type: str  # kill | multikill | death | win | match_start | match_end | manual | ...
    t: float  # time.monotonic() at detection
    confidence: float = 1.0
    source: str = "api"  # api | vision | audio | manual
    data: dict = field(default_factory=dict)


class GamePlugin(ABC):
    """Turns one game's signals into GameEvents. One instance per running game.

    Rules for every plugin: no injection, no memory reading, no handles into the game
    process. Official local APIs first, then screen/audio analysis.
    Emit "match_start" / "match_end" for match boundaries, on the same stream as the rest.
    """

    name: ClassVar[str]
    event_types: ClassVar[tuple[str, ...]]  # everything this plugin can emit (drives user toggles)

    def setup(self, game: dict) -> str | None:
        """Optional one-time install (e.g. a config file the game reads).
        Return a message for the user if they need to do something, else None."""
        return None

    @abstractmethod
    def start(self, emit: Callable[[GameEvent], None], game: dict) -> None:
        """Begin detecting on your own thread; call emit() from any thread. Must not block."""

    @abstractmethod
    def stop(self) -> None:
        """Stop detecting and release everything. Must be safe to call twice."""
