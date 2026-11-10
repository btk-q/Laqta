"""LoL Live Client. Event shapes follow Riot's published sample
(static.developer.riotgames.com/docs/lol/liveclientdata_events.json); GameEnd and FirstBlood
are not in that sample and still need confirming against a real match."""

from laqta.plugins.lol_live import LolPlugin, Tracker

ME = "Riot Tuxedo#TXC1"


def ev(id, name, **fields):
    return {"EventID": id, "EventName": name, "EventTime": float(id), **fields}


START = [ev(0, "GameStart"), ev(1, "MinionsSpawning")]


def types(events):
    return [e.type for e in events]


def test_first_poll_starts_the_match_and_does_not_replay_history():
    old = START + [ev(2, "ChampionKill", KillerName="Riot Tuxedo", VictimName="Enemy", Assisters=[])]
    tracker = Tracker()
    assert types(tracker.update(old, ME, 0.0)) == ["match_start"]
    assert tracker.update(old, ME, 1.0) == []


def test_kill_death_assist_are_matched_on_game_name_with_or_without_tag():
    tracker = Tracker()
    tracker.update(START, ME, 0.0)
    events = START + [
        ev(2, "ChampionKill", KillerName="Riot Tuxedo", VictimName="Enemy", Assisters=[]),
        ev(3, "ChampionKill", KillerName="Enemy#EUW", VictimName="riot tuxedo#TXC1", Assisters=[]),
        ev(4, "ChampionKill", KillerName="Ally", VictimName="Enemy", Assisters=["Other", "Riot Tuxedo"]),
        ev(5, "ChampionKill", KillerName="Ally", VictimName="Enemy", Assisters=["Other"]),  # not about me
        ev(6, "ChampionKill", KillerName="Riot Tuxedo2", VictimName="Enemy", Assisters=[]),  # similar name
    ]
    assert types(tracker.update(events, ME, 1.0)) == ["kill", "death", "assist"]


def test_events_are_not_repeated_across_polls():
    tracker = Tracker()
    tracker.update(START, ME, 0.0)
    kill = ev(2, "ChampionKill", KillerName="Riot Tuxedo", VictimName="Enemy")
    assert types(tracker.update(START + [kill], ME, 1.0)) == ["kill"]
    assert tracker.update(START + [kill], ME, 2.0) == []
    multi = ev(3, "Multikill", KillerName="Riot Tuxedo", KillStreak=2)
    out = tracker.update(START + [kill, multi], ME, 3.0)
    assert types(out) == ["multikill"] and out[0].data == {"count": 2}


def test_ace_first_blood_objectives_and_other_players_multikills():
    tracker = Tracker()
    tracker.update(START, ME, 0.0)
    out = tracker.update(START + [
        ev(2, "FirstBlood", Recipient="Riot Tuxedo"),
        ev(3, "Multikill", KillerName="Enemy", KillStreak=5),
        ev(4, "Ace", Acer="Riot Tuxedo", AcingTeam="ORDER"),
        ev(5, "DragonKill", KillerName="Riot Tuxedo", DragonType="Earth", Stolen="True", Assisters=[]),
        ev(6, "TurretKilled", KillerName="Minion_T100", TurretKilled="Turret_T2_L_03_A"),
        ev(7, "BaronKill", KillerName="Enemy", Stolen="False"),
    ], ME, 1.0)
    assert types(out) == ["first_blood", "ace", "objective"]
    assert out[2].data == {"kind": "dragon", "stolen": True}


def test_game_end_reports_result_then_match_end_once():
    tracker = Tracker()
    tracker.update(START, ME, 0.0)
    end = START + [ev(2, "GameEnd", Result="Win")]
    assert types(tracker.update(end, ME, 1.0)) == ["win", "match_end"]
    assert tracker.update(end, ME, 2.0) == []
    assert tracker.gone(3.0) == []  # process exits afterwards: already ended


def test_process_dying_mid_match_ends_it_and_next_match_starts_clean():
    tracker = Tracker()
    tracker.update(START + [ev(2, "ChampionKill", KillerName="Riot Tuxedo")], ME, 0.0)
    assert types(tracker.gone(1.0)) == ["match_end"]
    assert tracker.gone(2.0) == []
    assert types(tracker.update(START, ME, 3.0)) == ["match_start"]  # EventIDs restart at 0


def test_joining_after_the_game_already_ended_does_nothing():
    tracker = Tracker()
    assert tracker.update(START + [ev(2, "GameEnd", Result="Lose")], ME, 0.0) == []


class FakeApi:
    def __init__(self):
        self.responses = {}

    def __call__(self, path):
        return self.responses.get(path)


def test_poll_waits_for_api_then_for_player_name():
    api = FakeApi()
    plugin, tracker = LolPlugin(fetch=api), Tracker()
    assert plugin.poll(tracker, None, 0.0) == ([], None)  # connection refused: not in game

    api.responses["eventdata"] = {"Events": []}
    api.responses["activeplayername"] = ""  # loading screen
    assert plugin.poll(tracker, None, 1.0) == ([], None)

    api.responses["activeplayername"] = ME
    api.responses["eventdata"] = {"Events": START}
    events, me = plugin.poll(tracker, None, 2.0)
    assert types(events) == ["match_start"] and me == ME

    api.responses["eventdata"] = {"Events": START + [ev(2, "ChampionKill", KillerName="Riot Tuxedo")]}
    assert types(plugin.poll(tracker, me, 3.0)[0]) == ["kill"]

    api.responses.clear()  # game closed
    events, me = plugin.poll(tracker, me, 4.0)
    assert types(events) == ["match_end"] and me is None


def test_plugin_thread_starts_and_stops_cleanly():
    seen = []
    plugin = LolPlugin(fetch=lambda path: None)
    plugin.start(seen.append, {"exe": "League of Legends.exe"})
    plugin.stop()
    plugin.stop()
    assert seen == []
