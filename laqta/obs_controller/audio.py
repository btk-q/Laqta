"""What goes on the recording's audio tracks.

  track 1  everything (what you hear when you play the file)
  track 2  the game, or the whole desktop when game-only audio is off or unavailable
  track 3  the microphone, when it is on

Game-only audio uses OBS's per-application capture aimed at the game's window, so voice chat
and music stay out of track 2. Everything goes through OBS; no game process is touched.
"""

from __future__ import annotations

import logging
import winreg
from typing import Callable

from .client import Obs

log = logging.getLogger(__name__)

DESKTOP = "Laqta desktop audio"
GAME = "Laqta game audio"
MIC = "Laqta microphone"
KINDS = {DESKTOP: "wasapi_output_capture", GAME: "wasapi_process_output_capture", MIC: "wasapi_input_capture"}
MATCH_EXE = 2  # OBS window "priority": match by executable


def tracks(*on: int) -> dict[str, bool]:
    return {str(i): i in on for i in range(1, 7)}


def ensure_inputs(obs: Obs, scene: str) -> None:
    """A new OBS scene collection has no audio sources at all; make ours (game and mic start muted)."""
    existing = {i["inputName"] for i in obs.call("GetInputList")["inputs"]}
    for name, kind in KINDS.items():
        if name in existing:
            continue
        settings = {} if name == GAME else {"device_id": "default"}
        obs.call("CreateInput", sceneName=scene, inputName=name, inputKind=kind, inputSettings=settings)
        if name != DESKTOP:
            obs.call("SetInputMute", inputName=name, inputMuted=True)


def find_window(obs: Obs, exe: str) -> str | None:
    """The game's "title:class:exe" entry in the per-application capture list, if it has a window."""
    items = obs.call("GetInputPropertiesListPropertyItems", inputName=GAME, propertyName="window")
    suffix = ":" + exe.lower()
    return next((i["itemValue"] for i in items["propertyItems"] if str(i["itemValue"]).lower().endswith(suffix)), None)


class Audio:
    def __init__(self, obs: Obs):
        self.obs = obs
        self.game: dict | None = None
        self.game_only = False  # what was asked for
        self.isolated = False  # whether the game really is alone on track 2 right now

    def start(self, game: dict, mic_enabled: bool, mic_device: str, mic_volume: int) -> None:
        """Route audio for this game. Call check() later: a game's window can appear after its process."""
        self.game, self.game_only, self.isolated = game, bool(game.get("game_audio_only")), False
        self._route()
        self.obs.call("SetInputMute", inputName=MIC, inputMuted=not mic_enabled)
        if mic_enabled:
            self._settings(MIC, {"device_id": mic_device or "default"})
            self.obs.call("SetInputVolume", inputName=MIC, inputVolumeMul=max(0, min(200, mic_volume)) / 100)
            self.obs.call("SetInputAudioTracks", inputName=MIC, inputAudioTracks=tracks(1, 3))

    def _route(self) -> None:
        window = find_window(self.obs, self.game["exe"]) if self.game_only else None
        self.isolated = window is not None
        if self.isolated:
            self._settings(GAME, {"window": window, "priority": MATCH_EXE})
            self.obs.call("SetInputAudioTracks", inputName=GAME, inputAudioTracks=tracks(1, 2))
            log.info("game-only audio for %s", self.game["exe"])
        # Exactly one of the two carries tracks 1 and 2, so nothing is doubled and nothing is silent.
        self.obs.call("SetInputAudioTracks", inputName=DESKTOP, inputAudioTracks=tracks(1, 2))
        self.obs.call("SetInputMute", inputName=GAME, inputMuted=not self.isolated)
        self.obs.call("SetInputMute", inputName=DESKTOP, inputMuted=self.isolated)

    def _settings(self, name: str, wanted: dict) -> None:
        """Change an input's settings only if they differ: re-applying the same device makes OBS
        reopen it, and a live test recorded several seconds of silence from the microphone."""
        current = self.obs.call("GetInputSettings", inputName=name).get("inputSettings") or {}
        if any(current.get(key) != value for key, value in wanted.items()):
            self.obs.call("SetInputSettings", inputName=name, inputSettings=wanted)

    def check(self) -> bool:
        """Try again to isolate the game if its window was not there yet. True = keep checking."""
        if self.game is None or not self.game_only or self.isolated:
            return False
        self._route()
        return not self.isolated


# -- microphones, without OBS (so Settings can list them while OBS is closed) -----------

_CAPTURE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"  # device description, e.g. "Microphone"
_INTERFACE = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"  # the hardware it belongs to
_ACTIVE = 1


def microphones(open_key: Callable = winreg.OpenKey) -> list[tuple[str, str]]:
    """[(device id as OBS expects it, display name)] for every active recording device.
    Read from the registry key Windows keeps its audio endpoints in."""
    found = []
    try:
        with open_key(winreg.HKEY_LOCAL_MACHINE, _CAPTURE) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                guid = winreg.EnumKey(root, i)
                try:
                    with open_key(root, guid) as device:
                        if winreg.QueryValueEx(device, "DeviceState")[0] != _ACTIVE:
                            continue
                    with open_key(root, guid + r"\Properties") as props:
                        name = str(winreg.QueryValueEx(props, _NAME)[0])
                        try:
                            name = f"{name} ({winreg.QueryValueEx(props, _INTERFACE)[0]})"
                        except OSError:
                            pass
                    found.append(("{0.0.1.00000000}." + guid, name))
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(found, key=lambda d: d[1].lower())
