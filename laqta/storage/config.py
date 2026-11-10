"""User settings: one TOML file in %APPDATA%/Laqta, edited by the settings window
(or by hand; unknown keys are kept)."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import tomli_w

from ..core.recorder import MODES
from ..i18n import LANGUAGES
from ..obs_controller.capture import KINDS

DIR = Path(os.environ.get("APPDATA", Path.home())) / "Laqta"
PATH = DIR / "config.toml"
QUALITIES = ("Stream", "Small", "HQ")  # OBS simple-output recording quality presets

DEFAULTS = {
    "language": "en",
    "hotkey": "Ctrl+Alt+F9",
    "default_mode": "session",
    "pre_seconds": 15,
    "post_seconds": 5,
    "max_clip_seconds": 60,
    "exit_debounce_seconds": 10,
    "fps": 60,
    "quality": "Small",
    "save_folder": "",  # where recordings go; "" = Videos\\Laqta
    "cleanup_days": 0,  # delete recordings older than this; 0 = never
    "cleanup_gb": 0,  # keep the recordings folder under this size; 0 = no limit
    "accent": "#8B5CF6",  # UI colour
    "asked": [],  # fullscreen apps we already asked "is this a game?" about
    "games": [],  # user games and overrides of built-in ones, matched by exe
}


def _check(cfg: dict) -> dict:
    def need(ok: bool, message: str) -> None:
        if not ok:
            raise ValueError(message)

    need(cfg["language"] in LANGUAGES, f'language must be one of {", ".join(LANGUAGES)}')
    need(cfg["default_mode"] in MODES, f'default_mode must be one of {", ".join(MODES)}, not {cfg["default_mode"]!r}')
    need(cfg["quality"] in QUALITIES, f'quality must be one of {", ".join(QUALITIES)}')
    need(cfg["fps"] in (30, 60, 120), "fps must be 30, 60 or 120")
    need(isinstance(cfg["save_folder"], str) and (not cfg["save_folder"] or Path(cfg["save_folder"]).is_absolute()),
         "save_folder must be a full path such as D:\\Recordings, or empty for the default")
    need(isinstance(cfg["accent"], str) and re.fullmatch(r"#[0-9a-fA-F]{6}", cfg["accent"]) is not None, "accent must look like #8B5CF6")
    for key in ("cleanup_days", "cleanup_gb"):
        need(isinstance(cfg[key], (int, float)) and 0 <= cfg[key] <= 100000, f"{key} must be 0 or more")
    for key in ("pre_seconds", "post_seconds", "max_clip_seconds", "exit_debounce_seconds"):
        need(isinstance(cfg[key], (int, float)) and 0 <= cfg[key] <= 600, f"{key} must be between 0 and 600")
    for game in cfg["games"]:
        need("exe" in game, "every [[games]] entry needs an exe")
        need(game.get("mode", "session") in MODES, f'{game["exe"]}: mode must be one of {", ".join(MODES)}')
        need(game.get("capture") in (None, *KINDS), f'{game["exe"]}: capture must be one of {", ".join(KINDS)}')
    return cfg


def load(path: Path = PATH) -> dict:
    if not path.exists():
        save(dict(DEFAULTS), path)
    with path.open("rb") as f:
        return _check({**DEFAULTS, **tomllib.load(f)})


def save(cfg: dict, path: Path = PATH) -> None:
    _check({**DEFAULTS, **cfg})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(tomli_w.dumps(cfg).encode("utf-8"))
    os.replace(tmp, path)  # never leave a half-written settings file
