"""User settings: one hand-editable TOML file in %APPDATA%/Laqta."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from ..core.recorder import MODES

DIR = Path(os.environ.get("APPDATA", Path.home())) / "Laqta"
PATH = DIR / "config.toml"

DEFAULTS = {
    "hotkey": "Ctrl+Alt+F9",
    "default_mode": "session",
    "pre_seconds": 15,
    "post_seconds": 5,
    "max_clip_seconds": 60,
    "exit_debounce_seconds": 10,
    "games": [],
}

TEMPLATE = """\
# Laqta settings. Restart Laqta after editing.

hotkey = "Ctrl+Alt+F9"        # "clip this": saves a clip in events mode, adds a mark in hybrid/match
default_mode = "session"      # session = record while the game is open
                              # hybrid  = session + mark events in a .json so clips can be cut later
                              # match   = record each match separately (games with a plugin; others fall back to session)
                              # events  = keep a replay buffer, save a clip on each event/hotkey
pre_seconds = 15              # clip starts this long before the first event
post_seconds = 5              # ...and ends this long after the last one
max_clip_seconds = 60
exit_debounce_seconds = 10    # wait this long after the game closes before stopping

# Add your own games, or override a built-in one (matched by exe name):
# [[games]]
# exe = "MyGame.exe"
# name = "My Game"
# mode = "events"
#
# Hide a built-in game:
# [[games]]
# exe = "dota2.exe"
# remove = true
"""


def load(path: Path = PATH) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
    with path.open("rb") as f:
        cfg = {**DEFAULTS, **tomllib.load(f)}
    if cfg["default_mode"] not in MODES:
        raise ValueError(f'default_mode must be one of {", ".join(MODES)}, not {cfg["default_mode"]!r}')
    for game in cfg["games"]:
        if "exe" not in game:
            raise ValueError("every [[games]] entry needs an exe")
        if game.get("mode", "session") not in MODES:
            raise ValueError(f'{game["exe"]}: mode must be one of {", ".join(MODES)}')
    return cfg
