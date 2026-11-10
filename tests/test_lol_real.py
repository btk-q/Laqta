"""Replay of a real match's event list (ARAM, captured with LAQTA_LOL_DUMP, player names
replaced). Confirms the field names the plugin relies on against what the game really sends."""

import collections
import json
from pathlib import Path

from laqta.plugins.lol_live import Tracker

EVENTS = json.loads((Path(__file__).parent / "fixtures" / "lol_events_real.json").read_text(encoding="utf-8"))["Events"]


def replay(me: str, step: int = 3) -> collections.Counter:
    """Feed the list the way polling sees it: a growing prefix, a few events per poll."""
    tracker, seen = Tracker(), collections.Counter()
    for end in range(2, len(EVENTS) + step, step):  # the first poll sees GameStart + MinionsSpawning
        seen.update(e.type for e in tracker.update(EVENTS[:end], me, float(end)))
    seen.update(e.type for e in tracker.gone(999.0))  # the game process exits
    return seen


def test_real_match_counts_for_the_top_fragger():
    assert replay("Player2") == {
        "match_start": 1, "kill": 18, "death": 12, "assist": 16, "multikill": 8, "ace": 1, "match_end": 1,
    }  # fmt: skip


def test_first_blood_goes_to_its_recipient_only():
    assert replay("Player1")["first_blood"] == 1 and replay("Player2")["first_blood"] == 0


def test_riot_id_with_tag_matches_the_tagless_names_the_game_reports():
    assert replay("Player2#EUW") == replay("Player2") == replay("player2")


def test_every_event_is_reported_once_whatever_the_poll_size():
    assert replay("Player2", step=1) == replay("Player2", step=50)


def test_someone_who_is_not_in_the_match_gets_only_the_match_itself():
    assert replay("Nobody") == {"match_start": 1, "match_end": 1}
