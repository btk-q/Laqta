"""Find installed games by reading the launchers' own files and registry keys.

Covered: Steam, Epic Games, GOG and Riot from their own lists; Battle.net and EA games from
Windows' list of installed programs, where both register each game with its publisher and folder.
Anything else is picked up by the "looks like a game" prompt or added by hand.
"""

from __future__ import annotations

import json
import logging
import os
import re
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

# Executables that ship inside game folders but are not the game.
HELPERS = re.compile(
    r"crash|report|redist|setup|install|unins|launcher|helper|updater|anticheat|easyanticheat|battleye|"
    r"vc_|dxsetup|dotnet|cefprocess|webhelper|overlay|benchmark|config|server|editor|ffmpeg|7z|touchup|"
    r"bootstrap|console|diagnos|eos",
    re.IGNORECASE,
)
NOT_GAMES = {"steamworks common redistributables", "wallpaper engine", "steam linux runtime", "proton"}


def _steam_libraries() -> list[Path]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return []
    libraries = [steam]
    try:
        vdf = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libraries += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    return list(dict.fromkeys(libraries))


def main_exe(folder: Path, name: str = "") -> str | None:
    """Best guess at a game's executable, since Steam does not record it anywhere we can
    read: among .exe files near the top of the folder that are not obvious helpers, prefer
    one named like the game, then the largest."""
    words = re.findall(r"[a-z0-9]+", f"{name} {folder.name}".lower())
    hints = {w for w in words if len(w) >= 4} | {"".join(w[0] for w in words[:n]) for n in (2, 3) if len(words) >= n}
    best: tuple[bool, int, str] | None = None
    try:
        candidates = [p for depth in ("*.exe", "*/*.exe", "*/*/*.exe", "*/*/*/*.exe") for p in folder.glob(depth)]
    except OSError:
        return None
    for path in candidates:
        if HELPERS.search(path.name):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        stem = re.sub(r"[^a-z0-9]", "", path.stem.lower())
        rank = (any(h in stem for h in hints if len(h) >= 4) or stem in hints, size, path.name)
        if best is None or rank[:2] > best[:2]:
            best = rank
    return best[2] if best else None


def scan_steam(libraries: list[Path] | None = None) -> list[dict]:
    games = []
    for library in _steam_libraries() if libraries is None else libraries:
        for manifest in sorted((library / "steamapps").glob("appmanifest_*.acf")):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = re.search(r'"name"\s+"([^"]+)"', text)
            folder = re.search(r'"installdir"\s+"([^"]+)"', text)
            if not name or not folder or any(n in name.group(1).lower() for n in NOT_GAMES):
                continue
            exe = main_exe(library / "steamapps" / "common" / folder.group(1), name.group(1))
            if exe:
                games.append({"exe": exe, "name": name.group(1).replace("™", "").replace("®", "").strip()})
    return games


def scan_epic(manifests: Path | None = None) -> list[dict]:
    manifests = manifests or Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"
    games = []
    for item in sorted(manifests.glob("*.item")):
        try:
            data = json.loads(item.read_text(encoding="utf-8", errors="replace"))
            exe = Path(data["LaunchExecutable"]).name
            if exe.lower().endswith(".exe"):
                games.append({"exe": exe, "name": data["DisplayName"]})
        except (OSError, ValueError, KeyError):
            continue
    return games


def scan_gog() -> list[dict]:
    games = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as game:
                        exe = Path(winreg.QueryValueEx(game, "exe")[0]).name
                        games.append({"exe": exe, "name": winreg.QueryValueEx(game, "gameName")[0]})
                except OSError:
                    continue
    except OSError:
        pass
    return games


RIOT = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Riot Games" / "RiotClientInstalls.json"


def riot_folders(installs: Path | None = None) -> list[Path]:
    """Where Riot's games are installed, from the Riot Client's own list."""
    try:
        data = json.loads((installs or RIOT).read_text(encoding="utf-8", errors="replace"))
        return [Path(folder) for folder in data.get("associated_client", {}) if Path(folder).is_dir()]
    except (OSError, ValueError, AttributeError):
        return []


def scan_riot(installs: Path | None = None) -> list[dict]:
    games = []
    for folder in riot_folders(installs):
        # ".../League of Legends/" is the game; ".../2XKO/Live/" and ".../LoR/live/Game/" name it higher up.
        named = next((p for p in (folder, *folder.parents) if p.name.lower() not in ("live", "game")), folder)
        exe = main_exe(folder, named.name)
        if exe:
            games.append({"exe": exe, "name": named.name})
    return games


UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
# Publishers whose games come from Battle.net and the EA app. Their launchers register under the
# same publisher and are left out by name.
PUBLISHERS = re.compile(r"blizzard|activision|electronic arts", re.IGNORECASE)
LAUNCHERS = re.compile(r"battle\.net|ea app|origin|ea desktop", re.IGNORECASE)


def installed_programs() -> list[dict]:
    """Windows' list of installed programs (what Settings > Apps shows): name, publisher, folder.
    Reads the registry only."""
    entries = []
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            with winreg.OpenKey(hive, UNINSTALL, 0, winreg.KEY_READ | view) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as key:
                            entry = {}
                            for field in ("DisplayName", "Publisher", "InstallLocation"):
                                try:
                                    entry[field] = str(winreg.QueryValueEx(key, field)[0])
                                except OSError:
                                    entry[field] = ""
                            entries.append(entry)
                    except OSError:
                        continue
        except OSError:
            continue
    return entries


def _publisher_folders(entries: list[dict] | None = None) -> list[tuple[str, Path]]:
    """(name, folder) of each installed Battle.net or EA game."""
    out = []
    for e in installed_programs() if entries is None else entries:
        name, folder = e.get("DisplayName", "").strip(), e.get("InstallLocation", "").strip().strip('"')
        if name and folder and PUBLISHERS.search(e.get("Publisher", "")) and not LAUNCHERS.search(name) and Path(folder).is_dir():
            out.append((name, Path(folder)))
    return out


def scan_publishers(entries: list[dict] | None = None) -> list[dict]:
    games = []
    for name, folder in _publisher_folders(entries):
        if exe := main_exe(folder, name):
            games.append({"exe": exe, "name": name.replace("™", "").replace("®", "").strip()})
    return games


def _exes_under(folder: Path, depth: int) -> dict[str, Path]:
    found: dict[str, Path] = {}
    try:
        for level in range(1, depth + 1):
            for path in folder.glob("/".join(["*"] * (level - 1) + ["*.exe"])):
                found.setdefault(path.name.lower(), path)  # the copy nearest the top wins
    except OSError:
        pass
    return found


def program_files() -> dict[str, Path]:
    """Every program file in the installed games' folders: {name in lower case: full path}.
    Used to find a game's own icon. Reads folders only; no process is touched. Takes a second
    or two on a large library, so call it off the GUI thread."""
    found: dict[str, Path] = {}
    for library in _steam_libraries():
        try:
            folders = [p for p in (library / "steamapps" / "common").iterdir() if p.is_dir()]
        except OSError:
            continue
        for folder in folders:
            for name, path in _exes_under(folder, 4).items():
                found.setdefault(name, path)
    for folder in riot_folders() + [folder for _, folder in _publisher_folders()]:
        for name, path in _exes_under(folder, 5).items():  # Valorant's is five folders down
            found.setdefault(name, path)
    manifests = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"
    for item in manifests.glob("*.item"):
        try:
            data = json.loads(item.read_text(encoding="utf-8", errors="replace"))
            path = Path(data["InstallLocation"]) / data["LaunchExecutable"]
            if path.is_file():
                found.setdefault(path.name.lower(), path)
        except (OSError, ValueError, KeyError):
            continue
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as game:
                        path = Path(winreg.QueryValueEx(game, "exe")[0])
                        if path.is_file():
                            found.setdefault(path.name.lower(), path)
                except OSError:
                    continue
    except OSError:
        pass
    return found


def scan(known: set[str]) -> list[dict]:
    """Installed games not already in `known` (lower-case exe names), one entry per exe."""
    found: dict[str, dict] = {}
    for scanner in (scan_steam, scan_epic, scan_gog, scan_riot, scan_publishers):
        try:
            for game in scanner():
                key = game["exe"].lower()
                if key not in known and key not in found:
                    found[key] = game
        except Exception:
            log.exception("%s failed", scanner.__name__)
    return sorted(found.values(), key=lambda g: g["name"].lower())
