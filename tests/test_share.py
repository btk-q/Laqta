import io
import json
import urllib.error

import pytest

from laqta.core import health
from laqta.storage import config, share
from laqta.storage.export import DISCORD_LIMIT

HOOK = "https://discord.com/api/webhooks/123456789/AbC-dEf_123"


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "Game" / "2026-11-11" / 'Replay "best" 1.mp4'.replace('"', "'")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00\x01video-bytes\xff")
    return path


def test_only_discord_webhook_addresses_are_accepted():
    good = (HOOK, "https://discordapp.com/api/webhooks/1/a", "https://canary.discord.com/api/webhooks/1/a", f"  {HOOK}  ")
    bad = ("", "http://discord.com/api/webhooks/1/a", "https://discord.com.evil.example/api/webhooks/1/a", "https://example.com/api/webhooks/1/a",
           "https://discord.com/api/webhooks/1/a?wait=true", "https://discord.com/api/webhooks/1/a/../../x", HOOK + "\nHost: evil")
    assert all(share.valid_webhook(u) for u in good) and not any(share.valid_webhook(u) for u in bad)


def test_the_request_carries_the_file_and_text_and_cannot_ping_everyone(clip):
    request = share.request(HOOK, clip, "League of Legends @everyone")
    assert request.full_url == HOOK and request.get_method() == "POST" and request.get_header("User-agent") == "Laqta"
    boundary = request.get_header("Content-type").split("boundary=")[1]
    parts = request.data.split(f"--{boundary}".encode())
    assert len(parts) == 4 and parts[-1] == b"--\r\n"
    message = json.loads(parts[1].split(b"\r\n\r\n", 1)[1])
    assert message == {"content": "League of Legends @everyone", "allowed_mentions": {"parse": []}}
    head, body = parts[2].split(b"\r\n\r\n", 1)
    assert b'name="files[0]"' in head and clip.name.encode() in head and body == clip.read_bytes() + b"\r\n"


def test_nothing_is_sent_to_a_wrong_address_or_when_the_file_is_too_big(clip):
    sent = []
    for url in ("https://example.com/hook", ""):
        with pytest.raises(ValueError):
            share.post(url, clip, opener=lambda *a, **k: sent.append(a))
    clip.write_bytes(b"x" * (DISCORD_LIMIT + 1))
    with pytest.raises(ValueError, match="10 MB"):
        share.post(HOOK, clip, opener=lambda *a, **k: sent.append(a))
    assert sent == []


def test_post_sends_once_with_a_timeout(clip):
    sent = []

    def opener(request, timeout):
        sent.append((request.full_url, timeout))
        return io.BytesIO(b"")

    share.post(HOOK, clip, "hi", opener=opener)
    assert sent == [(HOOK, 60)]


def test_failures_are_explained_without_repeating_the_address(clip):
    def refuse(code):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b""))
        return opener

    for code, words in ((404, "no longer exists"), (413, "too big"), (500, "error 500")):
        with pytest.raises(OSError, match=words) as e:
            share.post(HOOK, clip, opener=refuse(code))
        assert "AbC-dEf" not in str(e.value) and e.value.__cause__ is None

    def offline(request, timeout):
        raise urllib.error.URLError("no network")

    with pytest.raises(OSError, match="could not reach Discord"):
        share.post(HOOK, clip, opener=offline)


def test_an_mp4_is_sent_as_it_is_and_anything_else_is_remuxed_first(clip, monkeypatch):
    made = []
    monkeypatch.setattr(share.library, "export", lambda path, root=None: made.append(path) or path.with_suffix(".mp4"))
    assert share.for_sharing(clip) == clip and made == []
    mkv = clip.with_suffix(".mkv")
    assert share.for_sharing(mkv) == clip and made == [mkv]


def test_sharing_is_off_by_default_and_the_address_is_checked_and_kept_out_of_diagnostics():
    assert config.DEFAULTS["discord_webhook"] == "" and config.DEFAULTS["toast_window"] is False
    config._check({**config.DEFAULTS, "discord_webhook": HOOK})
    for bad in ({"discord_webhook": "https://example.com/x"}, {"toast_window": 1}):
        with pytest.raises(ValueError):
            config._check({**config.DEFAULTS, **bad})
    assert health.redact({**config.DEFAULTS, "discord_webhook": HOOK})["discord_webhook"] == health.REMOVED


def test_the_corner_note_never_takes_focus_or_clicks():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from laqta.ui.toast import Toast

    app = QApplication.instance() or QApplication([])
    note = Toast()
    flags = note.windowFlags()
    for flag in (Qt.WindowType.WindowDoesNotAcceptFocus, Qt.WindowType.WindowTransparentForInput, Qt.WindowType.WindowStaysOnTopHint,
                 Qt.WindowType.FramelessWindowHint):
        assert flags & flag, flag
    assert note.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    note.show_text("Clip saved", ms=30)
    assert note.isVisible() and note.text() == "Clip saved"
    area = app.primaryScreen().availableGeometry()
    assert area.contains(note.geometry())
    deadline = __import__("time").time() + 2
    while note.isVisible() and __import__("time").time() < deadline:
        app.processEvents()
    assert not note.isVisible()
