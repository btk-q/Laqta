import os

from laqta.game_detector import detector
from laqta.game_detector.detector import Detector, load_games


def test_builtin_db_loads_with_user_overrides():
    games = load_games(
        [
            {"exe": "Notepad.exe", "name": "Notepad", "mode": "events"},
            {"exe": "CS2.EXE", "mode": "events"},  # override keeps the built-in name/plugin
            {"exe": "dota2.exe", "remove": True},
        ]
    )
    assert games["notepad.exe"]["mode"] == "events"
    assert games["cs2.exe"] == {"exe": "CS2.EXE", "name": "Counter-Strike 2", "plugin": "cs2", "mode": "events"}
    assert games["league of legends.exe"]["mode"] == "session"
    assert "dota2.exe" not in games


def make(snapshot, front=None, **kw):
    return Detector(load_games(), processes=lambda: snapshot["p"], fullscreen=lambda: front and front["f"], **kw)


def test_start_and_exit_are_reported_once():
    snap = {"p": {1: "explorer.exe"}}
    d = make(snap)
    assert d.poll() is None

    snap["p"] = {1: "explorer.exe", 2: "cs2.exe", 3: "dota2.exe"}
    kind, game = d.poll()
    assert (kind, game["name"]) == ("game_started", "Counter-Strike 2")
    assert d.poll() is None  # still running; a second game does not interrupt

    snap["p"] = {1: "explorer.exe", 3: "dota2.exe"}
    assert d.poll() == ("game_exited", game)
    assert d.poll()[1]["name"] == "Dota 2"  # the other game is picked up next


def test_unknown_fullscreen_app_is_asked_about_exactly_once():
    snap = {"p": {1: "explorer.exe", 7: "Hades2.exe"}}
    front = {"f": (7, "Hades II")}
    d = make(snap, front)
    assert d.poll() == ("unknown_fullscreen", {"exe": "Hades2.exe", "title": "Hades II"})
    assert d.poll() is None


def test_never_asks_about_non_games_known_games_or_already_asked():
    snap = {"p": {1: "explorer.exe", 2: "chrome.exe", 3: "Old.exe"}}
    front = {"f": (2, "YouTube")}
    d = make(snap, front, asked=["old.exe"])
    assert d.poll() is None  # browser in fullscreen
    front["f"] = (3, "Old game")
    assert d.poll() is None  # asked in an earlier run
    front["f"] = (99, "ghost")
    assert d.poll() is None  # window of a process that is not in the snapshot
    front["f"] = None
    assert d.poll() is None


def test_does_not_ask_while_a_game_is_being_recorded():
    snap = {"p": {2: "cs2.exe", 7: "Overlay.exe"}}
    d = make(snap, {"f": (7, "Overlay")})
    assert d.poll()[0] == "game_started"
    assert d.poll() is None


def test_real_snapshot_contains_this_process_and_foreground_check_runs():
    running = detector.processes()
    assert running[os.getpid()].lower().startswith("python")
    assert "explorer.exe" in detector.running_exes() or len(running) > 5
    front = detector.foreground_fullscreen()
    assert front is None or (isinstance(front[0], int) and isinstance(front[1], str))
