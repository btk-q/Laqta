"""Rocket League plugin against messages built by hand from the published format.
NOT yet checked against a capture from a real match: replace or extend with one when it exists."""

import json
import socket
import threading
import time

import pytest

from laqta.plugins import PLUGINS, rocket_league
from laqta.plugins.rocket_league import Decoder, RocketLeaguePlugin, Tracker, enable, read_ini

ME = {"Name": "Fahd", "PrimaryId": "Steam|76561190000000001|0", "Shortcut": 1, "TeamNum": 0}
MATE = {"Name": "Mate", "PrimaryId": "Epic|abcdef|0", "Shortcut": 2, "TeamNum": 0}
RIVAL = {"Name": "Rival", "PrimaryId": "Steam|76561190000000009|0", "Shortcut": 3, "TeamNum": 1}
STATE = {"Event": "UpdateState", "Data": {"Players": [ME, MATE, RIVAL], "Game": {"TimeSeconds": 300}}}


def who(player):
    return {k: player[k] for k in ("Name", "Shortcut", "TeamNum")}


def goal(scorer, assister=None):
    data = {"Scorer": who(scorer), "GoalSpeed": 88.0}
    if assister:
        data["Assister"] = who(assister)
    return {"Event": "GoalScored", "Data": data}


def stat(name, player):
    return {"Event": "StatfeedEvent", "Data": {"EventName": name, "Type": name, "MainTarget": who(player)}}


def run(tracker, *messages):
    return [[e.type for e in tracker.update(m, float(i))] for i, m in enumerate(messages)]


def mine():
    return Tracker({"76561190000000001"})


def test_a_whole_match():
    out = run(mine(), {"Event": "MatchCreated", "Data": {}}, STATE, goal(ME), goal(MATE, ME), goal(RIVAL), stat("EpicSave", ME),
              stat("Demolish", ME), stat("Save", RIVAL), stat("Goal", ME), {"Event": "MatchEnded", "Data": {"WinnerTeamNum": 0}},
              {"Event": "PodiumStart", "Data": {}}, STATE, {"Event": "MatchDestroyed", "Data": {}})
    assert out == [["match_start"], [], ["goal"], ["assist"], [], ["save"], ["demolition"], [], [], ["win", "match_end"], [], [], []]


def test_losing_and_the_next_match():
    t = mine()
    assert run(t, STATE, {"Event": "MatchEnded", "Data": {"WinnerTeamNum": 1}}, STATE) == [["match_start"], ["lose", "match_end"], []]
    assert run(t, {"Event": "MatchDestroyed", "Data": {}}, {"Event": "MatchCreated", "Data": {}}, STATE) == [[], ["match_start"], []]


def test_leaving_early_or_the_game_closing_ends_the_match_once():
    t = mine()
    assert run(t, STATE, {"Event": "MatchDestroyed", "Data": {}}) == [["match_start"], ["match_end"]]
    run(t, STATE)
    assert [e.type for e in t.gone(9.0)] == ["match_end"] and t.gone(10.0) == []


def test_who_i_am_by_typed_name_by_id_fragment_or_not_at_all():
    for tracker in (Tracker(name=" fahd "), Tracker(name="76561190000000001"), Tracker({"x"}, name="Fahd")):
        assert run(tracker, STATE, goal(ME))[1] == ["goal"]
    assert run(Tracker(name="Mate"), STATE, goal(ME, MATE))[1] == ["assist"]
    unknown = Tracker()
    assert run(unknown, STATE, goal(ME), {"Event": "MatchEnded", "Data": {"WinnerTeamNum": 0}}) == [["match_start"], [], ["match_end"]]


def test_a_same_named_player_on_the_other_team_is_not_me():
    twin = {**RIVAL, "Name": "Fahd"}
    state = {"Event": "UpdateState", "Data": {"Players": [ME, twin]}}
    assert run(mine(), state, goal(twin), goal(ME)) == [["match_start"], [], ["goal"]]


def test_odd_messages_are_survived():
    t = mine()
    odd = [{}, {"Event": "UpdateState"}, {"Event": "UpdateState", "Data": "not json"}, {"Event": "GoalScored", "Data": {"Scorer": "x"}},
           {"Event": "UpdateState", "Data": {"Players": [None, 3]}}, {"Event": "MatchEnded", "Data": {"WinnerTeamNum": None}}, {"Event": 5, "Data": []}]
    for message in odd:
        t.update(message, 0.0)
    wrapped = {"event": "UpdateState", "data": json.dumps(STATE["Data"])}  # lowercase keys, data inside a string
    assert run(mine(), wrapped, goal(ME))[1] == ["goal"]


def test_decoder_splits_back_to_back_json_arriving_in_any_pieces():
    stream = (json.dumps(STATE) + json.dumps(goal(ME)) + "\n" + json.dumps({"Event": "X", "Data": {"s": "} {"}})).encode()
    for size in (1, 7, 64, len(stream)):
        decoder, got = Decoder(), []
        for i in range(0, len(stream), size):
            got += decoder.feed(stream[i : i + size])
        assert [m["Event"] for m in got] == ["UpdateState", "GoalScored", "X"], size
    decoder = Decoder()
    assert decoder.feed(b'ore":1}}' + json.dumps(goal(ME)).encode()) == [] and decoder.feed(json.dumps(goal(ME)).encode()) == [goal(ME)]


REAL_INI = """[TAGame.MatchStatsExporter_TA]
Port=49123
WebPort=49124
PacketSendRate=0

[IniVersion]
0=1785935245.000000
"""  # exactly as Rocket League wrote it on this PC


def test_enable_changes_only_the_one_line(tmp_path):
    path = tmp_path / "TAStatsAPI.ini"
    path.write_text(REAL_INI, encoding="utf-8")
    assert read_ini(path) == {"Port": 49123, "PacketSendRate": 0}
    assert enable(path) and path.read_text(encoding="utf-8") == REAL_INI.replace("PacketSendRate=0", "PacketSendRate=2")
    assert not enable(path)  # already on: nothing to do


def test_enable_respects_a_rate_the_user_chose_and_copes_with_missing_lines(tmp_path):
    path = tmp_path / "TAStatsAPI.ini"
    path.write_text(REAL_INI.replace("PacketSendRate=0", "PacketSendRate=30").replace("49123", "50000"), encoding="utf-8")
    assert not enable(path) and read_ini(path) == {"Port": 50000, "PacketSendRate": 30}
    path.write_text("[TAGame.MatchStatsExporter_TA]\nPort=49123\n", encoding="utf-8")
    assert enable(path) and read_ini(path)["PacketSendRate"] == 2 and "Port=49123" in path.read_text(encoding="utf-8")
    path.write_text("", encoding="utf-8")
    assert enable(path) and read_ini(path)["PacketSendRate"] == 2
    with pytest.raises(OSError):
        enable(tmp_path / "missing.ini")


def test_setup_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(rocket_league, "ini_path", lambda: tmp_path / "TAStatsAPI.ini")
    monkeypatch.setattr(rocket_league, "steam_ids", lambda: set())
    plugin = RocketLeaguePlugin()
    assert "once" in plugin.setup({})  # the game has never run: no file to edit
    (tmp_path / "TAStatsAPI.ini").write_text(REAL_INI.replace("49123", "50123"), encoding="utf-8")
    assert "Restart" in plugin.setup({}) and plugin._port == 50123
    assert "name" in plugin.setup({})  # on now, but nobody to recognise
    assert plugin.setup({"player": "Fahd"}) is None


def test_plugin_reads_a_live_socket_and_survives_the_game_closing():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    events = []
    plugin = RocketLeaguePlugin()
    plugin._port = server.getsockname()[1]
    rocket_league_ids = rocket_league.steam_ids
    rocket_league.steam_ids = lambda: {"76561190000000001"}
    try:
        plugin.start(events.append, {"exe": "RocketLeague.exe"})
        link, _ = server.accept()
        link.sendall(json.dumps(STATE).encode() + json.dumps(goal(ME)).encode()[:20])
        time.sleep(0.2)
        link.sendall(json.dumps(goal(ME)).encode()[20:])
        deadline = time.time() + 3
        while len(events) < 2 and time.time() < deadline:
            time.sleep(0.05)
        link.close()  # the game closes
        while len(events) < 3 and time.time() < deadline + 3:
            time.sleep(0.05)
    finally:
        rocket_league.steam_ids = rocket_league_ids
        plugin.stop()
        plugin.stop()
        server.close()
    assert [e.type for e in events] == ["match_start", "goal", "match_end"]
    assert not any(th.name == "laqta-rocket-league" for th in threading.enumerate())


def test_registered_with_names_for_its_events():
    from laqta import i18n

    assert PLUGINS["rocket_league"] is RocketLeaguePlugin
    for event in RocketLeaguePlugin.event_types:
        assert i18n.t(f"event.{event}") != f"event.{event}"


def test_a_rocket_league_recording_is_summarised_in_goals_not_kills(tmp_path):
    from pathlib import Path

    from laqta import i18n
    from laqta.storage.library import Clip, summary
    from laqta.ui.library import summary_text

    i18n.set_language("en")
    events = [{"type": x, "offset": 1.0} for x in ("match_start", "goal", "goal", "assist", "save", "win", "match_end")]
    clip = Clip(Path("a.mkv"), "Rocket League", 0.0, 1, "recording", events, 330.0, [], False)
    assert summary(clip)["goals"] == 2 and "Goals 2, assists 1, saves 1" in summary_text(clip) and "Victory" in summary_text(clip)


def test_a_short_typed_name_does_not_match_inside_another_players_id():
    """Review finding: the name "a" or "steam" matched every player whose id contained it."""
    for name in ("a", "steam", "epic", "0", "7656"):
        assert run(Tracker(name=name), STATE, goal(ME), goal(MATE), goal(RIVAL)) == [["match_start"], [], [], []], name
