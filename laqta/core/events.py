from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GameEvent:
    type: str  # kill | multikill | death | win | match_start | match_end | manual | ...
    t: float  # time.monotonic() at detection
    confidence: float = 1.0
    source: str = "api"  # api | vision | audio | manual
    data: dict = field(default_factory=dict)
