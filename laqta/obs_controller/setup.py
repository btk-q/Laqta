"""Find, launch and prepare OBS. Laqta owns its own profile + scene collection
and never edits the user's existing ones."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import winreg
from pathlib import Path

import psutil

from .client import Obs, ObsDown

log = logging.getLogger(__name__)

NAME = "Laqta"  # profile, scene collection and scene name
CAPTURE = "Laqta Capture"
WS_CONFIG = Path(os.environ.get("APPDATA", "")) / "obs-studio/plugin_config/obs-websocket/config.json"


class ObsBusy(Exception):
    """The user is already recording/streaming; Laqta must not take over."""


def ws_settings(path: Path = WS_CONFIG) -> tuple[int, str, bool]:
    """(port, password, enabled) as OBS itself stores them. Read-only: we never edit this file."""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    password = cfg.get("server_password", "") if cfg.get("auth_required", True) else ""
    return int(cfg.get("server_port", 4455)), password, bool(cfg.get("server_enabled"))


def obs_exe() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\OBS Studio") as key:
            root = Path(winreg.QueryValue(key, None))
    except OSError:
        root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "obs-studio"
    exe = root / "bin" / "64bit" / "obs64.exe"
    return exe if exe.exists() else None


def obs_running() -> bool:
    return any((p.info["name"] or "").lower() == "obs64.exe" for p in psutil.process_iter(["name"]))


def launch_obs() -> None:
    exe = obs_exe()
    if exe is None:
        raise ObsDown("OBS Studio is not installed")
    # OBS only finds its data files when started from its own folder.
    subprocess.Popen([str(exe), "--minimize-to-tray", "--disable-shutdown-check"], cwd=exe.parent)


def connect(obs: Obs, wait: float = 40.0) -> None:
    """Connect, launching OBS first if it is not running. Raises ObsDown after `wait` seconds."""
    if not obs_running():
        log.info("launching OBS")
        launch_obs()
    deadline = time.monotonic() + wait
    while True:
        try:
            obs.connect()
            obs.call("GetVersion")
            return
        except ObsDown:
            if time.monotonic() > deadline:
                raise
            time.sleep(1.0)


def ensure_laqta(obs: Obs, record_dir: Path, replay_seconds: int = 30) -> None:
    """Idempotent: make sure the Laqta profile, scene collection, scene and capture source exist
    and are current, with the replay buffer enabled."""
    if obs.call("GetRecordStatus")["outputActive"] or obs.call("GetStreamStatus")["outputActive"]:
        raise ObsBusy("OBS is already recording or streaming")
    record_dir.mkdir(parents=True, exist_ok=True)

    profiles = obs.call("GetProfileList")
    previous = profiles["currentProfileName"]
    if NAME not in profiles["profiles"]:
        obs.call("CreateProfile", profileName=NAME)
    elif previous != NAME:
        obs.call("SetCurrentProfile", profileName=NAME)

    wanted = {
        ("Output", "Mode"): "Simple",
        ("SimpleOutput", "FilePath"): str(record_dir),
        ("SimpleOutput", "RecFormat2"): "mkv",  # stays playable if OBS or the PC dies
        ("SimpleOutput", "RecRB"): "true",
        ("SimpleOutput", "RecRBTime"): str(replay_seconds),
    }
    changed = False
    for (category, name), value in wanted.items():
        current = obs.call("GetProfileParameter", parameterCategory=category, parameterName=name)
        if current.get("parameterValue") != value:
            obs.call("SetProfileParameter", parameterCategory=category, parameterName=name, parameterValue=value)
            changed = True
    if changed:
        # OBS builds its outputs (incl. the replay buffer) when a profile loads, so reload ours.
        other = next((p for p in [previous, *profiles["profiles"]] if p != NAME), None)
        if other:
            obs.call("SetCurrentProfile", profileName=other)
            obs.call("SetCurrentProfile", profileName=NAME)

    collections = obs.call("GetSceneCollectionList")
    if NAME not in collections["sceneCollections"]:
        obs.call("CreateSceneCollection", sceneCollectionName=NAME)
    elif collections["currentSceneCollectionName"] != NAME:
        obs.call("SetCurrentSceneCollection", sceneCollectionName=NAME)

    if NAME not in [s["sceneName"] for s in obs.call("GetSceneList")["scenes"]]:
        obs.call("CreateScene", sceneName=NAME)
    obs.call("SetCurrentProgramScene", sceneName=NAME)
    if CAPTURE not in [i["inputName"] for i in obs.call("GetInputList")["inputs"]]:
        obs.call(
            "CreateInput",
            sceneName=NAME,
            inputName=CAPTURE,
            inputKind="game_capture",
            inputSettings={"capture_mode": "any_fullscreen"},
        )
