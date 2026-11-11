import hashlib
import io
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

from laqta.storage import config, update
from laqta.storage.update import Release

ZIP_BYTES = b"pretend this is the release zip" * 1000
SHA = hashlib.sha256(ZIP_BYTES).hexdigest()
URL = "https://github.com/btk-q/Laqta/releases/download/v0.9.0/Laqta-0.9.0-win64.zip"


def api(tag="v0.9.0", name="Laqta-0.9.0-win64.zip", url=URL, digest=f"sha256:{SHA}", body="## What changed\n- things", **extra):
    asset = {"name": name, "browser_download_url": url, "size": len(ZIP_BYTES)}
    if digest:
        asset["digest"] = digest
    return {"tag_name": tag, "body": body, "assets": [{"name": "notes.txt", "browser_download_url": URL + ".txt"}, asset], **extra}


def serving(data):
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, dict(request.header_items()), timeout))
        return io.BytesIO(data if isinstance(data, bytes) else json.dumps(data).encode())

    opener.calls = calls
    return opener


def test_versions_compare_as_numbers():
    assert update.newer("0.10.0", "0.9.9") and update.newer("v1.0", "0.9.9") and update.newer("0.2.1", "0.2.0")
    assert not update.newer("0.2.0", "0.2.0") and not update.newer("0.1.9", "0.2.0") and not update.newer("", "0.2.0")


def test_a_newer_release_is_found_with_its_notes_and_only_one_anonymous_request_is_made():
    opener = serving(api())
    release = update.check(opener, current="0.2.0")
    assert release == Release("0.9.0", "## What changed\n- things", URL, len(ZIP_BYTES), SHA)
    (url, headers, timeout), = opener.calls
    assert url == "https://api.github.com/repos/btk-q/Laqta/releases/latest" and timeout
    assert headers["User-agent"] == "Laqta" and "Authorization" not in headers and "Cookie" not in headers


def test_nothing_is_offered_when_up_to_date_offline_or_the_answer_is_odd():
    assert update.check(serving(api()), current="0.9.0") is None
    assert update.check(serving(b"<html>rate limited</html>"), current="0.2.0") is None

    def offline(request, timeout):
        raise urllib.error.URLError("no network")

    assert update.check(offline, current="0.2.0") is None


def test_a_release_is_only_usable_from_our_own_downloads_with_a_hash():
    assert update.from_api(api(digest="", body=f"notes\nSHA256: {SHA.upper()}")).sha256 == SHA  # hash from the notes
    for bad in (api(url="https://evil.example/Laqta-0.9.0-win64.zip"), api(url="https://github.com/someone/else/releases/download/v1/Laqta-0.9.0-win64.zip"),
                api(name="Laqta-setup.exe"), api(digest=""), api(digest="sha256:short"), api(draft=True), api(prerelease=True), {"assets": None}, {}):
        assert update.from_api(bad) is None


def test_download_checks_the_hash_and_leaves_nothing_behind_when_it_is_wrong(tmp_path):
    good = Release("0.9.0", "", URL, len(ZIP_BYTES), SHA)
    seen = []
    path = update.download(good, tmp_path, seen.append, serving(ZIP_BYTES))
    assert path.read_bytes() == ZIP_BYTES and seen[-1] == 1.0
    path.unlink()
    with pytest.raises(OSError, match="does not match"):
        update.download(good, tmp_path, opener=serving(ZIP_BYTES + b"tampered"))
    with pytest.raises(OSError, match="own releases"):
        update.download(Release("0.9.0", "", "https://evil.example/x.zip", 1, SHA), tmp_path, opener=serving(ZIP_BYTES))
    with pytest.raises(OSError, match="cancelled"):
        update.download(good, tmp_path, opener=serving(ZIP_BYTES), cancelled=lambda: True)
    assert not list(tmp_path.iterdir())


def release_zip(path: Path, top="Laqta", exe=True):
    with zipfile.ZipFile(path, "w") as z:
        if exe:
            z.writestr(f"{top}/Laqta.exe", b"MZ new")
        z.writestr(f"{top}/_internal/base_library.zip", b"x")
        z.writestr(f"{top}/_internal/laqta/games.json", b"[]")
    return path


def test_unpack_finds_the_app_and_refuses_a_zip_without_it(tmp_path):
    new = update.unpack(release_zip(tmp_path / "a.zip"), tmp_path / "out")
    assert new == tmp_path / "out" / "Laqta" and (new / "_internal" / "laqta" / "games.json").exists()
    with pytest.raises(OSError, match="does not contain"):
        update.unpack(release_zip(tmp_path / "b.zip", exe=False), tmp_path / "out2")


def test_the_swap_script_waits_then_replaces_only_laqtas_own_files():
    script = update.swap_script(Path(r"C:\Temp\laqta-update-1\new\Laqta"), Path(r"C:\Users\x\Desktop"), 4321, work=Path(r"C:\Temp\laqta-update-1"))
    lines = script.splitlines()
    assert lines.index(next(l for l in lines if "PID eq 4321" in l)) < lines.index(next(l for l in lines if "robocopy" in l))
    removed = [l for l in lines if "rmdir" in l]
    assert removed == [r'if exist "C:\Users\x\Desktop\_internal\base_library.zip" rmdir /s /q "C:\Users\x\Desktop\_internal"',
                       r'rmdir /s /q "C:\Temp\laqta-update-1"']  # never the folder Laqta.exe sits in
    assert "/MIR" not in script and "/PURGE" not in script and "timeout" not in script
    assert r'start "" "C:\Users\x\Desktop\Laqta.exe"' in script
    assert "start" not in update.swap_script(Path("n/Laqta"), Path("a"), 1, restart=False).replace("restart", "")


def test_install_refuses_a_folder_it_cannot_write_to(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(update.tempfile, "mkdtemp", lambda prefix="": str((tmp_path / "work").resolve()) if not (tmp_path / "work").mkdir() else "")
    blocked = tmp_path / "missing" / "folder"
    with pytest.raises(OSError, match="cannot write"):
        update.install(release_zip(tmp_path / "a.zip"), blocked)
    assert started == []
    app = tmp_path / "app"
    app.mkdir()
    update.install(release_zip(tmp_path / "a.zip"), app)
    assert len(started) == 1 and started[0][0][:2] == ["cmd", "/c"] and Path(started[0][0][2]).read_text(encoding="utf-8").startswith("@echo off")
    script = Path(started[0][0][2]).read_text(encoding="utf-8")
    assert f'del /q "{tmp_path / "a.zip"}"' in script  # the 94 MB download is not left behind


def test_running_from_source_has_no_exe_to_replace():
    assert update.app_dir() is None


def test_the_update_check_is_a_setting():
    assert config.DEFAULTS["update_check"] is True
    with pytest.raises(ValueError):
        config._check({**config.DEFAULTS, "update_check": "yes"})
