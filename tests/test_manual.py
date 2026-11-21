from laqta.core import app
from laqta.core.app import manual_game

GAMES = {"league of legends.exe": {"exe": "League of Legends.exe", "name": "League of Legends", "mode": "events", "plugin": "lol"}}
RUNNING = {10: "IndieGame.exe", 11: "explorer.exe", 12: "League of Legends.exe", 13: "Laqta.exe"}


def test_what_the_hotkey_records():
    assert manual_game((10, "  My Indie Game  "), RUNNING, GAMES) == {"exe": "IndieGame.exe", "name": "My Indie Game", "mode": "session", "manual": True}
    assert manual_game((10, ""), RUNNING, GAMES)["name"] == "IndieGame"
    known = manual_game((12, "League of Legends (TM) Client"), RUNNING, GAMES)
    assert known["name"] == "League of Legends" and known["plugin"] == "lol" and known["mode"] == "session" and known["manual"]
    for front in ((11, "Program Manager"), (13, "Laqta"), None, (99, "gone")):
        assert manual_game(front, RUNNING, GAMES) == {**app.DESKTOP, "mode": "session", "manual": True}


class FakeRecorder:
    def __init__(self):
        self.game, self.calls = None, []

    def game_started(self, game):
        self.calls.append(("start", game["name"]))
        self.game = self.game or game

    def stop_now(self):
        self.calls.append(("stop", self.game["name"]))
        self.game = None

    def game_exited(self):
        self.calls.append(("exited",))


class FakeDetector:
    def __init__(self):
        self.current, self.games = None, GAMES


def core(monkeypatch, front=(10, "My Indie Game")):
    c = app.Core.__new__(app.Core)  # no threads, no OBS: only the hotkey logic
    c.recorder, c.detector, c.toasts = FakeRecorder(), FakeDetector(), []
    c.notify, c.cfg = c.toasts.append, {"record_hotkey": "Ctrl+Alt+F11"}
    c._start_game = lambda game: c.recorder.game_started(game)
    monkeypatch.setattr(app, "foreground_window", lambda: front)
    monkeypatch.setattr(app, "processes", lambda: RUNNING)
    return c


def test_press_to_start_press_again_to_stop(monkeypatch):
    c = core(monkeypatch)
    c._toggle_manual()
    assert c.recorder.calls == [("start", "My Indie Game")] and "Ctrl+Alt+F11" in c.toasts[-1]
    c._toggle_manual()
    assert c.recorder.calls[-1] == ("stop", "My Indie Game") and c.recorder.game is None


def test_a_known_game_already_recording_is_left_alone(monkeypatch):
    c = core(monkeypatch)
    c.recorder.game = {"exe": "cs2.exe", "name": "Counter-Strike 2"}
    c._toggle_manual()
    assert c.recorder.calls == [] and "Counter-Strike 2" in c.toasts[-1]


def test_a_game_that_started_during_a_manual_recording_is_recorded_once_it_stops(monkeypatch):
    c = core(monkeypatch)
    c._toggle_manual()
    c.detector.current = GAMES["league of legends.exe"]
    c._toggle_manual()
    assert c.recorder.calls[-2:] == [("stop", "My Indie Game"), ("start", "League of Legends")]
