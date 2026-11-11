import pytest

from laqta import i18n
from laqta.game_detector.detector import load_games
from laqta.storage import config
from laqta.ui.settings import builtin_exes, remove_game, set_game
from laqta.ui.tray import status_text


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def test_set_game_creates_updates_and_clears_fields():
    cfg = {"games": []}
    set_game(cfg, "Hades2.exe", name="Hades II")
    set_game(cfg, "hades2.EXE", mode="events", capture="window_capture", disabled_events=["death"])
    assert cfg["games"] == [{"exe": "Hades2.exe", "name": "Hades II", "mode": "events", "capture": "window_capture", "disabled_events": ["death"]}]
    set_game(cfg, "Hades2.exe", capture=None, disabled_events=[])  # back to automatic / all events
    assert cfg["games"] == [{"exe": "Hades2.exe", "name": "Hades II", "mode": "events"}]


def test_remove_drops_user_games_and_hides_builtin_ones_and_can_be_undone():
    builtin = builtin_exes()
    cfg = {"games": [{"exe": "Hades2.exe", "name": "Hades II"}, {"exe": "cs2.exe", "mode": "events"}]}
    remove_game(cfg, "hades2.exe", builtin)
    remove_game(cfg, "cs2.exe", builtin)
    assert cfg["games"] == [{"exe": "cs2.exe", "remove": True}]
    assert "cs2.exe" not in load_games(cfg["games"]) and "hades2.exe" not in load_games(cfg["games"])
    set_game(cfg, "cs2.exe", mode="match")  # choosing it again brings it back
    assert load_games(cfg["games"])["cs2.exe"]["mode"] == "match"


def test_settings_survive_a_save_and_load(tmp_path):
    path = tmp_path / "config.toml"
    cfg = {**config.DEFAULTS, "language": "ar", "asked": ["hades2.exe"], "games": [{"exe": "cs2.exe", "disabled_events": ["death"], "mode": "match"}]}
    config.save(cfg, path)
    assert config.load(path) == cfg
    with pytest.raises(ValueError):
        config.save({**cfg, "fps": 59}, path)
    assert config.load(path) == cfg  # a rejected save leaves the file untouched


def test_status_text_in_both_languages():
    state = {"state": "active", "game": "CS2", "mode": "match", "recording": False, "obs": True, "error": None}
    assert status_text(state) == "CS2: waiting for a match"
    assert status_text({**state, "recording": True}) == "CS2: recording match"
    assert status_text({**state, "state": "wait_obs", "error": "OBS is off"}) == "CS2: waiting for OBS — OBS is off"
    i18n.set_language("ar")
    assert status_text({**state, "state": "idle", "game": None}) == "في انتظار لعبة"


def test_every_string_exists_in_every_language_with_the_same_placeholders():
    import re

    for key, row in i18n.STRINGS.items():
        assert len(row) == len(i18n.LANGUAGES) and all(row), key
        assert len({tuple(sorted(re.findall(r"{(\w+)}", text))) for text in row}) == 1, key
    assert i18n.t("no.such.key") == "no.such.key"


def test_add_a_game_offers_installed_games_then_running_programs_without_known_ones_or_system_programs():
    from laqta.ui.settings import addable_games

    installed = [{"exe": "Hades2.exe", "name": "Hades II"}, {"exe": "cs2.exe", "name": "Counter-Strike 2"}]
    running = ["Hades2.exe", "MyIndieGame.exe", "explorer.exe", "CS2.EXE", "notes.txt", "MyIndieGame.exe"]
    offered = addable_games({"cs2.exe"}, installed, running)
    assert offered == [{"exe": "Hades2.exe", "name": "Hades II", "where": "installed"},
                       {"exe": "MyIndieGame.exe", "name": "MyIndieGame", "where": "running"}]


def test_deadlock_is_a_built_in_game():
    assert "deadlock.exe" in builtin_exes()
