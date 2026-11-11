import pytest

from laqta.obs_controller import client
from laqta.obs_controller.audio import MIC, Shout, mic_peak
from laqta.obs_controller.client import Obs
from laqta.storage import config
from tests.test_obs import FakeClient

LOUD, QUIET = 0.5, 0.05  # -6 dB and -26 dB against the default -12 dB line


def run(shout, levels, step=0.05, start=0.0):
    return [round(start + i * step, 2) for i, level in enumerate(levels) if shout.feed(level, start + i * step)]


def test_a_sustained_loud_voice_is_one_shout():
    assert run(Shout(), [QUIET] * 5 + [LOUD] * 40 + [QUIET] * 5) == [0.35]  # the third loud sample, once


def test_a_click_is_not_a_shout():
    assert run(Shout(), [QUIET, LOUD, LOUD, QUIET, LOUD, QUIET, LOUD, LOUD, QUIET]) == []


def test_shouts_close_together_are_one_moment_and_later_ones_count_again():
    burst = [LOUD] * 5 + [QUIET] * 5
    assert run(Shout(cooldown=10), burst * 3) == [0.1]
    shout = Shout(cooldown=10)
    assert run(shout, burst) == [0.1] and run(shout, burst, start=11.0) == [11.1]


def test_the_level_is_the_users_to_set():
    assert run(Shout(db=-30), [QUIET] * 3) == [0.1]
    shout = Shout()
    shout.db = 0
    assert run(shout, [LOUD] * 10) == [] and shout.level == 1.0
    assert Shout(db=-20).level == pytest.approx(0.1)


def test_mic_peak_reads_the_microphone_only_and_survives_odd_payloads():
    event = {"inputs": [{"inputName": "Laqta desktop audio", "inputLevelsMul": [[0.9, 0.9, 0.9]]},
                        {"inputName": MIC, "inputLevelsMul": [[0.1, 0.2, 0.3], [0.1, 0.2, 0.6]]}]}  # shape captured from OBS 33
    assert mic_peak(event) == 0.6
    for odd in ({}, {"inputs": None}, {"inputs": [{"inputName": MIC}]}, {"inputs": [{"inputName": MIC, "inputLevelsMul": [[]]}]}):
        assert mic_peak(odd) == 0.0


def connect(monkeypatch, meters, on_meters):
    seen = {}

    class Recording(FakeClient):
        def __init__(self, **kwargs):
            seen.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(client.obsws, "ReqClient", FakeClient)
    monkeypatch.setattr(client.obsws, "EventClient", Recording)
    events = []
    obs = Obs(4455, "pw", on_event=lambda name, data: events.append(name), on_meters=on_meters)
    obs.meters = meters
    obs.connect()
    return obs, seen, events


def test_meters_are_only_asked_for_when_wanted(monkeypatch):
    for meters, handler, expected in ((False, print, 0), (True, None, 0), (True, print, 1 << 16)):
        _, seen, _ = connect(monkeypatch, meters, handler)
        assert seen["subs"] & (1 << 16) == expected and seen["subs"] & 2047 == 2047


def test_meter_events_stay_off_the_core_inbox_and_a_bad_handler_cannot_kill_the_event_thread(monkeypatch):
    got = []
    obs, _, events = connect(monkeypatch, True, got.append)
    obs._ev.callback.trigger("InputVolumeMeters", {"inputs": []})
    obs._ev.callback.trigger("RecordStateChanged", {})
    assert got == [{"inputs": []}] and events == ["RecordStateChanged"]
    obs, _, events = connect(monkeypatch, True, lambda data: 1 / 0)
    obs._ev.callback.trigger("InputVolumeMeters", {})  # must not raise


def test_trigger_is_off_by_default_and_checked():
    assert config.DEFAULTS["mic_trigger"] is False
    for bad in ({"mic_trigger": "yes"}, {"mic_trigger_db": 5}):
        with pytest.raises(ValueError):
            config._check({**config.DEFAULTS, **bad})
