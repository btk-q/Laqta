import base64
import json

from laqta.obs_controller.capture import Capture, source
from laqta.obs_controller.client import ObsError

WINDOW = "League of Legends (TM) Client:RiotWindowClass:League of Legends.exe"
GAME = {"exe": "League of Legends.exe", "name": "League of Legends"}


def ppm(value: int) -> str:
    return "data:image/ppm;base64," + base64.b64encode(b"P6\n64 36\n255\n" + bytes([value]) * (64 * 36 * 3)).decode()


class FakeObs:
    def __init__(self, working=("window_capture", "monitor_capture"), window=WINDOW):
        self.working, self.window = working, window
        self.settings, self.enabled = {}, {}

    def call(self, request, **d):
        match request:
            case "GetInputPropertiesListPropertyItems" if d["propertyName"] == "window":
                others = [{"itemValue": "Claude:Chrome_WidgetWin_1:claude.exe"}]
                return {"propertyItems": others + ([{"itemValue": self.window}] if self.window else [])}
            case "GetInputPropertiesListPropertyItems":
                return {"propertyItems": [{"itemValue": "DUMMY"}, {"itemValue": r"\\?\DISPLAY#1"}]}
            case "SetInputSettings":
                self.settings[d["inputName"]] = d["inputSettings"]
            case "GetSceneItemId":
                return {"sceneItemId": d["sourceName"]}
            case "SetSceneItemEnabled":
                self.enabled[d["sceneItemId"]] = d["sceneItemEnabled"]
            case "GetSourceScreenshot":
                kind = d["sourceName"].removeprefix("Laqta ")
                if kind == "game_capture" and kind not in self.working:
                    raise ObsError(request, 702, "not renderable")  # unhooked game capture has no size
                return {"imageData": ppm(90 if kind in self.working else 0)}
        return {}

    def active(self):
        return [k.removeprefix("Laqta ") for k, on in self.enabled.items() if on]


def run(cap, max_checks=12):
    for n in range(max_checks):
        if not cap.check():
            return n + 1
    raise AssertionError("never settled")


def test_blocked_game_capture_falls_back_and_is_remembered(tmp_path):
    obs, store, toasts = FakeObs(), tmp_path / "capture.json", []
    cap = Capture(obs, "Laqta", store, toasts.append)
    cap.start(GAME)
    assert obs.active() == ["game_capture"]
    assert obs.settings[source("game_capture")] == {"window": WINDOW, "priority": 2, "capture_mode": "window"}

    assert run(cap) == 3  # two black looks, then one good look at window capture
    assert obs.active() == ["window_capture"] and obs.settings[source("window_capture")]["method"] == 2
    assert json.loads(store.read_text()) == {"league of legends.exe": "window_capture"} and not toasts

    obs2 = FakeObs()
    cap2 = Capture(obs2, "Laqta", store, toasts.append)
    cap2.start(GAME)  # next launch starts with what worked: no black seconds
    assert obs2.active() == ["window_capture"] and run(cap2) == 1


def test_working_game_capture_is_kept(tmp_path):
    obs = FakeObs(working=("game_capture",))
    cap = Capture(obs, "Laqta", tmp_path / "c.json", lambda m: None)
    cap.start(GAME)
    assert run(cap) == 1 and obs.active() == ["game_capture"]


def test_falls_through_to_display_capture_and_reports_total_failure(tmp_path):
    obs = FakeObs(working=("monitor_capture",))
    cap = Capture(obs, "Laqta", tmp_path / "c.json", lambda m: None)
    cap.start(GAME)
    run(cap)
    assert obs.active() == ["monitor_capture"] and obs.settings[source("monitor_capture")] == {"monitor_id": r"\\?\DISPLAY#1"}

    toasts = []
    cap = Capture(FakeObs(working=()), "Laqta", tmp_path / "d.json", toasts.append)
    cap.start(GAME)
    run(cap)
    assert len(toasts) == 1 and "black" in toasts[0] and not (tmp_path / "d.json").exists()


def test_waits_for_the_game_window_to_appear(tmp_path):
    obs = FakeObs(working=("game_capture",), window=None)
    cap = Capture(obs, "Laqta", tmp_path / "c.json", lambda m: None)
    cap.start(GAME)
    assert obs.active() == [] and cap.check() is True  # still no window
    obs.window = WINDOW
    assert run(cap) == 2 and obs.active() == ["game_capture"]


def test_per_game_override_in_config_wins(tmp_path):
    obs = FakeObs()
    cap = Capture(obs, "Laqta", tmp_path / "c.json", lambda m: None)
    cap.start({**GAME, "capture": "monitor_capture"})
    assert obs.active() == ["monitor_capture"]
