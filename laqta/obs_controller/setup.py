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

from . import audio, capture, encoders
from .client import Obs, ObsDown, ObsError

log = logging.getLogger(__name__)

NAME = "Laqta"  # profile, scene collection and scene name
OBS_DATA = Path(os.environ.get("APPDATA", "")) / "obs-studio"
WS_CONFIG = OBS_DATA / "plugin_config/obs-websocket/config.json"
# OBS 33.0.1 crashed once in its browser component when profiles were switched back to back,
# so every profile / scene-collection change is followed by a pause, and we avoid switching
# at all in the normal case by launching OBS straight into the Laqta profile.
SETTLE = 1.5


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
    # OBS 32+ leaves .sentinel/run_* behind when it dies and then blocks startup on a
    # "launch in safe mode?" dialog (--disable-shutdown-check no longer exists). Nobody is
    # there to click it mid-game, so clear the markers. Only reached when OBS is not running.
    # ponytail: this also skips the safe-mode offer after a genuine plugin crash; make it a
    # setting if someone needs that prompt back.
    for marker in (OBS_DATA / ".sentinel").glob("run_*"):
        marker.unlink(missing_ok=True)
    args = [str(exe), "--minimize-to-tray"]
    if (OBS_DATA / "basic/profiles" / NAME).is_dir() and (OBS_DATA / "basic/scenes" / f"{NAME}.json").is_file():
        args += ["--profile", NAME, "--collection", NAME]
    subprocess.Popen(args, cwd=exe.parent)


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


def _replay_available(obs: Obs) -> bool:
    try:
        obs.call("GetReplayBufferStatus")
        return True
    except ObsError:
        return False


def _switch(obs: Obs, request: str, **data) -> None:
    obs.call(request, **data)
    time.sleep(SETTLE)


def _encoder_works(obs: Obs) -> bool:
    """Start the replay buffer for a moment: that builds the encoder, which is where a missing
    GPU encoder fails. The start request itself succeeds either way, so look at the result."""
    try:
        obs.call("StartReplayBuffer")
    except ObsError:
        return False
    active = False
    for _ in range(16):  # up to 4 s
        time.sleep(0.25)
        active = obs.call("GetReplayBufferStatus")["outputActive"]
        if active:
            break
    if active:
        obs.call("StopReplayBuffer")
        for _ in range(16):
            if not obs.call("GetReplayBufferStatus")["outputActive"]:
                break
            time.sleep(0.25)
    return active


def _remembered(store: Path | None) -> dict:
    try:
        return json.loads(store.read_text(encoding="utf-8")) if store else {}
    except (OSError, ValueError):
        return {}


def ensure_laqta(
    obs: Obs,
    record_dir: Path,
    replay_seconds: int = 30,
    fps: int = 60,
    quality: str = "Small",
    mic: bool = False,
    store: Path | None = None,  # where the encoder that worked is remembered
    profile_dir: Path | None = None,
) -> str:
    """Idempotent: make sure the Laqta profile, scene collection, scene and sources exist and
    are current, in Advanced output mode with the replay buffer on. Returns the encoder in use."""
    if obs.call("GetRecordStatus")["outputActive"] or obs.call("GetStreamStatus")["outputActive"]:
        raise ObsBusy("OBS is already recording or streaming")
    record_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = profile_dir or OBS_DATA / "basic" / "profiles" / NAME

    profiles = obs.call("GetProfileList")
    previous = profiles["currentProfileName"]
    if NAME not in profiles["profiles"]:
        _switch(obs, "CreateProfile", profileName=NAME)
    elif previous != NAME:
        _switch(obs, "SetCurrentProfile", profileName=NAME)
    other = next((p for p in [previous, *profiles["profiles"]] if p != NAME), None)

    known = _remembered(store).get("encoder")
    candidates = [known] if known in encoders.ORDER else list(encoders.ORDER)
    for encoder in candidates:
        _configure(obs, other, profile_dir, encoder, quality, replay_seconds, mic)
        if encoder == known or _encoder_works(obs):
            break
        log.warning("encoder %s did not start, trying the next one", encoder)
    else:
        raise ObsError("ensure_laqta", 0, "no video encoder could be started in OBS")
    if store and encoder != known:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({**_remembered(store), "encoder": encoder}), encoding="utf-8")

    # Record at the monitor's own size: a new profile defaults to a 1920x1080 canvas scaled to
    # 720p30, which letterboxes and blurs an ultrawide game.
    # ponytail: uses the primary monitor; follow the game's monitor when someone plays on another.
    monitors = obs.call("GetMonitorList")["monitors"]
    primary = next((m for m in monitors if (m["monitorPositionX"], m["monitorPositionY"]) == (0, 0)), monitors[0])
    width, height = min(primary["monitorWidth"], 4096), min(primary["monitorHeight"], 4096)  # OBS's limit
    full = {"baseWidth": width, "baseHeight": height, "outputWidth": width, "outputHeight": height,
            "fpsNumerator": fps, "fpsDenominator": 1}
    video = obs.call("GetVideoSettings")
    if any(video[k] != v for k, v in full.items()):
        obs.call("SetVideoSettings", **full)
    obs.call("SetRecordDirectory", recordDirectory=str(record_dir))

    collections = obs.call("GetSceneCollectionList")
    if NAME not in collections["sceneCollections"]:
        _switch(obs, "CreateSceneCollection", sceneCollectionName=NAME)
    elif collections["currentSceneCollectionName"] != NAME:
        _switch(obs, "SetCurrentSceneCollection", sceneCollectionName=NAME)

    if NAME not in [s["sceneName"] for s in obs.call("GetSceneList")["scenes"]]:
        obs.call("CreateScene", sceneName=NAME)
    obs.call("SetCurrentProgramScene", sceneName=NAME)
    capture.ensure_inputs(obs, NAME)
    audio.ensure_inputs(obs, NAME)
    return encoder


def _configure(obs: Obs, other: str | None, profile_dir: Path, encoder: str, quality: str, replay_seconds: int, mic: bool) -> None:
    """Put the Laqta profile into the wanted state and reload it if anything had to change.

    Advanced output mode, because Simple mode records one audio track and offers no real
    encoder control. Verified on OBS 33: parameters set right after CreateProfile can be
    overwritten while the profile settles; the replay buffer exists only once the profile is
    *loaded* with RecRB=true; and encoder settings are read from recordEncoder.json at load.
    So: set, write the file while another profile is current, reload, check, repeat."""
    wanted = {
        ("Output", "Mode"): "Advanced",
        ("AdvOut", "RecType"): "Standard",
        ("AdvOut", "RecFormat2"): "mkv",  # stays playable if OBS or the PC dies
        ("AdvOut", "RecEncoder"): encoder,
        ("AdvOut", "RecAudioEncoder"): "ffmpeg_aac",
        ("AdvOut", "RecTracks"): "7" if mic else "3",  # bit per track: 1 mix, 2 game, 3 microphone
        ("AdvOut", "RecRB"): "true",
        ("AdvOut", "RecRBTime"): str(replay_seconds),
    }
    encoder_file = profile_dir / "recordEncoder.json"
    encoder_json = json.dumps(encoders.settings(encoder, quality), sort_keys=True)
    for _ in range(5):
        changed = False
        for (category, name), value in wanted.items():
            current = obs.call("GetProfileParameter", parameterCategory=category, parameterName=name)
            if current.get("parameterValue") != value:
                obs.call("SetProfileParameter", parameterCategory=category, parameterName=name, parameterValue=value)
                changed = True
        try:
            stale = encoder_file.read_text(encoding="utf-8") != encoder_json
        except OSError:
            stale = True
        if not changed and not stale and _replay_available(obs):
            return
        if other is None:
            raise ObsError("ensure_laqta", 0, "restart OBS once to finish setting up the Laqta profile")
        _switch(obs, "SetCurrentProfile", profileName=other)
        profile_dir.mkdir(parents=True, exist_ok=True)
        encoder_file.write_text(encoder_json, encoding="utf-8")  # OBS is not looking at this profile right now
        _switch(obs, "SetCurrentProfile", profileName=NAME)
    raise ObsError("ensure_laqta", 0, "OBS did not keep the Laqta profile settings")
