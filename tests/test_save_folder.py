import os

import pytest

from laqta.storage import clips, config, library
from laqta.ui.settings import usable_folder


@pytest.fixture(autouse=True)
def restore_root():
    yield
    clips.set_root(None)


def test_recordings_follow_the_folder_from_settings(tmp_path):
    assert clips.set_root("") == clips.DEFAULT_ROOT == clips.ROOT
    clips.set_root(str(tmp_path / "Rec"))
    folder = clips.game_dir({"exe": "cs2.exe", "name": "Counter-Strike 2"})
    assert folder.parent == tmp_path / "Rec" / "Counter-Strike 2"

    folder.mkdir(parents=True)
    (folder / "a.mkv").write_bytes(b"x")
    assert [c.path.name for c in library.scan()] == ["a.mkv"]  # the library looks in the same place
    assert library.cleanup(0, 0) == 0


def test_save_folder_must_be_a_full_path_or_empty(tmp_path):
    path = tmp_path / "c.toml"
    config.save({**config.DEFAULTS, "save_folder": str(tmp_path)}, path)
    assert config.load(path)["save_folder"] == str(tmp_path)
    config.save({**config.DEFAULTS, "save_folder": ""}, path)
    for bad in ("Recordings", "..\\elsewhere", 7):
        with pytest.raises(ValueError):
            config.save({**config.DEFAULTS, "save_folder": bad}, path)


def test_usable_folder_is_decided_by_actually_writing(tmp_path):
    assert usable_folder(tmp_path / "new" / "deeper") and not list((tmp_path / "new" / "deeper").iterdir())
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    assert not usable_folder(blocker / "under-a-file")
    assert not usable_folder(__import__("pathlib").Path(os.environ["SystemRoot"]) / "System32" / "config" / "laqta-test")
