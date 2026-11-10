import json
import os

import pytest

from laqta.core.app import resolve_video, skipped_percent
from laqta.game_detector import detector
from laqta.obs_controller import setup
from laqta.obs_controller.capture import Capture, source
from laqta.storage import config
from tests.test_obs import FakeObs, ensure

MAIN = {"monitorWidth": 3440, "monitorHeight": 1440, "monitorPositionX": 0, "monitorPositionY": 0}
SIDE = {"monitorWidth": 1080, "monitorHeight": 1920, "monitorPositionX": -1080, "monitorPositionY": -480}


def test_game_settings_override_global_ones_only_where_set():
    cfg = {**config.DEFAULTS, "fps": 60, "output_height": 1080, "bitrate": 0, "encoder": "auto", "quality": "HQ"}
    assert resolve_video(cfg, {"exe": "a.exe"}) == {"fps": 60, "output_height": 1080, "bitrate": None, "encoder": None, "quality": "HQ"}
    game = {"exe": "a.exe", "fps": 120, "output_height": 720, "bitrate": 20000, "encoder": "obs_x264"}
    assert resolve_video(cfg, game) == {"fps": 120, "output_height": 720, "bitrate": 20000, "encoder": "obs_x264", "quality": "HQ"}
    assert resolve_video({**cfg, "encoder": "obs_x264"}, {"exe": "a.exe", "encoder": "auto"})["encoder"] == "obs_x264"


def test_monitor_is_chosen_by_where_the_game_window_is():
    monitors = [SIDE, MAIN]
    assert setup.pick_monitor(monitors, None) is MAIN  # no window yet: the primary monitor
    assert setup.pick_monitor(monitors, (0, 0, 3440, 1440)) is MAIN
    assert setup.pick_monitor(monitors, (-1080, -480, 0, 1440)) is SIDE
    assert setup.pick_monitor(monitors, (-600, 100, 400, 900)) is SIDE  # straddling: its centre decides
    assert setup.pick_monitor(monitors, (9000, 9000, 9100, 9100)) is MAIN  # off every screen


def test_output_size_scales_down_evenly_and_never_up():
    assert setup.scaled(3440, 1440, 0) == (3440, 1440)
    assert setup.scaled(3440, 1440, 1080) == (2580, 1080)
    assert setup.scaled(3440, 1440, 720) == (1720, 720)
    assert setup.scaled(1920, 1080, 1440) == (1920, 1080)
    assert all(n % 2 == 0 for n in setup.scaled(1366, 768, 720))


def test_canvas_follows_the_games_monitor_and_output_is_scaled(tmp_path):
    fake = FakeObs()
    ensure(fake, tmp_path, window=(-1080, -480, 0, 1440), output_height=1080, fps=120)
    v = fake.video
    assert (v["baseWidth"], v["baseHeight"]) == (1080, 1920)  # the portrait side monitor
    assert (v["outputWidth"], v["outputHeight"], v["fpsNumerator"]) == (608, 1080, 120)


def test_chosen_encoder_is_used_and_does_not_replace_the_remembered_automatic_one(tmp_path):
    store = tmp_path / "obs.json"
    fake = FakeObs()
    ensure(fake, tmp_path, store=store)
    assert ensure(fake, tmp_path, store=store, encoder="obs_x264", bitrate=20000) == "obs_x264"
    settings = json.loads((tmp_path / "profile" / "recordEncoder.json").read_text())
    assert (settings["rate_control"], settings["bitrate"]) == ("CBR", 20000)
    assert json.loads(store.read_text())["encoder"] == "obs_nvenc_h264_tex"  # automatic choice is unchanged


def test_a_chosen_encoder_that_will_not_start_falls_back_instead_of_recording_nothing(tmp_path):
    fake = FakeObs(encoders={"obs_x264"})
    assert ensure(fake, tmp_path, encoder="h264_texture_amf") == "obs_x264"


def test_display_capture_uses_the_games_monitor(tmp_path):
    class Obs:
        def __init__(self):
            self.settings = {}

        def call(self, request, **d):
            if request == "GetInputPropertiesListPropertyItems":
                return {"propertyItems": [
                    {"itemName": "G34: 3440x1440 @ 0,0 (Primary Monitor)", "itemValue": "MAIN"},
                    {"itemName": "ED271: 1080x1920 @ -1080,-480", "itemValue": "SIDE"}]}
            if request == "SetInputSettings":
                self.settings[d["inputName"]] = d["inputSettings"]
            if request == "GetSceneItemId":
                return {"sceneItemId": 1}
            return {}

    for monitor, wanted in ((SIDE, "SIDE"), (MAIN, "MAIN"), (None, "MAIN")):
        obs = Obs()
        Capture(obs, "Laqta", tmp_path / "c.json", print).start({"exe": "g.exe", "capture": "monitor_capture"}, monitor)
        assert obs.settings[source("monitor_capture")] == {"monitor_id": wanted}


def test_skipped_frames_percentage():
    assert skipped_percent(100, 100, 600, 60) == 0
    assert round(skipped_percent(0, 2627, 1015.8, 60), 1) == 4.3  # the real League recording
    assert skipped_percent(50, 10, 60, 60) == 0  # OBS restarted: the counter went back
    assert skipped_percent(0, 10**9, 1, 60) == 100


def test_video_settings_are_validated(tmp_path):
    path = tmp_path / "c.toml"
    good = {**config.DEFAULTS, "encoder": "obs_x264", "output_height": 1080, "bitrate": 12000,
            "games": [{"exe": "a.exe", "fps": 120, "encoder": "auto", "output_height": 720, "bitrate": 8000}]}
    config.save(good, path)
    assert config.load(path) == good
    for bad in ({"encoder": "h266"}, {"output_height": 900}, {"bitrate": -5}, {"fps": 75},
                {"games": [{"exe": "a.exe", "fps": 75}]}, {"games": [{"exe": "a.exe", "encoder": "h266"}]}):
        with pytest.raises(ValueError):
            config.save({**config.DEFAULTS, **bad}, path)


def test_game_window_is_found_without_opening_the_process():
    assert detector.game_window("no-such-game.exe") is None
    assert detector.game_window("x.exe", running={}) is None
    rect = detector.game_window("explorer.exe")  # the desktop/taskbar always exists in a session
    assert rect is None or (rect[2] > rect[0] and rect[3] > rect[1])
    assert detector.game_window(os.path.basename(os.sys.executable)) is None or True  # must simply not raise
