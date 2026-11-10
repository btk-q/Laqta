from laqta.game_detector.detector import Detector, load_games, running_exes


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


def test_start_and_exit_are_reported_once():
    snapshot: set[str] = {"explorer.exe"}
    d = Detector(load_games(), running=lambda: snapshot)
    assert d.poll() is None

    snapshot = {"explorer.exe", "cs2.exe", "dota2.exe"}
    kind, game = d.poll()
    assert (kind, game["name"]) == ("game_started", "Counter-Strike 2")
    assert d.poll() is None  # still running; a second game does not interrupt

    snapshot = {"explorer.exe", "dota2.exe"}
    assert d.poll() == ("game_exited", game)
    assert d.poll()[1]["name"] == "Dota 2"  # the other game is picked up next


def test_real_process_list_is_lowercase_names():
    names = running_exes()
    assert names and all(n == n.lower() for n in names)
