import pytest

from laqta.obs_controller import setup


@pytest.fixture(autouse=True)
def no_obs_settle_pauses(monkeypatch):
    """OBS setup pauses after profile switches and while probing encoders; fakes need neither."""
    monkeypatch.setattr(setup.time, "sleep", lambda seconds: None)
