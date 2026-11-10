import json

from laqta.game_detector import library_scan


def steam_library(tmp_path):
    apps = tmp_path / "steamapps"
    game = apps / "common" / "Hades II"
    (game / "Ship").mkdir(parents=True)
    (game / "Ship" / "Hades2.exe").write_bytes(b"x" * 5000)
    (game / "Ship" / "CrashReporter.exe").write_bytes(b"x" * 9000)  # bigger, but a helper
    (game / "UnityCrashHandler64.exe").write_bytes(b"x" * 8000)
    (game / "Ship" / "F10.exe").write_bytes(b"x" * 7000)  # bigger than the game, not named like it
    (apps / "appmanifest_1.acf").write_text('"AppState"\n{\n "name"  "Hades II™"\n "installdir"  "Hades II"\n}', encoding="utf-8")
    (apps / "appmanifest_2.acf").write_text('"AppState"\n{\n "name"  "Steamworks Common Redistributables"\n "installdir"  "Redist"\n}')
    (apps / "appmanifest_3.acf").write_text('"AppState"\n{\n "name"  "Not Installed Yet"\n "installdir"  "Missing"\n}')
    return tmp_path


def test_steam_picks_the_largest_non_helper_exe(tmp_path):
    assert library_scan.scan_steam([steam_library(tmp_path)]) == [{"exe": "Hades2.exe", "name": "Hades II"}]


def test_epic_reads_the_launchers_manifests(tmp_path):
    (tmp_path / "a.item").write_text(json.dumps({"DisplayName": "Fortnite", "LaunchExecutable": "FortniteGame/Binaries/Win64/FortniteLauncher.exe"}))
    (tmp_path / "b.item").write_text("not json")
    (tmp_path / "c.item").write_text(json.dumps({"DisplayName": "No exe"}))
    assert library_scan.scan_epic(tmp_path) == [{"exe": "FortniteLauncher.exe", "name": "Fortnite"}]


def test_scan_skips_known_games_and_survives_a_broken_scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(library_scan, "scan_steam", lambda: [{"exe": "Hades2.exe", "name": "Hades II"}, {"exe": "cs2.exe", "name": "Counter-Strike 2"}])
    monkeypatch.setattr(library_scan, "scan_epic", lambda: 1 / 0)
    monkeypatch.setattr(library_scan, "scan_gog", lambda: [{"exe": "HADES2.EXE", "name": "duplicate"}, {"exe": "Witcher3.exe", "name": "The Witcher 3"}])
    assert library_scan.scan({"cs2.exe"}) == [{"exe": "Hades2.exe", "name": "Hades II"}, {"exe": "Witcher3.exe", "name": "The Witcher 3"}]
