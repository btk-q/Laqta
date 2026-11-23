"""Install Laqta on this PC from the unpacked zip, and remove it again.

No installer program and no administrator rights: Laqta copies its own folder to
%LOCALAPPDATA%\\Programs\\Laqta (where per-user apps go), adds Start menu and Desktop shortcuts,
and registers itself in the user's part of Windows' list of installed programs, so Settings >
Apps can uninstall it. Updates keep working: they replace the files wherever Laqta.exe is.

Recordings and settings live elsewhere (Videos\\Laqta, %APPDATA%\\Laqta) and are never touched.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import winreg
from pathlib import Path

TARGET = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "Laqta"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Laqta"
NAME = "Laqta"
LEFTOVERS = ("_internal.old", "Laqta.exe.old")  # an interrupted update's spare copy is not installed


def is_installed(app: Path | None, target: Path = TARGET) -> bool:
    """Is this copy (the folder of Laqta.exe) the installed one?"""
    return app is not None and os.path.normcase(os.path.abspath(app)) == os.path.normcase(os.path.abspath(target))


def _quote_ps(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def shortcuts_command(exe: Path, remove: bool = False, name: str = "Laqta.lnk") -> str:
    """PowerShell that makes, or removes, the Start menu and Desktop shortcuts to `exe`. Windows
    says where those folders are, so a Desktop moved to OneDrive is found too. Removing leaves a
    shortcut alone unless it points at `exe` (one to an unpacked copy stays)."""
    places = f"foreach ($place in 'Programs', 'Desktop') {{ $lnk = Join-Path $shell.SpecialFolders.Item($place) {_quote_ps(name)}; "
    if remove:
        body = places + f"if ((Test-Path -LiteralPath $lnk) -and $shell.CreateShortcut($lnk).TargetPath -eq {_quote_ps(exe)}) {{ Remove-Item -LiteralPath $lnk -Force }} }}"
    else:
        body = (places + "$s = $shell.CreateShortcut($lnk); "
                f"$s.TargetPath = {_quote_ps(exe)}; $s.WorkingDirectory = {_quote_ps(exe.parent)}; "
                f"$s.IconLocation = {_quote_ps(str(exe) + ',0')}; $s.Description = 'Laqta game recorder'; $s.Save() }}")
    return "$shell = New-Object -ComObject WScript.Shell; " + body


def _powershell(command: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], check=True, timeout=60,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def register(exe: Path, version: str, key: str = KEY) -> None:
    """The entry in Settings > Apps. Written again at every start, so the version stays right
    after updates."""
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) // 1024 if exe.parent.is_dir() else 0
    values = {"DisplayName": NAME, "DisplayVersion": version, "Publisher": NAME, "DisplayIcon": f'"{exe}",0',
              "InstallLocation": str(exe.parent), "UninstallString": f'"{exe}" uninstall', "URLInfoAbout": "https://github.com/btk-q/Laqta"}
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        for name, value in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", min(size, 0xFFFFFFFF))):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


def unregister(key: str = KEY) -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except OSError:
        pass  # was not there


def _wait_for(pid: int) -> list[str]:
    # ping as a one-second pause: `timeout` refuses to run without a console
    return [":wait", f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)']


def install_script(source: Path, target: Path, pid: int) -> str:
    """Waits for this Laqta to close, copies it to `target` and starts the installed copy."""
    lines = ["@echo off", "chcp 65001 >nul", *_wait_for(pid),
             f'robocopy "{source}" "{target}" /E /R:5 /W:1 /XD {LEFTOVERS[0]} /XF {LEFTOVERS[1]} /NFL /NDL /NJH /NJS >nul',
             f'start "" "{target / "Laqta.exe"}"', '(goto) 2>nul & del "%~f0"']
    return "\r\n".join(lines) + "\r\n"


def uninstall_script(target: Path, pid: int) -> str:
    """Waits for this Laqta to close, closes any other running copy from `target`, and deletes the folder."""
    exe = target / "Laqta.exe"
    lines = ["@echo off", "chcp 65001 >nul", *_wait_for(pid),
             # Only a Laqta started from this folder is closed: a copy elsewhere (an unpacked zip) is left alone.
             f'powershell -NoProfile -NonInteractive -Command "Get-Process Laqta -ErrorAction SilentlyContinue | '
             f"Where-Object {{ $_.Path -eq {_quote_ps(exe)} }} | Stop-Process -Force\"",
             "ping -n 3 127.0.0.1 >nul", f'rmdir /s /q "{target}"', '(goto) 2>nul & del "%~f0"']
    return "\r\n".join(lines) + "\r\n"


def _run_script(text: str) -> None:
    script = Path(tempfile.mkdtemp(prefix="laqta-install-")) / "run.cmd"
    script.write_text(text, encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True, cwd=str(script.parent))


def install(app: Path, version: str, target: Path = TARGET) -> None:
    """Shortcuts and the Apps entry now; the files are copied once Laqta has quit. The caller
    must quit Laqta right after this; the installed copy then starts by itself."""
    exe = target / "Laqta.exe"
    _powershell(shortcuts_command(exe))
    register(exe, version)
    _run_script(install_script(app, target, os.getpid()))


def uninstall(target: Path = TARGET) -> None:
    """Shortcuts, the Apps entry and start-with-Windows go now; the folder once Laqta has quit."""
    from . import autostart

    _powershell(shortcuts_command(target / "Laqta.exe", remove=True))
    unregister()
    if autostart.state() != "off":
        autostart.set_enabled(False)
    _run_script(uninstall_script(target, os.getpid()))
