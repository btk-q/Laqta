import json
from pathlib import Path

import pytest
from obsws_python.error import OBSSDKRequestError

from laqta.obs_controller import client, setup
from laqta.obs_controller.client import Obs, ObsDown, ObsError


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(setup.time, "sleep", lambda s: None)


class FakeWs:
    def close(self):
        pass


class FakeClient:
    """Stands in for both ReqClient and EventClient."""

    script: list = []  # what send() does next: a dict to return or an exception to raise

    def __init__(self, **kwargs):
        assert kwargs["timeout"], "every OBS call must have a timeout"
        self.base_client = type("B", (), {"ws": FakeWs()})()
        self.worker = type("W", (), {"is_alive": lambda self: True})()

    def send(self, request, data=None, raw=False):
        step = FakeClient.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def obs(monkeypatch):
    monkeypatch.setattr(client.obsws, "ReqClient", FakeClient)
    monkeypatch.setattr(client.obsws, "EventClient", FakeClient)
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    o = Obs(4455, "pw")
    o.connect()
    return o


def test_not_ready_is_retried(obs):
    FakeClient.script = [OBSSDKRequestError("GetSceneList", 207, "busy"), {"scenes": []}]
    assert obs.call("GetSceneList") == {"scenes": []}


def test_refused_request_is_obs_error_and_keeps_connection(obs):
    FakeClient.script = [OBSSDKRequestError("StartRecord", 500, "already active")]
    with pytest.raises(ObsError) as e:
        obs.call("StartRecord")
    assert e.value.code == 500 and obs.alive


def test_dead_socket_becomes_obs_down_and_disconnects(obs):
    FakeClient.script = [BrokenPipeError("gone")]
    with pytest.raises(ObsDown):
        obs.call("GetVersion")
    assert not obs.alive
    with pytest.raises(ObsDown):
        obs.call("GetVersion")


def test_events_arrive_raw(obs):
    seen = []
    obs._on_event = lambda name, data: seen.append((name, data))
    obs.connect()
    obs._ev.callback.trigger("ReplayBufferSaved", {"savedReplayPath": "x.mkv"})
    assert seen == [("ReplayBufferSaved", {"savedReplayPath": "x.mkv"})]


def test_ws_settings(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"server_port": 4456, "server_password": "s", "auth_required": True, "server_enabled": True}))
    assert setup.ws_settings(cfg) == (4456, "s", True)
    cfg.write_text(json.dumps({"server_password": "s", "auth_required": False}))
    assert setup.ws_settings(cfg) == (4455, "", False)


class FakeObs:
    """Minimal in-memory OBS for ensure_laqta."""

    def __init__(self, recording=False, lose_first_sets=0):
        self.calls = []
        self.recording = recording
        self.lose = lose_first_sets  # real OBS drops parameters set while a new profile settles
        self.loaded_rb = False  # was RecRB true when the Laqta profile was last loaded?
        self.video = {"baseWidth": 1920, "baseHeight": 1080, "outputWidth": 1280, "outputHeight": 720,
                      "fpsNumerator": 30, "fpsDenominator": 1}
        self.record_dir = None
        self.transforms = {}
        self.profiles, self.profile = ["Untitled"], "Untitled"
        self.params = {}
        self.collections, self.collection = ["Untitled"], "Untitled"
        self.scenes, self.inputs = [], []

    def call(self, request, **d):
        self.calls.append(request)
        match request:
            case "GetRecordStatus":
                return {"outputActive": self.recording}
            case "GetStreamStatus":
                return {"outputActive": False}
            case "GetProfileList":
                return {"currentProfileName": self.profile, "profiles": list(self.profiles)}
            case "CreateProfile":
                self.profiles.append(d["profileName"])
                self.profile = d["profileName"]
            case "SetCurrentProfile":
                self.profile = d["profileName"]
                if self.profile == setup.NAME:
                    self.loaded_rb = self.params.get(("SimpleOutput", "RecRB")) == "true"
            case "GetProfileParameter":
                return {"parameterValue": self.params.get((d["parameterCategory"], d["parameterName"]))}
            case "SetProfileParameter":
                assert self.profile == setup.NAME, "must never edit the user's own profile"
                if self.lose > 0:
                    self.lose -= 1
                else:
                    self.params[(d["parameterCategory"], d["parameterName"])] = d["parameterValue"]
            case "GetReplayBufferStatus":
                if not self.loaded_rb:
                    raise ObsError(request, 604, "Replay buffer is not available.")
                return {"outputActive": False}
            case "GetVideoSettings":
                return dict(self.video)
            case "SetVideoSettings":
                self.video.update(d)
            case "SetRecordDirectory":
                self.record_dir = d["recordDirectory"]
            case "GetSceneCollectionList":
                return {"currentSceneCollectionName": self.collection, "sceneCollections": list(self.collections)}
            case "CreateSceneCollection" | "SetCurrentSceneCollection":
                self.collection = d["sceneCollectionName"]
                if self.collection not in self.collections:
                    self.collections.append(self.collection)
            case "GetSceneList":
                return {"scenes": [{"sceneName": s} for s in self.scenes]}
            case "CreateScene":
                assert self.collection == setup.NAME, "must never edit the user's own scenes"
                self.scenes.append(d["sceneName"])
            case "GetInputList":
                return {"inputs": [{"inputName": i} for i in self.inputs]}
            case "CreateInput":
                self.inputs.append(d["inputName"])
                return {"sceneItemId": len(self.inputs)}
            case "GetMonitorList":
                return {"monitors": [
                    {"monitorWidth": 1080, "monitorHeight": 1920, "monitorPositionX": -1080, "monitorPositionY": -480},
                    {"monitorWidth": 3440, "monitorHeight": 1440, "monitorPositionX": 0, "monitorPositionY": 0}]}
            case "GetSceneItemId":
                return {"sceneItemId": self.inputs.index(d["sourceName"]) + 1}
            case "GetSceneItemTransform":
                return {"sceneItemTransform": self.transforms.get(d["sceneItemId"], {})}
            case "SetSceneItemTransform":
                self.transforms[d["sceneItemId"]] = d["sceneItemTransform"]
        return {}


def test_ensure_laqta_creates_everything_once(tmp_path):
    fake = FakeObs()
    setup.ensure_laqta(fake, tmp_path / "out")
    assert (fake.profile, fake.collection) == (setup.NAME, setup.NAME)
    assert fake.params[("SimpleOutput", "RecRB")] == "true"
    assert fake.scenes == [setup.NAME] and fake.inputs == ["Laqta game_capture", "Laqta window_capture", "Laqta monitor_capture", "Laqta desktop audio"]
    assert fake.loaded_rb and fake.record_dir == str(tmp_path / "out")
    assert (fake.video["baseWidth"], fake.video["outputHeight"], fake.video["fpsNumerator"]) == (3440, 1440, 60)  # primary monitor
    assert fake.transforms[1]["boundsWidth"] == 3440

    fake.calls.clear()
    setup.ensure_laqta(fake, tmp_path / "out")  # second run changes nothing
    assert not {"CreateProfile", "SetProfileParameter", "SetCurrentProfile", "SetVideoSettings", "CreateScene", "CreateInput", "SetSceneItemTransform"} & set(fake.calls)


def test_ensure_laqta_survives_obs_dropping_settings_on_a_new_profile(tmp_path):
    fake = FakeObs(lose_first_sets=4)  # what OBS 33 did on first run
    setup.ensure_laqta(fake, tmp_path / "out")
    assert fake.params[("SimpleOutput", "RecRB")] == "true" and fake.loaded_rb


def test_ensure_laqta_refuses_to_take_over_a_live_recording(tmp_path):
    fake = FakeObs(recording=True)
    with pytest.raises(setup.ObsBusy):
        setup.ensure_laqta(fake, tmp_path / "out")
    assert fake.profile == "Untitled"
