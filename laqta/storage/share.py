"""Sending a clip off this PC. The only code in Laqta that does, and only when the user
presses the button for one specific clip. No Qt.

Discord webhooks: a channel's owner creates an address; a POST of a file to it appears in
that channel. Nothing is read back, there is no account or login, and no other host is
ever contacted: addresses that are not Discord webhooks are refused.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from . import library
from .config import WEBHOOK
from .export import DISCORD_LIMIT


def valid_webhook(url: str) -> bool:
    return WEBHOOK.fullmatch(url.strip()) is not None


def for_sharing(path: Path, root: Path | None = None) -> Path:
    """The file to send: chat apps play MP4 in place but show MKV as a download."""
    return path if path.suffix.lower() == ".mp4" else library.export(path, root)


def request(url: str, path: Path, text: str = "") -> urllib.request.Request:
    """The upload as one multipart request (standard library only)."""
    if not valid_webhook(url):
        raise ValueError("That is not a Discord webhook address.")
    size = path.stat().st_size
    if size > DISCORD_LIMIT:
        raise ValueError(f"too big for Discord ({size / 1024 / 1024:.0f} MB; the limit is 10 MB)")
    boundary = uuid.uuid4().hex
    # allowed_mentions: a clip's text must never be able to ping @everyone.
    message = json.dumps({"content": text[:1900], "allowed_mentions": {"parse": []}})
    name = re.sub(r'["\r\n]', "_", path.name)
    body = b"".join((
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n{message}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{name}"\r\nContent-Type: video/mp4\r\n\r\n'.encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ))  # fmt: skip
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "Laqta"}
    return urllib.request.Request(url.strip(), data=body, headers=headers, method="POST")


def post(url: str, path: Path, text: str = "", opener: Callable = urllib.request.urlopen) -> None:
    """Upload `path` to the webhook. Raises ValueError (refused before sending) or OSError (sending failed)."""
    try:
        with opener(request(url, path, text), timeout=60):
            pass
    except urllib.error.HTTPError as e:
        reasons = {401: "Discord no longer accepts this webhook address", 404: "this webhook no longer exists in Discord",
                   413: "Discord says the file is too big", 429: "Discord says to slow down; try again in a minute"}
        raise OSError(reasons.get(e.code, f"Discord answered with error {e.code}")) from None  # `from None`: the chain holds the address
    except urllib.error.URLError as e:
        raise OSError(f"could not reach Discord ({e.reason})") from None
