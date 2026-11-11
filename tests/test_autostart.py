"""Runs against the real registry, in a throwaway key of the current user (never the real Run key)."""

import uuid
import winreg

import pytest

from laqta.storage import autostart


@pytest.fixture
def keys():
    base = rf"Software\LaqtaTests\{uuid.uuid4().hex}"
    yield rf"{base}\Run", rf"{base}\Approved"
    for key in (rf"{base}\Run", rf"{base}\Approved", base, r"Software\LaqtaTests"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


def flag(key, data):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, autostart.VALUE, 0, winreg.REG_BINARY, data)


def test_on_and_off(keys):
    assert autostart.state(*keys) == "off"
    autostart.set_enabled(True, *keys)
    assert autostart.state(*keys) == "on" and autostart._read(keys[0]) == autostart.command()
    autostart.set_enabled(False, *keys)
    autostart.set_enabled(False, *keys)  # twice is fine
    assert autostart.state(*keys) == "off"


def test_switched_off_in_task_manager_shows_as_blocked_and_ticking_the_box_lifts_it(keys):
    autostart.set_enabled(True, *keys)
    flag(keys[1], bytes([3, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]))  # what Task Manager writes for "Disabled"
    assert autostart.state(*keys) == "blocked"
    autostart.set_enabled(True, *keys)
    assert autostart.state(*keys) == "on"
    flag(keys[1], bytes([2] + [0] * 11))  # "Enabled"
    assert autostart.state(*keys) == "on"


def test_command_starts_this_copy_without_a_console():
    assert autostart.command().endswith('pythonw.exe" -m laqta') and autostart.command().startswith('"')


def test_the_real_run_key_is_only_ever_addressed_by_default():
    assert autostart.RUN.endswith(r"CurrentVersion\Run") and "StartupApproved" in autostart.APPROVED
