"""Is everything Laqta needs in place? Shown on the Health page and saved in the diagnostic file.

`gather()` looks at the machine; `checks()` is pure and turns what was found into a verdict
and a sentence saying what to do. `write_zip()` packs logs and settings for a bug report,
with secrets and the user's name taken out.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import tomli_w

from .. import __version__
from ..i18n import t
from ..obs_controller import audio, encoders, setup
from ..storage import autostart, clips, config
from .recorder import GB

REMOVED = "<removed>"
SECRET_WORDS = ("webhook", "password", "token", "secret")
WEBHOOK = re.compile(r"https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\S+")


@dataclass(frozen=True)
class Check:
    key: str  # title is t(f"health.{key}")
    status: str  # "ok", "warn" (works, with a catch) or "bad" (recording will not work)
    text: str  # what was found and, if not ok, what to do about it


def gather(cfg: dict, hotkeys: dict[str, bool] | None = None) -> dict:
    """Everything `checks` needs to know about this machine. Quick: no OBS connection, no network."""
    try:
        websocket = "on" if setup.ws_settings()[2] else "off"
    except (OSError, ValueError):
        websocket = "unknown"  # OBS has never been started
    try:
        encoder = json.loads((config.DIR / "obs.json").read_text(encoding="utf-8")).get("encoder")
    except (OSError, ValueError):
        encoder = None
    return {
        "obs": setup.obs_exe() is not None,
        "websocket": websocket,
        "ffmpeg": shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None,
        "folder": str(clips.ROOT),
        "folder_ok": clips.usable_folder(clips.ROOT),
        "free": clips.free_bytes(),
        "min_free": cfg["min_free_gb"] * GB,
        "encoder": encoder,
        "mic_missing": cfg["mic_enabled"] and cfg["mic_device"] != "default"
        and cfg["mic_device"] not in [device for device, _ in audio.microphones()],
        "hotkeys": hotkeys or {},
        "autostart": autostart.state(),
    }


def checks(facts: dict) -> list[Check]:
    found: list[Check] = []

    def add(key: str, status: str, **values) -> None:
        found.append(Check(key, status, t(f"health.{key}.{status}", **values)))

    add("obs", "ok" if facts["obs"] else "bad")
    if facts["obs"]:
        add("websocket", {"on": "ok", "off": "bad", "unknown": "warn"}[facts["websocket"]])
    add("ffmpeg", "ok" if facts["ffmpeg"] else "warn")
    add("folder", "ok" if facts["folder_ok"] else "bad", path=facts["folder"])
    free, limit = facts["free"], facts["min_free"]
    if free is not None:
        # Twice the limit is "getting close": about an hour of recording at high quality.
        status = "bad" if limit and free < limit else "warn" if free < max(2 * limit, 10 * GB) else "ok"
        add("disk", status, free=f"{free / GB:.0f}", limit=f"{limit / GB:.0f}")
    if facts["encoder"]:
        add("encoder", "warn" if facts["encoder"] == "x264" else "ok", name=encoders.NAMES.get(facts["encoder"], facts["encoder"]))
    if facts["mic_missing"]:
        add("mic", "warn")
    taken = [combo for combo, registered in facts["hotkeys"].items() if not registered]
    if facts["hotkeys"]:
        add("hotkeys", "warn" if taken else "ok", keys=", ".join(taken or facts["hotkeys"]))
    if facts["autostart"] == "blocked":
        add("autostart", "warn")
    return found


def forget_encoder(store: Path | None = None) -> None:
    """Make the next game try the graphics-card encoders again (after a driver update)."""
    store = store or config.DIR / "obs.json"
    try:
        kept = {k: v for k, v in json.loads(store.read_text(encoding="utf-8")).items() if k != "encoder"}
        store.write_text(json.dumps(kept), encoding="utf-8")
    except (OSError, ValueError):
        pass  # nothing remembered


# -- diagnostic file -----------------------------------------------------------------------------


def redact(value, key: str = ""):
    """A copy of a settings value with anything secret-looking replaced."""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if value and any(word in key.lower() for word in SECRET_WORDS):
        return REMOVED
    return value


def secrets_in(value, key: str = "") -> list[str]:
    """The secret strings themselves, so they can also be wiped from logs."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in secrets_in(v, k)]
    if isinstance(value, list):
        return [s for v in value for s in secrets_in(v, key)]
    return [value] if isinstance(value, str) and value and any(word in key.lower() for word in SECRET_WORDS) else []


def scrub(text: str, secrets: Iterable[str] = (), home: Path | None = None) -> str:
    """Take known secrets, any Discord webhook address and the user's own folder name out of text."""
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, REMOVED)
    text = WEBHOOK.sub(REMOVED, text)
    home = str(home or Path.home())
    for spelling in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:  # as typed, as a URL, as in JSON
        text = re.sub(re.escape(spelling), lambda _: "%USERPROFILE%", text, flags=re.IGNORECASE)
    return text


def machine_secrets(folder: Path | None = None) -> list[str]:
    """Secrets that live outside config.toml: OBS's WebSocket password and the CS2 token."""
    found = []
    try:
        found.append(setup.ws_settings()[1])
    except (OSError, ValueError):
        pass
    try:
        found.append(((folder or config.DIR) / "gsi_token.txt").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pass
    return found


def write_zip(dest: Path, cfg: dict, results: list[Check], folder: Path | None = None, secrets: Iterable[str] | None = None,
              home: Path | None = None) -> Path:
    """Pack what a bug report needs. Recordings, the token file and OBS's own settings never go in."""
    folder = folder or config.DIR
    secrets = [*(machine_secrets(folder) if secrets is None else secrets), *secrets_in(cfg)]

    def clean(text: str) -> str:
        return scrub(text, secrets, home)

    system = [f"Laqta {__version__}{' (packaged)' if getattr(sys, 'frozen', False) else ''}", platform.platform(),
              f"Python {platform.python_version()}", f"language {cfg.get('language')}"]
    report = [f"[{c.status}] {c.key}: {c.text}" for c in results]
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.with_name(dest.name + ".part")  # same rule as exports: no half-written file under the real name
    with zipfile.ZipFile(work, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.txt", "\n".join(system))
        z.writestr("health.txt", clean("\n".join(report)))
        z.writestr("config.toml", clean(tomli_w.dumps(redact(cfg))))
        for name in ("laqta.log.2", "laqta.log.1", "laqta.log", "obs.json", "capture.json"):
            try:
                z.writestr(name, clean((folder / name).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass  # not there yet
    os.replace(work, dest)
    return dest
