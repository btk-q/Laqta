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
    monkeypatch.setattr(library_scan, "scan_riot", lambda: [{"exe": "LoR.exe", "name": "LoR"}])
    monkeypatch.setattr(library_scan, "scan_publishers", lambda: [])
    assert library_scan.scan({"cs2.exe"}) == [{"exe": "Hades2.exe", "name": "Hades II"}, {"exe": "LoR.exe", "name": "LoR"},
                                              {"exe": "Witcher3.exe", "name": "The Witcher 3"}]


def test_riot_games_are_found_from_the_riot_clients_own_list(tmp_path):
    """Folder layout copied from a real install: one game at its folder, two with Live/Game beneath."""
    lol, xko, lor = tmp_path / "Riot Games" / "League of Legends", tmp_path / "Riot Games" / "2XKO" / "Live", tmp_path / "Riot Games" / "LoR" / "live" / "Game"
    for folder, exe, size in ((lol / "Game", "League of Legends.exe", 3000), (lol, "LeagueClient.exe", 200),
                              (xko / "Lion" / "Binaries" / "Win64", "Lion-Win64-Shipping.exe", 3000), (lor, "LoR.exe", 900)):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / exe).write_bytes(b"x" * size)
    installs = tmp_path / "RiotClientInstalls.json"
    installs.write_text(__import__("json").dumps({"associated_client": {f"{lol.as_posix()}/": "client", f"{xko.as_posix()}/": "client",
                                                                         f"{lor.as_posix()}/": "client", "Z:/gone/": "client"}}))
    found = {g["name"]: g["exe"] for g in library_scan.scan_riot(installs)}
    assert found == {"League of Legends": "League of Legends.exe", "2XKO": "Lion-Win64-Shipping.exe", "LoR": "LoR.exe"}
    assert library_scan.scan_riot(tmp_path / "missing.json") == []


def test_program_files_are_found_by_name_with_the_copy_nearest_the_top_winning(tmp_path):
    deep = tmp_path / "Game" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / "Game" / "a" / "Tool.exe").write_bytes(b"1")
    (deep / "tool.exe").write_bytes(b"2")
    (deep / "TooDeep.exe").write_bytes(b"3")
    found = library_scan._exes_under(tmp_path / "Game", 3)
    assert found == {"tool.exe": tmp_path / "Game" / "a" / "Tool.exe"}  # four folders down is out of reach at depth 3
    assert library_scan._exes_under(tmp_path / "missing", 3) == {}
    assert isinstance(library_scan.program_files(), dict)  # runs on this PC without error



def test_battle_net_and_ea_games_come_from_the_installed_programs_list(tmp_path):
    def game(folder, exe):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / exe).write_bytes(b"x" * 1000)
        return str(tmp_path / folder)

    entries = [
        {"DisplayName": "Overwatch", "Publisher": "Blizzard Entertainment", "InstallLocation": game("Overwatch", "Overwatch.exe")},
        {"DisplayName": "Apex Legends™", "Publisher": "Electronic Arts", "InstallLocation": '"' + game("Apex", "r5apex.exe") + '"'},
        {"DisplayName": "Battle.net", "Publisher": "Blizzard Entertainment", "InstallLocation": game("Battle.net", "Battle.net.exe")},
        {"DisplayName": "Notepad++", "Publisher": "Notepad++ Team", "InstallLocation": game("npp", "notepad++.exe")},
        {"DisplayName": "Diablo IV", "Publisher": "Blizzard Entertainment", "InstallLocation": str(tmp_path / "gone")},
        {"DisplayName": "", "Publisher": "Electronic Arts", "InstallLocation": ""},
    ]
    assert library_scan.scan_publishers(entries) == [{"exe": "Overwatch.exe", "name": "Overwatch"}, {"exe": "r5apex.exe", "name": "Apex Legends"}]
    assert isinstance(library_scan.installed_programs(), list)  # reads this PC's real list without error


def test_a_game_with_a_launchers_word_in_its_name_is_not_taken_for_the_launcher(tmp_path):
    entries = []
    for number, name in enumerate(("Dragon Age: Origins", "Origin", "EA app", "EA Desktop", "Battle.net")):
        folder = tmp_path / str(number)
        folder.mkdir()
        (folder / "DAOrigins.exe").write_bytes(b"x")
        entries.append({"DisplayName": name, "Publisher": "Electronic Arts", "InstallLocation": str(folder)})
    assert [g["name"] for g in library_scan.scan_publishers(entries)] == ["Dragon Age: Origins"]
