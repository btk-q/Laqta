"""Choose how OBS captures a game, and notice when the picture is black.

Game Capture is the best source but anti-cheat (e.g. Riot Vanguard) can block its hook,
leaving a black recording. So: aim at the game's own window, look at a tiny screenshot a few
seconds in, and step down to Window Capture, then Display Capture. What worked is remembered
per game. Everything goes through OBS; nothing here touches the game process.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Callable

from ..i18n import t
from .client import Obs, ObsError

log = logging.getLogger(__name__)

KINDS = ("game_capture", "window_capture", "monitor_capture")
AUDIO = "Laqta desktop audio"
BLANK_CHECKS = 2  # consecutive black screenshots before stepping down
MATCH_EXE = 2  # OBS window "priority": match by executable


def source(kind: str) -> str:
    return f"Laqta {kind}"


def ensure_inputs(obs: Obs, scene: str) -> None:
    """One (disabled) input per capture kind, scaled to fill the canvas, plus desktop audio.
    A new scene collection has no audio source at all, so without this recordings are silent."""
    existing = {i["inputName"] for i in obs.call("GetInputList")["inputs"]}
    video = obs.call("GetVideoSettings")
    # Position is pinned as well as size: when the canvas size changes OBS re-centres existing
    # items for the old shape, which left a real recording shifted 440 px right and cropped.
    fill = {
        "positionX": 0.0, "positionY": 0.0, "alignment": 5,  # 5 = anchored at the top-left corner
        "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
        "boundsWidth": float(video["baseWidth"]), "boundsHeight": float(video["baseHeight"]),
    }  # fmt: skip
    for kind in KINDS:
        if source(kind) in existing:
            item = obs.call("GetSceneItemId", sceneName=scene, sourceName=source(kind))["sceneItemId"]
            current = obs.call("GetSceneItemTransform", sceneName=scene, sceneItemId=item)["sceneItemTransform"]
            if all(current.get(k) == v for k, v in fill.items()):
                continue  # canvas size unchanged
        else:
            item = obs.call("CreateInput", sceneName=scene, inputName=source(kind), inputKind=kind, sceneItemEnabled=False)["sceneItemId"]
        obs.call("SetSceneItemTransform", sceneName=scene, sceneItemId=item, sceneItemTransform=fill)
    if AUDIO not in existing:
        # ponytail: everything the default output device plays (game, voice chat, music).
        # Per-game audio (wasapi_process_output_capture) and microphone are settings for M6.
        obs.call("CreateInput", sceneName=scene, inputName=AUDIO, inputKind="wasapi_output_capture",
                 inputSettings={"device_id": "default"})


def find_window(obs: Obs, exe: str) -> str | None:
    """OBS's own "title:class:exe" string for the game's window, or None if it has none yet."""
    items = obs.call("GetInputPropertiesListPropertyItems", inputName=source("game_capture"), propertyName="window")
    suffix = ":" + exe.lower()
    return next((i["itemValue"] for i in items["propertyItems"] if str(i["itemValue"]).lower().endswith(suffix)), None)


def is_blank(obs: Obs, kind: str) -> bool:
    try:
        shot = obs.call("GetSourceScreenshot", sourceName=source(kind), imageFormat="ppm", imageWidth=64, imageHeight=36)
    except ObsError:
        return True  # a source with nothing hooked has no size and cannot be rendered
    ppm = base64.b64decode(shot["imageData"].split(",", 1)[1])
    pixels = ppm.split(b"\n", 3)[3] if ppm.startswith(b"P6\n") else ppm  # "P6\n64 36\n255\n" + RGB bytes
    return max(pixels, default=0) < 16


class Capture:
    def __init__(self, obs: Obs, scene: str, store: Path, notify: Callable[[str], None]):
        self.obs, self.scene, self.store, self.notify = obs, scene, store, notify
        self.game: dict | None = None
        self.kind = KINDS[0]
        self._applied = False
        self._blank = 0

    def _remembered(self) -> dict:
        try:
            return json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def start(self, game: dict) -> None:
        """Point OBS at this game. Call check() a few seconds later."""
        self.game, self._blank = game, 0
        kind = game.get("capture") or self._remembered().get(game["exe"].lower())
        self.kind = kind if kind in KINDS else KINDS[0]
        self._applied = self._apply()

    def _apply(self) -> bool:
        settings: dict = {}
        if self.kind == "monitor_capture":
            monitors = self.obs.call("GetInputPropertiesListPropertyItems", inputName=source(self.kind), propertyName="monitor_id")
            ids = [m["itemValue"] for m in monitors["propertyItems"] if m["itemValue"] != "DUMMY"]
            if not ids:
                return False
            # ponytail: first monitor OBS lists; pick the one holding the game window when
            # someone plays on a secondary display.
            settings = {"monitor_id": ids[0]}
        else:
            window = find_window(self.obs, self.game["exe"])
            if window is None:
                return False  # process is up but its window is not: try again at the next check
            settings = {"window": window, "priority": MATCH_EXE}
            settings |= {"capture_mode": "window"} if self.kind == "game_capture" else {"method": 2}  # 2 = Windows Graphics Capture
        self.obs.call("SetInputSettings", inputName=source(self.kind), inputSettings=settings)
        for kind in KINDS:
            item = self.obs.call("GetSceneItemId", sceneName=self.scene, sourceName=source(kind))["sceneItemId"]
            self.obs.call("SetSceneItemEnabled", sceneName=self.scene, sceneItemId=item, sceneItemEnabled=kind == self.kind)
        log.info("capturing %s with %s", self.game["exe"], self.kind)
        return True

    def check(self) -> bool:
        """Look at the picture. Returns True while further checks are needed."""
        if self.game is None:
            return False
        if not self._applied:
            self._applied = self._apply()
            return True
        if not is_blank(self.obs, self.kind):
            remembered = self._remembered()
            if remembered.get(self.game["exe"].lower()) != self.kind:
                remembered[self.game["exe"].lower()] = self.kind
                self.store.parent.mkdir(parents=True, exist_ok=True)
                self.store.write_text(json.dumps(remembered, indent=2), encoding="utf-8")
            return False
        self._blank += 1
        if self._blank < BLANK_CHECKS:
            return True
        if self.kind == KINDS[-1]:
            self.notify(t("toast.black", game=self.game.get("name")))
            return False
        self.kind, self._blank = KINDS[KINDS.index(self.kind) + 1], 0
        log.warning("black picture, switching %s to %s", self.game["exe"], self.kind)
        self._applied = self._apply()
        return True
