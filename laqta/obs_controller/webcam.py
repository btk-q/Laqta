"""The player's webcam as a small picture in a corner of the recording.

OBS shows the camera through its own Video Capture Device source. The source exists only while a
game is being recorded and is removed afterwards, so the camera (and its light) is off the rest
of the time. Cameras are listed with ffmpeg, which sees the same DirectShow devices OBS does.
"""

from __future__ import annotations

import logging
import re
import subprocess

from .client import Obs, ObsError

log = logging.getLogger(__name__)

SOURCE = "Laqta webcam"
KIND = "dshow_input"
CORNERS = ("bottom_right", "bottom_left", "top_right", "top_left")
# OBS's own output shown back inside OBS would be a picture of the recording inside itself.
NOT_CAMERAS = {"obs virtual camera"}


def parse_cameras(listing: str) -> list[str]:
    """Video device names from `ffmpeg -list_devices true -f dshow -i dummy`. Pure."""
    names = re.findall(r'"([^"]+)"\s+\(video\)', listing)
    return [n for n in dict.fromkeys(names) if n.lower() not in NOT_CAMERAS]


def cameras(ffmpeg: str | None) -> list[str]:
    """The webcams on this PC, by name; empty without ffmpeg or a camera."""
    if not ffmpeg:
        return []
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_cameras(done.stderr + done.stdout)


def placement(width: int, height: int, corner: str, size: int) -> dict:
    """Where the camera goes on a `width` x `height` canvas: a box `size` percent of the canvas
    wide (16:9), in `corner`, a small margin from the edges. Pure."""
    box_w = round(width * max(5, min(50, size)) / 100)
    box_h = round(box_w * 9 / 16)
    margin = round(height * 0.02)
    x = margin if corner.endswith("left") else width - box_w - margin
    y = margin if corner.startswith("top") else height - box_h - margin
    return {"positionX": float(x), "positionY": float(y), "alignment": 5,  # anchored at its top-left corner
            "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
            "boundsWidth": float(box_w), "boundsHeight": float(box_h)}


def show(obs: Obs, scene: str, device: str, corner: str, size: int) -> bool:
    """Put the camera named `device` (or the first one, if it is "" or unplugged) in the corner,
    above the game. Returns False when OBS has no camera to show."""
    existing = {i["inputName"] for i in obs.call("GetInputList")["inputs"]}
    if SOURCE not in existing:
        obs.call("CreateInput", sceneName=scene, inputName=SOURCE, inputKind=KIND, sceneItemEnabled=False,
                 inputSettings={"deactivate_when_not_showing": True})
    items = obs.call("GetInputPropertiesListPropertyItems", inputName=SOURCE, propertyName="video_device_id")["propertyItems"]
    usable = [i for i in items if i.get("itemValue") and str(i.get("itemName", "")).lower() not in NOT_CAMERAS]
    chosen = next((i for i in usable if i["itemName"] == device), usable[0] if usable else None)
    if chosen is None:
        hide(obs)
        return False
    obs.call("SetInputSettings", inputName=SOURCE, inputSettings={"video_device_id": chosen["itemValue"]})
    item = obs.call("GetSceneItemId", sceneName=scene, sourceName=SOURCE)["sceneItemId"]
    video = obs.call("GetVideoSettings")
    obs.call("SetSceneItemTransform", sceneName=scene, sceneItemId=item,
             sceneItemTransform=placement(video["baseWidth"], video["baseHeight"], corner, size))
    count = len(obs.call("GetSceneItemList", sceneName=scene)["sceneItems"])
    obs.call("SetSceneItemIndex", sceneName=scene, sceneItemId=item, sceneItemIndex=count - 1)  # on top of the game
    obs.call("SetSceneItemEnabled", sceneName=scene, sceneItemId=item, sceneItemEnabled=True)
    return True


def hide(obs: Obs) -> None:
    """Remove the camera source, which releases the camera."""
    try:
        if SOURCE in {i["inputName"] for i in obs.call("GetInputList")["inputs"]}:
            obs.call("RemoveInput", inputName=SOURCE)
    except ObsError:
        log.warning("could not remove the webcam source", exc_info=True)
