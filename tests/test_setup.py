import json
import os

import pytest

from laqta import i18n
from laqta.obs_controller import setup as obs_setup
from laqta.storage import config, tools
from laqta.ui import setup as screen

FACTS = {"obs": True, "websocket": "on", "ffmpeg": True}


def test_each_step_is_ok_or_says_what_is_wrong():
    assert screen.states(FACTS) == {"obs": "ok", "websocket": "ok", "ffmpeg": "ok"} and not screen.needed(FACTS)
    assert screen.states({**FACTS, "obs": False}) == {"obs": "missing", "websocket": "wait", "ffmpeg": "ok"}
    assert screen.states({**FACTS, "websocket": "off"})["websocket"] == "off"
    assert screen.states({**FACTS, "websocket": "unknown"})["websocket"] == "never"
    assert screen.needed({**FACTS, "ffmpeg": False})


def test_every_state_has_words_and_every_fixable_one_a_button_in_both_languages():
    try:
        for language in i18n.LANGUAGES:
            i18n.set_language(language)
            for step, found in (("obs", ("ok", "missing")), ("websocket", ("ok", "wait", "never", "off")), ("ffmpeg", ("ok", "missing"))):
                for state in found:
                    assert not i18n.t(f"setup.{step}.{state}").startswith("setup."), (step, state)
                    if state not in ("ok", "wait"):
                        assert not i18n.t(f"setup.{step}.{state}.button").startswith("setup."), (step, state)
    finally:
        i18n.set_language("en")


REAL = {"alerts_enabled": False, "auth_required": True, "first_load": False, "server_enabled": False,
        "server_password": "secret-pw", "server_port": 4460}  # the keys OBS 33 writes


def test_turning_the_obs_connection_on_changes_one_value_and_nothing_else(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(REAL))
    assert obs_setup.enable_ws(path, running=lambda: False) is True
    assert json.loads(path.read_text()) == {**REAL, "server_enabled": True}
    assert obs_setup.ws_settings(path) == (4460, "secret-pw", True)
    assert not list(tmp_path.glob("*.laqta-tmp"))


def test_it_is_left_alone_while_obs_runs_and_fails_plainly_if_obs_never_ran(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(REAL))
    assert obs_setup.enable_ws(path, running=lambda: True) is False
    assert json.loads(path.read_text()) == REAL
    with pytest.raises(OSError):
        obs_setup.enable_ws(tmp_path / "missing.json", running=lambda: False)


def test_a_program_installed_a_moment_ago_is_found_without_restarting(tmp_path, monkeypatch):
    """Not on this process's PATH, but in the PATH Windows now has, or where winget puts it."""
    tools._found.clear()
    fresh = tmp_path / "fresh" / "bin"
    fresh.mkdir(parents=True)
    (fresh / "newtool.exe").write_bytes(b"MZ")
    monkeypatch.setattr(tools, "_registry_path", lambda: str(fresh))
    assert tools.which("newtool") and os.path.samefile(tools.which("newtool"), fresh / "newtool.exe")
    monkeypatch.setattr(tools, "_registry_path", lambda: "")
    assert tools.which("newtool")  # remembered
    (fresh / "newtool.exe").unlink()
    assert tools.which("newtool") is None  # and forgotten once it is gone
    links = tmp_path / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_x" / "ffmpeg-9-full_build" / "bin"
    links.mkdir(parents=True)
    (links / "othertool.exe").write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tools.which("othertool").endswith("othertool.exe")
    assert tools.which("no-such-program-xyz") is None
    tools._found.clear()


def test_installing_uses_winget_in_its_own_window_or_reports_there_is_none(monkeypatch):
    started = []
    monkeypatch.setattr(tools, "winget", lambda: "winget.exe")
    tools.install("ffmpeg", popen=lambda argv, **kw: started.append((argv, kw)) or "process")
    (argv, kw), = started
    assert argv[:4] == ["winget", "install", "--id", "Gyan.FFmpeg"] and "-e" in argv and kw["creationflags"]
    assert tools.install_command("obs")[3] == "OBSProject.OBSStudio"
    monkeypatch.setattr(tools, "winget", lambda: None)
    assert tools.install("obs", popen=lambda *a, **k: 1 / 0) is None
    assert all(page.startswith("https://") for page in tools.PAGES.values())


def test_the_setup_screen_is_remembered_as_done():
    assert config.DEFAULTS["setup_done"] is False
    with pytest.raises(ValueError):
        config._check({**config.DEFAULTS, "setup_done": "yes"})
