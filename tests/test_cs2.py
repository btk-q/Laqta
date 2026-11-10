"""CS2 GSI. Payloads here are hand-built from the documented shape, NOT captured from a
live match yet; replace/extend with real ones dumped via LAQTA_GSI_DUMP."""

import json
import urllib.error
import urllib.request

import pytest

from laqta.plugins import cs2_gsi
from laqta.plugins.cs2_gsi import Cs2Plugin, Tracker

ME, OTHER = "7656119800000001", "7656119800000002"


def payload(phase="live", round_phase="live", steamid=ME, team="CT", kills=0, hs=0, deaths=0, assists=0,
            win_team=None, ct=0, t=0, no_map=False):
    p = {
        "provider": {"steamid": ME},
        "player": {
            "steamid": steamid,
            "team": team,
            "state": {"health": 100, "round_kills": kills, "round_killhs": hs},
            "match_stats": {"kills": 0, "assists": assists, "deaths": deaths},
        },
    }
    if not no_map:
        p["map"] = {"name": "de_dust2", "mode": "competitive", "phase": phase,
                    "team_ct": {"score": ct}, "team_t": {"score": t}}
        p["round"] = {"phase": round_phase, **({"win_team": win_team} if win_team else {})}
    return p


def run(*payloads):
    tracker, out = Tracker(), []
    for i, p in enumerate(payloads):
        out.append([e.type for e in tracker.update(p, float(i))])
    return out


def test_match_start_once_and_not_again_at_halftime():
    assert run(payload("warmup"), payload("live"), payload("intermission"), payload("live")) == [
        [], ["match_start"], [], []]


def test_kills_headshots_multikill_and_ace():
    assert run(
        payload(),
        payload(kills=1),
        payload(kills=2, hs=1),
        payload(kills=2, hs=1),  # heartbeat: nothing new
        payload(kills=5, hs=1),  # three kills landed in one buffered update
    ) == [["match_start"], ["kill"], ["kill", "headshot", "multikill"], [], ["kill", "ace"]]


def test_new_round_resetting_counters_is_not_a_kill():
    assert run(payload(kills=3), payload(kills=0, round_phase="freezetime"), payload(kills=1))[1:] == [[], ["kill"]]


def test_death_assist_and_spectated_players_kills_are_not_mine():
    assert run(
        payload(),
        payload(assists=1),
        payload(assists=1, deaths=1),
        payload(steamid=OTHER, kills=4, hs=4, deaths=7, assists=9),  # now watching a teammate
        payload(steamid=OTHER, kills=5, hs=5, deaths=7, assists=9),
        payload(deaths=1, assists=1),  # respawned next round, counters unchanged
    )[1:] == [["assist"], ["death"], [], [], []]


def test_warmup_kills_do_not_count():
    assert run(payload("warmup"), payload("warmup", kills=2)) == [[], []]


def test_round_result_uses_my_team_even_while_spectating():
    assert run(
        payload(team="T"),
        payload(steamid=OTHER, team="T", round_phase="over", win_team="T"),
        payload(steamid=OTHER, team="T", round_phase="over", win_team="T"),  # reported once
        payload(team="T", round_phase="freezetime"),
        payload(team="T", round_phase="over", win_team="CT"),
    )[1:] == [["round_win"], [], [], ["round_lose"]]


def test_gameover_reports_result_then_match_end():
    assert run(payload(), payload("gameover", ct=13, t=7))[1] == ["win", "match_end"]
    assert run(payload(team="T"), payload("gameover", team="T", ct=13, t=7))[1] == ["lose", "match_end"]


def test_leaving_mid_match_ends_it_and_a_new_match_can_start():
    assert run(payload(), payload(no_map=True), payload(no_map=True), payload()) == [
        ["match_start"], ["match_end"], [], ["match_start"]]


def test_menu_payload_without_player_is_harmless():
    assert run({"provider": {"steamid": ME}}, {}) == [[], []]


def test_cfg_is_found_installed_once_and_updated_when_changed(tmp_path):
    lib = tmp_path / "lib"
    cfg = lib / "steamapps/common/Counter-Strike Global Offensive/game/csgo/cfg"
    cfg.mkdir(parents=True)
    assert cs2_gsi.cfg_dir([tmp_path / "empty", lib]) is None  # no appmanifest: CS2 not installed there
    (lib / "steamapps/appmanifest_730.acf").write_text("")
    assert cs2_gsi.cfg_dir([tmp_path / "empty", lib]) == cfg

    assert cs2_gsi.install_cfg(cfg, "secret") is True
    text = (cfg / cs2_gsi.CFG_NAME).read_text()
    assert '"uri"       "http://127.0.0.1:37650/"' in text and '"token" "secret"' in text
    assert cs2_gsi.install_cfg(cfg, "secret") is False
    assert cs2_gsi.install_cfg(cfg, "rotated") is True


def test_token_is_created_once(tmp_path):
    first = cs2_gsi.token(tmp_path / "t.txt")
    assert len(first) == 32 and cs2_gsi.token(tmp_path / "t.txt") == first


def post(body: dict) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{cs2_gsi.PORT}/", data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_listener_accepts_only_payloads_with_our_token(monkeypatch, tmp_path):
    monkeypatch.setattr(cs2_gsi.config, "DIR", tmp_path)
    secret, seen = cs2_gsi.token(), []
    plugin = Cs2Plugin()
    plugin.start(seen.append, {"exe": "cs2.exe"})
    try:
        ok = {"auth": {"token": secret}}
        assert post({**payload(), **ok}) == 200
        assert post({**payload(kills=1), "auth": {"token": "wrong"}}) == 403
        assert post(payload(kills=1)) == 403
        assert post({**payload(kills=1), **ok}) == 200
    finally:
        plugin.stop()
        plugin.stop()  # safe twice
    assert [e.type for e in seen] == ["match_start", "kill"]
    with pytest.raises(urllib.error.URLError):
        post({})  # port is released
