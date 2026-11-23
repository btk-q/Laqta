import subprocess
import uuid
import winreg
from pathlib import Path

from laqta.storage import install


def run_line(line: str, folder: Path) -> None:
    """One line of a generated script, run from a .cmd file as the real script is."""
    script = folder / "line.cmd"
    script.write_text("@echo off\r\nchcp 65001 >nul\r\n" + line + "\r\n", encoding="utf-8")
    subprocess.run(["cmd", "/c", str(script)], check=False, capture_output=True)


def test_only_the_copy_in_the_install_folder_counts_as_installed(tmp_path):
    assert install.is_installed(tmp_path / "Programs" / "Laqta", tmp_path / "programs" / "laqta")  # Windows ignores case
    assert not install.is_installed(tmp_path / "Downloads" / "Laqta", tmp_path / "Programs" / "Laqta")
    assert not install.is_installed(None)


def test_the_copy_waits_for_laqta_skips_update_leftovers_and_starts_the_installed_copy(tmp_path):
    source, target = tmp_path / "zip" / "Laqta", tmp_path / "Programs" / "Laqta"
    (source / "_internal").mkdir(parents=True)
    (source / "_internal" / "base_library.zip").write_bytes(b"lib")
    (source / "_internal.old").mkdir()
    (source / "Laqta.exe").write_bytes(b"exe")
    (source / "Laqta.exe.old").write_bytes(b"old")
    script = install.install_script(source, target, 4321)
    assert 'PID eq 4321' in script and script.endswith("\r\n")
    assert f'start "" "{target / "Laqta.exe"}"' in script
    # Run the copy line for real.
    copy = next(line for line in script.splitlines() if line.startswith("robocopy"))
    run_line(copy, tmp_path)
    assert (target / "Laqta.exe").read_bytes() == b"exe" and (target / "_internal" / "base_library.zip").exists()
    assert not (target / "_internal.old").exists() and not (target / "Laqta.exe.old").exists()


def test_uninstall_closes_only_the_installed_copy_and_removes_its_folder(tmp_path):
    target = tmp_path / "Programs" / "Laqta"
    (target / "_internal").mkdir(parents=True)
    script = install.uninstall_script(target, 99)
    assert 'PID eq 99' in script and f"$_.Path -eq '{target / 'Laqta.exe'}'" in script
    close = next(line for line in script.splitlines() if line.startswith("powershell"))
    (tmp_path / "close.cmd").write_text(close + "\r\nexit /b %errorlevel%\r\n", encoding="utf-8")
    assert subprocess.run(["cmd", "/c", str(tmp_path / "close.cmd")], capture_output=True).returncode == 0  # nothing from there runs: no error
    remove = next(line for line in script.splitlines() if line.startswith("rmdir"))
    run_line(remove, tmp_path)
    assert not target.exists()


def test_the_apps_entry_is_written_and_removed(tmp_path):
    key = rf"Software\LaqtaTest\Uninstall-{uuid.uuid4().hex}"
    exe = tmp_path / "Laqta.exe"
    exe.write_bytes(b"x" * 4096)
    try:
        install.register(exe, "1.2.3", key)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            read = lambda name: winreg.QueryValueEx(k, name)[0]  # noqa: E731
            assert read("DisplayName") == "Laqta" and read("DisplayVersion") == "1.2.3"
            assert read("UninstallString") == f'"{exe}" uninstall' and read("NoModify") == 1 and read("EstimatedSize") == 4
    finally:
        install.unregister(key)
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\LaqtaTest")
        except OSError:
            pass
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
        assert False, "the key should be gone"
    except FileNotFoundError:
        pass


def test_real_shortcuts_are_made_and_only_removed_when_they_point_at_this_copy(tmp_path):
    name = f"Laqta-test-{uuid.uuid4().hex[:8]}.lnk"  # never the user's own Laqta.lnk
    exe = tmp_path / "Laqta.exe"
    places = 'foreach ($p in "Programs","Desktop") { Join-Path (New-Object -ComObject WScript.Shell).SpecialFolders.Item($p) "%s" }' % name
    links = [Path(p) for p in subprocess.run(["powershell", "-NoProfile", "-Command", places], capture_output=True, text=True).stdout.splitlines() if p.strip()]
    try:
        install._powershell(install.shortcuts_command(exe, name=name))
        assert len(links) == 2 and all(link.exists() for link in links)
        install._powershell(install.shortcuts_command(tmp_path / "Other.exe", remove=True, name=name))
        assert all(link.exists() for link in links)  # someone else's shortcut stays
        install._powershell(install.shortcuts_command(exe, remove=True, name=name))
        assert not any(link.exists() for link in links)
    finally:
        for link in links:
            link.unlink(missing_ok=True)
