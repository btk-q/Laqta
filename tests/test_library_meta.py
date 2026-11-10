import datetime
import json
from pathlib import Path

import pytest

from laqta import i18n
from laqta.core.events import GameEvent
from laqta.core.recorder import Recorder
from laqta.storage import clips, library
from laqta.storage.library import Clip
from laqta.ui.library import caption, summary_text
from tests.test_recorder import EVENTS, HYBRID, SESSION, Rig

NOW = datetime.datetime(2026, 11, 11, 12, 0)


def video(tmp_path, name="a.mkv", game="Counter-Strike 2", meta=None):
    folder = tmp_path / game / "2026-11-11"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x")
    if meta is not None:
        path.with_suffix(".json").write_text(json.dumps(meta))
    return path


def test_favourites_and_tags_are_stored_in_the_sidecar_and_survive_a_rescan(tmp_path):
    path = video(tmp_path, meta={"game": "Counter-Strike 2", "events": [{"type": "kill", "offset": 3}], "seconds": 20})
    library.set_meta(path, favorite=True, tags=[" clutch ", "", "ace", "clutch", "x" * 90])
    (clip,) = library.scan(tmp_path)
    assert clip.favorite and clip.tags == ("clutch", "ace", "x" * 40)  # trimmed, de-duplicated, capped
    assert clip.event_types == {"kill"} and clip.seconds == 20  # nothing else in the sidecar was lost
    library.set_meta(path, favorite=False)
    assert not library.scan(tmp_path)[0].favorite and library.scan(tmp_path)[0].tags == clip.tags


def test_tagging_an_old_recording_with_no_sidecar_creates_one(tmp_path):
    path = video(tmp_path, name="2026-11-10 15-45-12.mkv", game="League of Legends")
    library.set_meta(path, tags=["funny"])
    (clip,) = library.scan(tmp_path)
    assert (clip.game, clip.kind, clip.tags) == ("League of Legends", "recording", ("funny",))


def test_recordings_say_what_they_are_so_a_length_does_not_make_them_clips(tmp_path):
    path = video(tmp_path, name="2026-11-11 10-00-00.mkv")
    clips.write_sidecar(path, {"exe": "cs2.exe", "name": "Counter-Strike 2", "mode": "session"}, [], start=0.0, seconds=1800.0, kind="recording")
    (clip,) = library.scan(tmp_path)
    assert (clip.kind, clip.seconds) == ("recording", 1800.0)


def clip(**kw):
    base = dict(path=Path("Replay 1.mkv"), game="Counter-Strike 2", when=NOW, size=10, kind="clip")
    return Clip(**{**base, **kw})


def test_text_search_covers_game_tags_events_and_file_name():
    c = clip(tags=("clutch", "dust2"), events=({"type": "ace", "offset": 1},))
    for query in ("counter", "CLUTCH", "ace", "replay", "dust2 ace", "  strike   clutch "):
        assert library.matches(c, text=query, now=NOW), query
    for query in ("valorant", "clutch mirage"):
        assert not library.matches(c, text=query, now=NOW), query
    assert not library.matches(c, favorite=True, now=NOW)
    assert library.matches(clip(favorite=True), favorite=True, now=NOW)


def test_cleanup_never_deletes_a_favourite():
    old = NOW - datetime.timedelta(days=400)
    keep = clip(path=Path("best.mkv"), when=old, size=50 * 1024**3, favorite=True)
    junk = clip(path=Path("junk.mkv"), when=old, size=1024**3)
    assert [c.path.name for c in library.to_delete([keep, junk], max_age_days=30, max_gb=1, now=NOW)] == ["junk.mkv"]


def test_match_summary_counts_and_result():
    events = tuple({"type": t, "offset": i} for i, t in enumerate(["match_start", "kill", "kill", "death", "assist", "kill", "win", "match_end"]))
    assert library.summary(clip(events=events, seconds=1500)) == {"kills": 3, "deaths": 1, "assists": 1, "result": "win", "seconds": 1500}
    assert library.summary(clip(events=({"type": "manual", "offset": 1},))) is None  # nothing to summarise
    assert library.summary(clip())  is None


def test_summary_line_and_star_in_both_languages():
    i18n.set_language("en")
    events = tuple({"type": t, "offset": 1} for t in ("kill", "kill", "death", "lose"))
    c = clip(events=events, seconds=754, tags=("ranked",), favorite=True)
    assert summary_text(c) == "12:34     Kills 2, deaths 1, assists 0     Defeat     Tags: ranked"
    assert caption(c, NOW.date()).startswith("★ Counter-Strike 2\n")
    i18n.set_language("ar")
    assert "خسارة" in summary_text(c) and "ranked" in summary_text(c)
    i18n.set_language("en")


# -- instant replay ---------------------------------------------------------------------------------


@pytest.fixture
def rig():
    return Rig()


def replay(rig, t, seconds=30):
    return rig.rec.instant_replay(GameEvent("replay", 100.0 + t, source="manual"), seconds)


def test_instant_replay_saves_the_last_seconds_at_once_with_no_post_roll(rig):
    rig.rec.game_started(EVENTS)
    rig.at(40)
    assert replay(rig, 40) is True
    assert rig.obs.count("SaveReplayBuffer") == 1  # immediately, not after a post-roll
    rig.rec.obs_event("ReplayBufferSaved", {"savedReplayPath": "C:/v/r.mkv"})
    (path, game, events, keep, pre), = rig.clips
    assert (keep, pre, [e.type for e in events]) == (30, 30, ["replay"])  # the press is the last frame


def test_instant_replay_absorbs_a_clip_that_was_still_waiting(rig):
    rig.rec.game_started(EVENTS)
    rig.ev(10)  # a kill opens a clip that would save at t=15
    rig.at(12)
    assert replay(rig, 12) is True
    rig.at(20)
    assert rig.obs.count("SaveReplayBuffer") == 1  # one file, not two overlapping ones
    rig.rec.obs_event("ReplayBufferSaved", {"savedReplayPath": "C:/v/r.mkv"})
    (_, _, events, keep, pre), = rig.clips
    assert [e.type for e in events] == ["kill", "replay"] and (keep, pre) == (30, 28)  # the kill sits 28 s in


def test_instant_replay_is_capped_by_the_buffer_and_refused_while_one_is_being_written(rig):
    rig.rec.game_started(EVENTS)
    assert replay(rig, 5, seconds=999) is True
    assert rig.rec._pending[2] == rig.rec.replay_seconds
    assert replay(rig, 6) is False and rig.obs.count("SaveReplayBuffer") == 1


def test_instant_replay_marks_the_moment_while_recording_and_does_nothing_otherwise(rig):
    assert replay(rig, 0) is False  # no game
    rig.rec.game_started(HYBRID)
    assert replay(rig, 50) is True and [m.type for m in rig.rec._marks] == ["replay"]
    assert rig.obs.count("SaveReplayBuffer") == 0
    other = Rig()
    other.rec.game_started(SESSION)
    assert other.rec.instant_replay(GameEvent("replay", 150.0), 30) is False  # plain session: nothing to mark


def test_recorder_exposes_no_new_required_arguments():
    assert Recorder.instant_replay.__name__ == "instant_replay"
