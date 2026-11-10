"""Find installed games by reading the launchers' own files and registry keys.

Covered: Steam, Epic Games, GOG. Battle.net and EA keep their install lists in formats that
need per-launcher parsers; games from those are picked up by the "looks like a game" prompt
or added by hand.
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


def scan(known: set[str]) -> list[dict]:
    """Installed games not already in `known` (lower-case exe names), one entry per exe."""
    found: dict[str, dict] = {}
    for scanner in (scan_steam, scan_epic, scan_gog):
        try:
            for game in scanner():
                key = game["exe"].lower()
                if key not in known and key not in found:
                    found[key] = game
        except Exception:
            log.exception("%s failed", scanner.__name__)
    return sorted(found.values(), key=lambda g: g["name"].lower())
