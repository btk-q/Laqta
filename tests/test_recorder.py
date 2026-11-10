from pathlib import Path

import pytest

from laqta.core.events import GameEvent
from laqta.core.recorder import Recorder
from laqta.obs_controller.client import ObsDown


class FakeObs:
    def __init__(self):
        self.calls: list[str] = []
        self.active = {"GetRecordStatus": False, "GetReplayBufferStatus": False}
        self.down = False

    def call(self, request, **data):
        if self.down:
            raise ObsDown("gone")
        self.calls.append(request)
        if request in self.active:
            return {"outputActive": self.active[request]}
        if request == "StartRecord":
            self.active["GetRecordStatus"] = True
        if request == "StopRecord":
            self.active["GetRecordStatus"] = False
            return {"outputPath": "C:/v/session.mkv"}
        if request == "StartReplayBuffer":
            self.active["GetReplayBufferStatus"] = True
        if request == "StopReplayBuffer":
            self.active["GetReplayBufferStatus"] = False
        return {}

    def count(self, request):
        return self.calls.count(request)


class Rig:
    def __init__(self):
        self.now = 100.0
        self.obs = FakeObs()
        self.clips, self.recordings, self.toasts, self.prepared = [], [], [], []

        def connect():
            if self.obs.down:
                raise ObsDown("gone")

        self.rec = Recorder(
            self.obs,
            connect=connect,
            prepare=self.prepared.append,
            on_clip=lambda *a: self.clips.append(a),
            on_recording=lambda *a: self.recordings.append(a),
            notify=self.toasts.append,
            clock=lambda: self.now,
        )

    def at(self, t):
        """Advance the clock to 100+t and tick."""
        self.now = 100.0 + t
        self.rec.tick()

    def ev(self, t, type="kill", **kw):
        self.rec.event(GameEvent(type, 100.0 + t, **kw))


@pytest.fixture
def rig():
    return Rig()


SESSION = {"exe": "game.exe", "name": "Game", "mode": "session"}
EVENTS = {"exe": "game.exe", "name": "Game", "mode": "events"}


def test_session_records_until_exit_debounce_expires(rig):
    rig.rec.game_started(SESSION)
    assert rig.rec.state == "active" and rig.obs.count("StartRecord") == 1

    rig.rec.game_exited()
    rig.at(9)
    assert rig.rec.state == "exit_debounce" and rig.obs.count("StopRecord") == 0
    rig.at(10)
    assert rig.rec.state == "idle"
    assert rig.recordings == [(Path("C:/v/session.mkv"), SESSION, [], 100.0)]


def test_game_coming_back_cancels_the_stop(rig):
    rig.rec.game_started(SESSION)
    rig.rec.game_exited()
    rig.at(5)
    rig.rec.game_started(SESSION)
    rig.at(30)
    assert rig.rec.state == "active" and rig.obs.count("StopRecord") == 0
    assert rig.obs.count("StartRecord") == 1


def test_second_game_is_ignored_while_one_is_active(rig):
    rig.rec.game_started(SESSION)
    rig.rec.game_started({"exe": "other.exe", "name": "Other", "mode": "session"})
    assert rig.rec.game is SESSION and rig.prepared == [SESSION]


def test_close_events_merge_into_one_clip_with_post_roll(rig):
    rig.rec.game_started(EVENTS)
    assert rig.obs.count("StartReplayBuffer") == 1
    rig.ev(0)
    rig.ev(3, "death")
    rig.at(7.9)
    assert rig.obs.count("SaveReplayBuffer") == 0  # still inside the second event's post-roll
    rig.at(8)
    rig.at(9)
    assert rig.obs.count("SaveReplayBuffer") == 1

    rig.rec.obs_event("ReplayBufferSaved", {"savedReplayPath": "C:/v/r.mkv"})
    (path, game, events, keep, pre), = rig.clips
    assert path == Path("C:/v/r.mkv") and game is EVENTS
    assert [e.type for e in events] == ["kill", "death"]
    assert (keep, pre) == (15 + 8, 15)  # pre-roll + first event to deadline


def test_merge_is_capped_by_replay_buffer_length(rig):
    rig.rec.game_started(EVENTS)
    for t in range(0, 70, 2):  # an event every 2 s would otherwise never save
        rig.ev(t)
        rig.at(t)
    assert rig.obs.count("SaveReplayBuffer") == 1


def test_duplicates_toggles_and_confidence(rig):
    rig.rec.game_started({**EVENTS, "disabled_events": ["death"]})
    rig.ev(0)
    rig.ev(0.5)  # duplicate trigger
    rig.ev(1, "death")  # toggled off
    rig.ev(2, "assist", confidence=0.2)  # too uncertain
    assert len(rig.rec._events) == 1
    rig.ev(2.5, "manual", confidence=0.0)  # the hotkey always counts
    assert [e.type for e in rig.rec._events] == ["kill", "manual"]


def test_events_are_ignored_in_session_mode_and_when_idle(rig):
    rig.ev(0, "manual")
    rig.rec.game_started(SESSION)
    rig.ev(1, "manual")
    rig.at(20)
    assert rig.obs.count("SaveReplayBuffer") == 0


def test_exit_flushes_a_waiting_clip_and_stops_buffer_after_it_is_saved(rig):
    rig.rec.game_started(EVENTS)
    rig.ev(0)
    rig.rec.game_exited()
    rig.at(2)
    rig.rec.game_exited()  # duplicate exit must not restart the debounce
    rig.at(10)
    assert rig.rec.state == "idle" and rig.obs.count("SaveReplayBuffer") == 1
    assert rig.obs.count("StopReplayBuffer") == 0  # OBS is still writing the clip
    rig.rec.obs_event("ReplayBufferSaved", {"savedReplayPath": "C:/v/r.mkv"})
    assert len(rig.clips) == 1 and rig.obs.count("StopReplayBuffer") == 1


def test_missing_save_report_times_out_with_a_toast(rig):
    rig.rec.game_started(EVENTS)
    rig.ev(0)
    rig.at(5)
    rig.at(16)
    assert rig.toasts == ["OBS did not save the clip"] and rig.rec._pending is None


def test_obs_crash_waits_then_starts_a_new_segment(rig):
    rig.rec.game_started(SESSION)
    rig.obs.down = True
    rig.at(5)  # liveness ping fails
    assert rig.rec.state == "wait_obs"
    rig.at(8)  # retry fails too
    assert rig.rec.state == "wait_obs"

    rig.obs.down = False
    rig.obs.active["GetRecordStatus"] = False  # OBS restarted: nothing is recording
    rig.at(11)
    assert rig.rec.state == "active" and rig.obs.count("StartRecord") == 2


def test_socket_blip_adopts_the_recording_that_is_still_running(rig):
    rig.rec.game_started(SESSION)
    rig.obs.down = True
    rig.at(5)
    rig.obs.down = False  # recording never stopped
    rig.at(8)
    assert rig.rec.state == "active" and rig.obs.count("StartRecord") == 1
    assert rig.prepared == [SESSION]  # not re-prepared: that would refuse a live recording


def test_obs_down_at_game_start_is_retried(rig):
    rig.obs.down = True
    rig.rec.game_started(SESSION)
    assert rig.rec.state == "wait_obs"
    rig.obs.down = False
    rig.at(3)
    assert rig.rec.state == "active"


def test_obs_exit_event_drops_open_clip_with_toast(rig):
    rig.rec.game_started(EVENTS)
    rig.ev(0)
    rig.rec.obs_event("ExitStarted", {})
    assert rig.rec.state == "wait_obs" and rig.toasts == ["Clip lost: OBS went away"]


def test_next_deadline_tracks_the_soonest_thing(rig):
    assert rig.rec.next_deadline() is None
    rig.rec.game_started(EVENTS)
    assert rig.rec.next_deadline() == 105  # liveness
    rig.ev(1)
    rig.ev(2, "death")
    assert rig.rec.next_deadline() == 105
    rig.at(5)
    assert rig.rec.next_deadline() == 107  # clip deadline


HYBRID = {"exe": "game.exe", "name": "Game", "mode": "hybrid"}
MATCH = {"exe": "game.exe", "name": "Game", "mode": "match"}


def test_hybrid_records_the_session_and_marks_events(rig):
    rig.rec.game_started(HYBRID)
    rig.ev(30)
    rig.ev(30.4)  # duplicate
    rig.ev(90, "manual")
    rig.rec.game_exited()
    rig.at(120)
    (path, game, marks, start), = rig.recordings
    assert start == 100.0 and [(m.type, m.t - start) for m in marks] == [("kill", 30), ("manual", 90)]
    assert rig.obs.count("SaveReplayBuffer") == 0


def test_match_mode_records_only_between_match_start_and_end(rig):
    rig.rec.game_started(MATCH)
    assert rig.rec.state == "active" and rig.obs.count("StartRecord") == 0  # in the menu: nothing recorded
    rig.ev(5)  # a kill outside a match is not marked anywhere

    rig.at(10)
    rig.ev(10, "match_start")
    assert rig.obs.count("StartRecord") == 1
    rig.ev(40)
    rig.ev(60, "match_end")
    rig.at(64)
    assert rig.obs.count("StopRecord") == 0  # post-roll still running 
    rig.at(65)
    (path, game, marks, start), = rig.recordings
    assert start == 110.0 and [m.type for m in marks] == ["match_start", "kill", "match_end"]

    rig.ev(200, "match_start")  # second match -> second file
    assert rig.obs.count("StartRecord") == 2
    rig.rec.game_exited()
    rig.at(300)
    assert len(rig.recordings) == 2 and rig.rec.state == "idle"


def test_match_restarting_inside_post_roll_keeps_one_file(rig):
    rig.rec.game_started(MATCH)
    rig.ev(0, "match_start")
    rig.ev(50, "match_end")
    rig.ev(52, "match_start")
    rig.at(80)
    assert rig.obs.count("StopRecord") == 0 and rig.obs.count("StartRecord") == 1


def test_match_that_starts_while_obs_is_down_records_after_reconnect(rig):
    rig.obs.down = True
    rig.rec.game_started(MATCH)
    rig.ev(1, "match_start")
    rig.obs.down = False
    rig.at(3)
    assert rig.rec.recording


def test_obs_crash_mid_match_starts_a_new_segment(rig):
    rig.rec.game_started(MATCH)
    rig.ev(0, "match_start")
    rig.obs.down = True
    rig.at(5)
    rig.obs.down = False
    rig.obs.active["GetRecordStatus"] = False
    rig.at(8)
    assert rig.obs.count("StartRecord") == 2 and rig.rec.recording
