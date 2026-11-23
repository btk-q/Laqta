from laqta.obs_controller import webcam
from laqta.storage import config

LISTING = """[in#0 @ 0x1] "UGREEN Camera 2K" (video)
[in#0 @ 0x1]   Alternative name "@device_pnp_\\\\?\\usb#vid_0c45"
[in#0 @ 0x1] "OBS Virtual Camera" (video)
[in#0 @ 0x1] "Microphone (UGREEN Camera 2K)" (audio)
[in#0 @ 0x1] "Logi C920" (video)
Error opening input file dummy."""


def test_cameras_come_from_ffmpegs_list_without_obs_own_camera_or_microphones():
    assert webcam.parse_cameras(LISTING) == ["UGREEN Camera 2K", "Logi C920"]
    assert webcam.cameras(None) == []


def test_the_camera_box_sits_in_the_chosen_corner():
    box = webcam.placement(1920, 1080, "bottom_right", 20)
    assert (box["boundsWidth"], box["boundsHeight"]) == (384.0, 216.0)
    assert (box["positionX"], box["positionY"]) == (1920 - 384 - 22, 1080 - 216 - 22)
    assert (webcam.placement(1920, 1080, "top_left", 20)["positionX"], webcam.placement(1920, 1080, "top_left", 20)["positionY"]) == (22.0, 22.0)
    assert webcam.placement(1920, 1080, "top_right", 99)["boundsWidth"] == 960.0  # never more than half the width


class FakeObs:
    alive = True

    def __init__(self, devices):
        self.devices, self.inputs, self.calls = devices, set(), []

    def call(self, request, **d):
        self.calls.append((request, d))
        if request == "GetInputList":
            return {"inputs": [{"inputName": n} for n in self.inputs]}
        if request == "CreateInput":
            self.inputs.add(d["inputName"])
            return {"sceneItemId": 7}
        if request == "RemoveInput":
            self.inputs.discard(d["inputName"])
        if request == "GetInputPropertiesListPropertyItems":
            return {"propertyItems": self.devices}
        if request == "GetSceneItemId":
            return {"sceneItemId": 7}
        if request == "GetVideoSettings":
            return {"baseWidth": 1920, "baseHeight": 1080}
        if request == "GetSceneItemList":
            return {"sceneItems": [{}, {}, {}, {}]}
        return {}

    def sent(self, request):
        return [d for r, d in self.calls if r == request]


DEVICES = [{"itemName": "OBS Virtual Camera", "itemValue": "OBS Virtual Camera:x"},
           {"itemName": "UGREEN Camera 2K", "itemValue": "UGREEN Camera 2K:usb1"},
           {"itemName": "Logi C920", "itemValue": "Logi C920:usb2"}]


def test_show_picks_the_named_camera_puts_it_on_top_and_hide_removes_it():
    obs = FakeObs(DEVICES)
    assert webcam.show(obs, "Laqta", "Logi C920", "top_left", 20)
    assert obs.sent("SetInputSettings")[-1]["inputSettings"] == {"video_device_id": "Logi C920:usb2"}
    assert obs.sent("SetSceneItemIndex")[-1]["sceneItemIndex"] == 3  # above the game captures
    assert obs.sent("SetSceneItemEnabled")[-1]["sceneItemEnabled"] is True
    webcam.hide(obs)
    assert webcam.SOURCE not in obs.inputs


def test_an_unplugged_or_unnamed_camera_falls_back_to_the_first_real_one():
    obs = FakeObs(DEVICES)
    assert webcam.show(obs, "Laqta", "Unplugged cam", "bottom_right", 20)
    assert obs.sent("SetInputSettings")[-1]["inputSettings"]["video_device_id"] == "UGREEN Camera 2K:usb1"


def test_no_camera_shows_nothing_and_leaves_no_source():
    obs = FakeObs([{"itemName": "OBS Virtual Camera", "itemValue": "x"}])
    assert webcam.show(obs, "Laqta", "", "bottom_right", 20) is False
    assert webcam.SOURCE not in obs.inputs


def test_webcam_settings_are_checked():
    import pytest

    good = {**config.DEFAULTS, "webcam": True, "webcam_corner": "top_left", "webcam_size": 25}
    assert config._check(dict(good))["webcam_size"] == 25
    for bad in ({"webcam_corner": "middle"}, {"webcam_size": 80}, {"webcam": "yes"}):
        with pytest.raises(ValueError):
            config._check({**good, **bad})
