import json
import zipfile
from pathlib import Path

import pytest

from laqta import i18n
from laqta.core import health
from laqta.core.health import REMOVED
from laqta.core.recorder import GB
from laqta.storage import config

GOOD = {
    "obs": True, "websocket": "on", "ffmpeg": True, "folder": "D:\\Rec", "folder_ok": True, "free": 200 * GB,
    "min_free": 5 * GB, "encoder": "nvenc", "mic_missing": False, "hotkeys": {"Ctrl+Alt+F9": True, "Ctrl+Alt+F10": True},
    "autostart": "on", "player": True,
}  # fmt: skip


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def verdicts(**changes):
    return {c.key: c.status for c in health.checks({**GOOD, **changes})}


def test_a_healthy_machine_is_all_ok():
    assert set(verdicts().values()) == {"ok"}
    assert list(verdicts()) == ["obs", "websocket", "ffmpeg", "player", "folder", "disk", "encoder", "hotkeys"]


def test_each_problem_is_found_and_graded():
    assert "websocket" not in verdicts(obs=False) and verdicts(obs=False)["obs"] == "bad"  # one problem, not two
    assert verdicts(websocket="off")["websocket"] == "bad"
    assert verdicts(websocket="unknown")["websocket"] == "warn"
    assert verdicts(ffmpeg=False)["ffmpeg"] == "warn"  # recording still works
    assert verdicts(folder_ok=False)["folder"] == "bad"
    assert verdicts(encoder="x264")["encoder"] == "warn"
    assert "encoder" not in verdicts(encoder=None)  # not chosen until the first game
    assert verdicts(mic_missing=True)["mic"] == "warn"
    assert verdicts(hotkeys={"Ctrl+Alt+F9": True, "Ctrl+Alt+F10": False})["hotkeys"] == "warn"
    assert verdicts(autostart="blocked")["autostart"] == "warn"
    assert "autostart" not in verdicts(autostart="off")  # off is a choice, not a problem


def test_disk_space_grades():
    assert verdicts(free=3 * GB)["disk"] == "bad"
    assert verdicts(free=8 * GB)["disk"] == "warn"
    assert verdicts(free=3 * GB, min_free=0)["disk"] == "warn"  # guard off: still worth saying
    assert verdicts(free=11 * GB)["disk"] == "ok"
    assert "disk" not in verdicts(free=None)


def test_every_verdict_has_words_in_both_languages_that_say_what_to_do():
    worst = {**GOOD, "websocket": "off", "ffmpeg": False, "folder_ok": False, "free": GB, "encoder": "x264", "mic_missing": True,
             "hotkeys": {"Ctrl+Alt+F9": False}, "autostart": "blocked"}  # fmt: skip
    for facts in (GOOD, worst, {**GOOD, "obs": False}, {**GOOD, "websocket": "unknown"}, {**GOOD, "free": 8 * GB}):
        for language in i18n.LANGUAGES:
            i18n.set_language(language)
            for check in health.checks(facts):
                assert not check.text.startswith("health."), check
                assert not i18n.t(f"health.{check.key}").startswith("health.")
                assert "{" not in check.text, check  # every placeholder was filled
    i18n.set_language("en")
    texts = {c.key: c.text for c in health.checks(worst)}
    assert "Ctrl+Alt+F9" in texts["hotkeys"] and "1 GB" in texts["disk"] and "5 GB" in texts["disk"] and "D:\\Rec" in texts["folder"]
    assert "x264" in texts["encoder"]


def test_gather_runs_on_this_machine_and_feeds_checks():
    facts = health.gather(dict(config.DEFAULTS))
    assert set(GOOD) == set(facts) and health.checks(facts)


def test_forget_encoder_keeps_everything_else(tmp_path):
    store = tmp_path / "obs.json"
    store.write_text(json.dumps({"encoder": "x264", "other": 1}))
    health.forget_encoder(store)
    assert json.loads(store.read_text()) == {"other": 1}
    health.forget_encoder(tmp_path / "missing.json")  # nothing remembered: nothing to do


# -- diagnostic file ---------------------------------------------------------------------------

HOOK = "https://discord.com/api/webhooks/123456/AbCdEf-secret_part"
CFG = {**config.DEFAULTS, "discord_webhook": HOOK, "games": [{"exe": "cs2.exe", "api_token": "tok-123", "name": "CS2"}], "obs_password": ""}


def test_redact_removes_secret_settings_and_nothing_else():
    clean = health.redact(CFG)
    assert clean["discord_webhook"] == REMOVED and clean["games"][0] == {"exe": "cs2.exe", "api_token": REMOVED, "name": "CS2"}
    assert clean["obs_password"] == "" and clean["hotkey"] == CFG["hotkey"]  # empty stays empty: "no password" is useful to know
    assert CFG["discord_webhook"] == HOOK  # the real settings are untouched
    assert sorted(health.secrets_in(CFG)) == sorted([HOOK, "tok-123"])


def test_scrub_removes_secrets_webhooks_and_the_user_name():
    home = Path("C:/Users/Real Name")
    text = (r"connecting with password hunter2hunter2 to C:\Users\Real Name\Videos" "\n"
            "posting to https://discordapp.com/api/webhooks/99/zzZZ-1 ok\n"
            '{"path": "C:\\\\Users\\\\Real Name\\\\AppData"} c:/users/real name/x')
    out = health.scrub(text, ["hunter2hunter2", ""], home)
    assert "hunter2" not in out and "webhooks" not in out and "real name" not in out.lower()
    assert r"%USERPROFILE%\Videos" in out and "ok" in out


def test_the_zip_holds_the_report_and_no_secret_anywhere(tmp_path):
    folder, home = tmp_path / "Laqta", tmp_path / "home" / "Real Name"
    folder.mkdir()
    (folder / "laqta.log").write_text(f"INFO authenticating pw-OBS-SECRET\nINFO saved {home}\\Videos\\a.mkv\nINFO hook {HOOK}\n", encoding="utf-8")
    (folder / "laqta.log.1").write_text("older line tok-123\n", encoding="utf-8")
    (folder / "capture.json").write_text(json.dumps({"cs2.exe": "game_capture", "dir": str(home)}), encoding="utf-8")
    (folder / "gsi_token.txt").write_text("gsi-TOKEN-777", encoding="ascii")
    (folder / "laqta.log.2").write_text("cs2 said gsi-TOKEN-777\n", encoding="utf-8")
    results = health.checks(GOOD)
    dest = health.write_zip(tmp_path / "out" / "diag.zip", CFG, results, folder=folder,
                            secrets=["pw-OBS-SECRET", "gsi-TOKEN-777"], home=home)
    with zipfile.ZipFile(dest) as z:
        files = {name: z.read(name).decode("utf-8") for name in z.namelist()}
    assert set(files) == {"system.txt", "health.txt", "config.toml", "laqta.log", "laqta.log.1", "laqta.log.2", "capture.json"}
    everything = "\n".join(files.values())
    for secret in ("pw-OBS-SECRET", "gsi-TOKEN-777", "tok-123", "AbCdEf", "Real Name"):
        assert secret not in everything, secret
    assert "gsi_token.txt" not in files  # the token file itself never goes in
    assert "[ok] obs:" in files["health.txt"] and 'hotkey = "Ctrl+Alt+F9"' in files["config.toml"]
    assert "%USERPROFILE%" in files["laqta.log"] and "Laqta " in files["system.txt"]
    assert not list(dest.parent.glob("*.part"))


def test_machine_secrets_reads_the_token_without_creating_one(tmp_path):
    assert not (tmp_path / "gsi_token.txt").exists()
    health.machine_secrets(tmp_path)
    assert not (tmp_path / "gsi_token.txt").exists()
    (tmp_path / "gsi_token.txt").write_text("abc123", encoding="ascii")
    assert "abc123" in health.machine_secrets(tmp_path)


def test_a_missing_video_player_is_reported_and_this_install_has_one():
    assert {c.key: c.status for c in health.checks({**GOOD, "player": False})}["player"] == "warn"
    assert health.player_works()
