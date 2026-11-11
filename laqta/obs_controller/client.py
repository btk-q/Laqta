"""Thin obs-websocket v5 wrapper.

obsws-python has no locks and no reconnect, so: only ONE thread may use `call()`,
every call has a timeout, and any transport failure becomes `ObsDown`.
Events arrive on obsws-python's own thread; `on_event` must only hand them off.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import obsws_python as obsws
from obsws_python.error import OBSSDKError, OBSSDKRequestError

# obsws-python logs the websocket password at INFO and a traceback per failed request.
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

METERS = "InputVolumeMeters"  # 20 a second per input: only subscribed to on request
NOT_READY = 207  # OBS is busy switching profile / scene collection


class ObsDown(Exception):
    """OBS is not reachable (not running, socket died, timed out, bad password)."""


class ObsError(Exception):
    """OBS answered but refused the request."""

    def __init__(self, request: str, code: int, comment: str | None):
        super().__init__(f"{request} failed ({code}): {comment}")
        self.request, self.code = request, code


class _Sink:
    """Stands in for obsws-python's Callback so we get raw (eventType, eventData)."""

    def __init__(self, fn: Callable[[str, dict], None]):
        self.trigger = fn


class Obs:
    def __init__(
        self,
        port: int,
        password: str,
        on_event: Callable[[str, dict], None] = lambda name, data: None,
        timeout: float = 5.0,
        host: str = "127.0.0.1",
        on_meters: Callable[[dict], None] | None = None,  # called on OBS's event thread; must be tiny
    ):
        self._conn = {"host": host, "port": port, "password": password, "timeout": timeout}
        self._on_event, self._on_meters = on_event, on_meters
        self.meters = False  # ask OBS for volume meters at the next connect
        self._req = None
        self._ev = None

    def connect(self) -> None:
        self.close()
        try:
            self._req = obsws.ReqClient(**self._conn)
            subs = int(obsws.Subs.LOW_VOLUME) | (int(obsws.Subs.INPUTVOLUMEMETERS) if self.meters and self._on_meters else 0)
            self._ev = obsws.EventClient(**self._conn, subs=subs)
            self._ev.callback = _Sink(self._dispatch)
        except OBSSDKError as e:
            self.close()
            raise ObsDown(f"OBS rejected the connection (wrong password?): {e}") from e
        except Exception as e:
            self.close()
            raise ObsDown(f"cannot connect to OBS: {type(e).__name__}: {e}") from e

    def _dispatch(self, name: str, data: dict) -> None:
        if name != METERS:
            return self._on_event(name, data)
        try:  # meters never reach the core's inbox: that would wake it 20 times a second
            self._on_meters(data)
        except Exception:
            log.exception("volume meter handler failed")  # an escaping error would end the event thread

    @property
    def alive(self) -> bool:
        # The event thread exits silently when the socket closes; that is our liveness signal.
        return self._req is not None and self._ev is not None and self._ev.worker.is_alive()

    def call(self, request: str, **data) -> dict:
        if not self.alive:
            self.close()
            raise ObsDown("not connected")
        for attempt in range(40):
            try:
                return self._req.send(request, data or None, raw=True) or {}
            except OBSSDKRequestError as e:
                if e.code == NOT_READY and attempt < 39:
                    time.sleep(0.25)
                    continue
                raise ObsError(request, e.code, str(e)) from e
            except Exception as e:  # raw websocket / OS errors on a dead socket
                self.close()
                raise ObsDown(f"{request}: {type(e).__name__}: {e}") from e
        raise AssertionError("unreachable")

    def close(self) -> None:
        # Close sockets directly: EventClient.disconnect() joins its own thread and
        # deadlocks if called from a callback.
        for client in (self._req, self._ev):
            try:
                if client is not None:
                    client.base_client.ws.close()
            except Exception:
                pass
        self._req = self._ev = None
