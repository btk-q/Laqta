import datetime
from pathlib import Path

import pytest

from laqta import i18n
from laqta.storage import config
from laqta.storage.library import Clip
from laqta.ui import theme
from laqta.ui.library import caption, clock, size_text


contrast = theme.contrast


@pytest.mark.parametrize("accent", [*theme.PRESETS.values(), "#FFFFFF", "#000000", "#808080", "#FF0000", "#FFFF00"])
def test_every_accent_gives_a_readable_palette(accent):
    c = theme.palette(accent)
    assert contrast(c["text"], c["ink"]) >= 7 and contrast(c["text"], c["panel"]) >= 7  # body text
    assert contrast(c["muted"], c["ink"]) >= 4.5  # hints
    assert contrast(c["on_accent"], c["accent_fill"]) >= 4.5  # primary button label
    assert c["rec"] == theme.REC  # recording red never changes
    assert accent.upper() == c["accent"]  # focus rings and checkboxes keep the exact colour chosen


def test_contrast_helper():
    assert round(contrast("#000000", "#FFFFFF"), 1) == 21.0 and contrast("#123456", "#123456") == 1.0


def test_default_palette_matches_the_logo_and_bad_values_fall_back():
    assert theme.palette()["accent"] == "#8B5CF6"
    assert theme.palette("purple") == theme.palette() == theme.palette(None)
    amber = theme.palette("#f5a524")
    assert (amber["on_accent"], amber["accent_fill"]) == ("#14101F", "#F5A524")  # light accent: dark label, fill untouched


def test_accent_is_validated_in_settings(tmp_path):
    path = tmp_path / "c.toml"
    config.save({**config.DEFAULTS, "accent": "#2DD4BF", "cleanup_days": 30, "cleanup_gb": 50}, path)
    assert config.load(path)["accent"] == "#2DD4BF"
    for bad in ("teal", "#12345", 5):
        with pytest.raises(ValueError):
            config.save({**config.DEFAULTS, "accent": bad}, path)
    with pytest.raises(ValueError):
        config.save({**config.DEFAULTS, "cleanup_gb": -1}, path)


def test_library_captions():
    i18n.set_language("en")
    today = datetime.date(2026, 11, 10)
    clip = Clip(Path("a.mkv"), "Counter-Strike 2", datetime.datetime(2026, 11, 10, 15, 28), 3 * 1024**2, "clip",
                ({"type": "match_start", "offset": 0}, {"type": "kill", "offset": 15}, {"type": "kill", "offset": 16}, {"type": "ace", "offset": 18}), 23.0)
    assert caption(clip, today) == "Counter-Strike 2\nToday 15:28   0:23   Kill, Ace"
    recording = Clip(Path("b.mkv"), "Hades II", datetime.datetime(2026, 11, 9, 9, 5), 5 * 1024**3, "recording")
    assert caption(recording, today) == "Hades II\nYesterday 09:05   5.0 GB"
    older = Clip(Path("c.mkv"), "Hades II", datetime.datetime(2026, 10, 1, 9, 5), 1, "recording")
    assert caption(older, today) == "Hades II\n2026-10-01 09:05   1 MB"
    assert (clock(7), clock(125), clock(3725)) == ("0:07", "2:05", "1:02:05")
    assert (size_text(900 * 1024**2), size_text(1536 * 1024**2)) == ("900 MB", "1.5 GB")


def test_videos_are_grouped_by_game_and_day_newest_first():
    import datetime
    from pathlib import Path

    from laqta.storage.library import Clip
    from laqta.ui.library import day_text, grouped

    def clip(game, day, hour):
        return Clip(Path(f"{game}-{day}-{hour}.mkv"), game, datetime.datetime(2026, 11, day, hour), 1, "clip")

    groups = grouped([clip("LoL", 10, 9), clip("CS2", 11, 8), clip("LoL", 11, 20), clip("LoL", 11, 7), clip("CS2", 10, 23)])
    assert [(game, day.day, [c.when.hour for c in members]) for game, day, members in groups] == [
        ("LoL", 11, [20, 7]), ("CS2", 11, [8]), ("CS2", 10, [23]), ("LoL", 10, [9])]
    today = datetime.date(2026, 11, 11)
    assert [day_text(datetime.date(2026, 11, d), today) for d in (11, 10, 2)] == ["Today", "Yesterday", "2026-11-02"]


def test_video_presets():
    from laqta.storage import config
    from laqta.ui.settings import VIDEO_PRESETS, preset_of

    assert preset_of({**config.DEFAULTS, **VIDEO_PRESETS["balanced"]}) == "balanced"
    assert preset_of({**config.DEFAULTS, **VIDEO_PRESETS["high"], "encoder": "obs_x264"}) == "high"  # the encoder is not part of a preset
    assert preset_of({**config.DEFAULTS, "bitrate": 12000}) == "custom"
    for preset in VIDEO_PRESETS.values():
        config._check({**config.DEFAULTS, **preset})  # every preset is a valid setting
