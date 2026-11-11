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
from ..obs_controller.encoders import ORDER as ENCODERS

DIR = Path(os.environ.get("APPDATA", Path.home())) / "Laqta"
PATH = DIR / "config.toml"
# Kept here, not imported from storage.share, so reading settings never loads the upload code.
WEBHOOK = re.compile(r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+")
QUALITIES = ("Stream", "Small", "HQ")  # smaller files .. near lossless
HEIGHTS = (0, 720, 1080, 1440, 2160)
FPS = (30, 60, 120)

DEFAULTS = {
    "language": "en",
    "hotkey": "Ctrl+Alt+F9",
    "replay_hotkey": "Ctrl+Alt+F10",  # "what just happened": keep the last replay_seconds
    "replay_seconds": 30,
    "default_mode": "session",
    "pre_seconds": 15,
    "post_seconds": 5,
    "max_clip_seconds": 60,
    "exit_debounce_seconds": 10,
    "fps": 60,
    "quality": "Small",
    "encoder": "auto",  # or one of obs_controller.encoders.ORDER
    "output_height": 0,  # scale recordings down to this height; 0 = the monitor's own size
    "bitrate": 0,  # kbit/s; 0 = constant quality from "quality"
    "mic_enabled": False,  # record the microphone on its own audio track
    "mic_device": "default",
    "mic_volume": 100,  # percent
    "mic_trigger": False,  # mark or clip the moment when the microphone gets loud (needs mic_enabled)
    "mic_trigger_db": -12,  # how loud: peak level in dB below full scale
    "save_folder": "",  # where recordings go; "" = Videos\\Laqta
    "cleanup_days": 0,  # delete recordings older than this; 0 = never
    "cleanup_gb": 0,  # keep the recordings folder under this size; 0 = no limit
    "min_free_gb": 5,  # do not record with less free disk space than this; 0 = no guard
    "accent": "#8B5CF6",  # UI colour
    "toast_window": False,  # show Laqta's notes in a small corner window instead of Windows notifications
    "discord_webhook": "",  # where "Post to Discord" sends a clip; "" = the button is not shown. A secret.
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
    def video(where: dict, label: str, optional: bool) -> None:
        skip = (None,) if optional else ()
        need(where.get("fps") in (*skip, *FPS), f"{label}fps must be 30, 60 or 120")
        need(where.get("encoder") in (*skip, "auto", *ENCODERS), f"{label}encoder must be auto or one of {', '.join(ENCODERS)}")
        need(where.get("output_height") in (*skip, *HEIGHTS), f"{label}output_height must be one of {HEIGHTS}")
        need(where.get("bitrate") in skip or (isinstance(where.get("bitrate"), int) and 0 <= where["bitrate"] <= 150000),
             f"{label}bitrate must be between 0 and 150000 kbit/s")

    video(cfg, "", optional=False)
    need(isinstance(cfg["save_folder"], str) and (not cfg["save_folder"] or Path(cfg["save_folder"]).is_absolute()),
         "save_folder must be a full path such as D:\\Recordings, or empty for the default")
    need(isinstance(cfg["accent"], str) and re.fullmatch(r"#[0-9a-fA-F]{6}", cfg["accent"]) is not None, "accent must look like #8B5CF6")
    need(isinstance(cfg["replay_seconds"], int) and 5 <= cfg["replay_seconds"] <= 300, "replay_seconds must be between 5 and 300")
    for key in ("cleanup_days", "cleanup_gb"):
        need(isinstance(cfg[key], (int, float)) and 0 <= cfg[key] <= 100000, f"{key} must be 0 or more")
    need(isinstance(cfg["min_free_gb"], (int, float)) and 0 <= cfg["min_free_gb"] <= 1000, "min_free_gb must be between 0 and 1000")
    for key in ("pre_seconds", "post_seconds", "max_clip_seconds", "exit_debounce_seconds"):
        need(isinstance(cfg[key], (int, float)) and 0 <= cfg[key] <= 600, f"{key} must be between 0 and 600")
    need(isinstance(cfg["mic_enabled"], bool) and isinstance(cfg["mic_device"], str), "mic_enabled must be true or false")
    need(isinstance(cfg["toast_window"], bool), "toast_window must be true or false")
    need(cfg["discord_webhook"] == "" or (isinstance(cfg["discord_webhook"], str) and WEBHOOK.fullmatch(cfg["discord_webhook"])),
         "discord_webhook must be empty or a Discord webhook address (https://discord.com/api/webhooks/...)")
    need(isinstance(cfg["mic_trigger"], bool), "mic_trigger must be true or false")
    need(isinstance(cfg["mic_trigger_db"], (int, float)) and -60 <= cfg["mic_trigger_db"] <= 0, "mic_trigger_db must be between -60 and 0")
    need(isinstance(cfg["mic_volume"], int) and 0 <= cfg["mic_volume"] <= 200, "mic_volume must be between 0 and 200")
    for game in cfg["games"]:
        need("exe" in game, "every [[games]] entry needs an exe")
        need(game.get("mode", "session") in MODES, f'{game["exe"]}: mode must be one of {", ".join(MODES)}')
        video(game, f'{game["exe"]}: ', optional=True)
        need(isinstance(game.get("player", ""), str), f'{game["exe"]}: player must be text')
        need(isinstance(game.get("game_audio_only", False), bool), f'{game["exe"]}: game_audio_only must be true or false')
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
