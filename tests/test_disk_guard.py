from laqta.core.recorder import DISK_RETRY, GB
from laqta.storage import clips
from tests.test_recorder import Rig

GAME = {"exe": "game.exe", "name": "Game", "mode": "session"}


def rig_with(free_gb, mode="session"):
    rig = Rig()
    rig.free = free_gb * GB
    rig.rec.free_space, rig.rec.min_free = (lambda: rig.free), 5 * GB
    rig.rec.game_started({**GAME, "mode": mode})
    return rig


def test_a_nearly_full_disk_refuses_to_start_and_says_so_once():
    rig = rig_with(2)
    assert rig.rec.state == "low_disk" and not rig.rec.recording and rig.obs.count("StartRecord") == 0
    assert len(rig.toasts) == 1 and "2.0 GB" in rig.toasts[0]
    rig.at(DISK_RETRY + 1)  # looks again, quietly
    rig.at(2 * DISK_RETRY + 2)
    assert len(rig.toasts) == 1 and rig.obs.count("StartRecord") == 0


def test_recording_starts_by_itself_once_space_is_back():
    rig = rig_with(2)
    rig.free = 50 * GB
    rig.at(DISK_RETRY + 1)
    assert rig.rec.state == "active" and rig.rec.recording


def test_a_disk_that_fills_up_mid_recording_stops_cleanly_and_keeps_the_file():
    rig = rig_with(50, mode="hybrid")
    rig.ev(3)
    rig.free = 4 * GB
    rig.at(6)  # the next 5-second look
    assert rig.obs.count("StopRecord") == 1 and rig.rec.state == "low_disk" and not rig.rec.recording
    path, game, marks, _ = rig.recordings[0]
    assert path.name == "session.mkv" and [m.type for m in marks] == ["kill"]  # saved with its marks
    assert len(rig.toasts) == 1
    rig.at(6 + DISK_RETRY + 1)
    assert len(rig.toasts) == 1 and rig.obs.count("StartRecord") == 1  # still full: stays stopped, stays quiet
    rig.free = 50 * GB
    rig.at(6 + 2 * DISK_RETRY + 2)
    assert rig.rec.recording and rig.obs.count("StartRecord") == 2


def test_events_mode_saves_the_open_clip_before_stopping():
    rig = rig_with(50, mode="events")
    rig.ev(4)
    rig.free = 1 * GB
    rig.at(6)
    assert rig.obs.count("SaveReplayBuffer") == 1 and rig.rec.state == "low_disk"


def test_no_limit_or_unknown_space_never_blocks():
    for free, limit in ((0, 0), (None, 5 * GB)):
        rig = Rig()
        rig.rec.free_space, rig.rec.min_free = (lambda: free), limit
        rig.rec.game_started(dict(GAME))
        rig.at(6)
        assert rig.rec.recording and not rig.toasts


def test_the_game_closing_while_stopped_for_disk_ends_quietly():
    rig = rig_with(2)
    rig.rec.game_exited()
    rig.at(11)
    assert rig.rec.state == "idle" and rig.obs.count("StopRecord") == 0


def test_free_bytes_asks_the_nearest_folder_that_exists(tmp_path):
    assert clips.free_bytes(tmp_path / "not" / "made" / "yet") == clips.free_bytes(tmp_path) > 0
