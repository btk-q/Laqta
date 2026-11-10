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

    def __init__(self, recording=False, lose_first_sets=0, encoders=("obs_nvenc_h264_tex", "obs_x264")):
        self.calls = []
        self.encoders, self.loaded_encoder, self.replay_active, self.muted = set(encoders), None, False, {}
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
                    self.loaded_rb = self.params.get(("AdvOut", "RecRB")) == "true"
                    self.loaded_encoder = self.params.get(("AdvOut", "RecEncoder"))
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
                return {"outputActive": self.replay_active}
            case "StartReplayBuffer":
                self.replay_active = self.loaded_encoder in self.encoders  # the request succeeds either way
            case "StopReplayBuffer":
                self.replay_active = False
            case "SetInputMute":
                self.muted[d["inputName"]] = d["inputMuted"]
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


def ensure(fake, tmp_path, **kw):
    return setup.ensure_laqta(fake, tmp_path / "out", profile_dir=tmp_path / "profile", **kw)


def test_ensure_laqta_creates_everything_once(tmp_path):
    fake = FakeObs()
    assert ensure(fake, tmp_path) == "obs_nvenc_h264_tex"
    assert (fake.profile, fake.collection) == (setup.NAME, setup.NAME)
    assert fake.params[("Output", "Mode")] == "Advanced" and fake.params[("AdvOut", "RecRB")] == "true"
    assert fake.params[("AdvOut", "RecTracks")] == "3"  # mix + game; no microphone track
    assert fake.scenes == [setup.NAME]
    assert fake.inputs == ["Laqta game_capture", "Laqta window_capture", "Laqta monitor_capture",
                           "Laqta desktop audio", "Laqta game audio", "Laqta microphone"]
    assert fake.muted == {"Laqta game audio": True, "Laqta microphone": True}  # only desktop audio is live at first
    assert fake.loaded_rb and fake.record_dir == str(tmp_path / "out")
    assert (fake.video["baseWidth"], fake.video["outputHeight"], fake.video["fpsNumerator"]) == (3440, 1440, 60)  # primary monitor
    assert fake.transforms[1]["boundsWidth"] == 3440
    assert (fake.transforms[1]["positionX"], fake.transforms[1]["positionY"]) == (0, 0)
    encoder = json.loads((tmp_path / "profile" / "recordEncoder.json").read_text())
    assert encoder["rate_control"] == "CQP" and encoder["lookahead"] is False

    # OBS re-centres items when the canvas changes shape; the next setup must put them back.
    fake.transforms[1]["positionX"] = 440.0
    ensure(fake, tmp_path)
    assert fake.transforms[1]["positionX"] == 0

    fake.calls.clear()
    ensure(fake, tmp_path)  # second run changes nothing
    assert not {"CreateProfile", "SetProfileParameter", "SetCurrentProfile", "SetVideoSettings", "CreateScene", "CreateInput", "SetSceneItemTransform"} & set(fake.calls)


def test_ensure_laqta_survives_obs_dropping_settings_on_a_new_profile(tmp_path):
    fake = FakeObs(lose_first_sets=4)  # what OBS 33 did on first run
    ensure(fake, tmp_path)
    assert fake.params[("AdvOut", "RecRB")] == "true" and fake.loaded_rb


def test_ensure_laqta_refuses_to_take_over_a_live_recording(tmp_path):
    fake = FakeObs(recording=True)
    with pytest.raises(setup.ObsBusy):
        ensure(fake, tmp_path)
    assert fake.profile == "Untitled"


def test_encoder_falls_back_until_one_starts_and_is_remembered(tmp_path):
    store = tmp_path / "obs.json"
    fake = FakeObs(encoders={"obs_x264"})  # no GPU encoder on this machine
    assert ensure(fake, tmp_path, store=store) == "obs_x264"
    assert fake.params[("AdvOut", "RecEncoder")] == "obs_x264"
    assert json.loads(store.read_text())["encoder"] == "obs_x264"
    assert json.loads((tmp_path / "profile" / "recordEncoder.json").read_text())["rate_control"] == "CRF"
    assert not fake.replay_active  # the probe cleans up after itself

    fake.calls.clear()
    assert ensure(fake, tmp_path, store=store) == "obs_x264"  # remembered: no probing, no reloads
    assert "StartReplayBuffer" not in fake.calls and "SetCurrentProfile" not in fake.calls


def test_no_working_encoder_is_an_error_not_a_silent_recording(tmp_path):
    with pytest.raises(ObsError):
        ensure(FakeObs(encoders=set()), tmp_path)


def test_microphone_adds_a_third_track_and_quality_changes_reload_the_encoder(tmp_path):
    fake = FakeObs()
    ensure(fake, tmp_path, mic=True, quality="HQ")
    assert fake.params[("AdvOut", "RecTracks")] == "7"
    assert json.loads((tmp_path / "profile" / "recordEncoder.json").read_text())["cqp"] == 18
    fake.calls.clear()
    ensure(fake, tmp_path, mic=True, quality="Stream")
    assert "SetCurrentProfile" in fake.calls  # OBS only reads encoder settings when the profile loads
    assert json.loads((tmp_path / "profile" / "recordEncoder.json").read_text())["cqp"] == 28
