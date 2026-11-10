import json
import shutil
import subprocess

import pytest

from laqta.obs_controller import audio, encoders
from laqta.obs_controller.audio import DESKTOP, GAME, MIC, Audio
from laqta.storage import clips, library

WINDOW = "Counter-Strike 2:SDL_app:cs2.exe"
CS2 = {"exe": "cs2.exe", "name": "Counter-Strike 2", "game_audio_only": True}


class FakeObs:
    def __init__(self, window=WINDOW):
        self.window = window
        self.muted, self.tracks, self.settings, self.volume, self.reopened = {}, {}, {}, {}, []

    def call(self, request, **d):
        match request:
            case "GetInputPropertiesListPropertyItems":
                others = [{"itemValue": "Discord:Chrome_WidgetWin_1:Discord.exe"}]
                return {"propertyItems": others + ([{"itemValue": self.window}] if self.window else [])}
            case "SetInputMute":
                self.muted[d["inputName"]] = d["inputMuted"]
            case "SetInputAudioTracks":
                self.tracks[d["inputName"]] = sorted(int(k) for k, on in d["inputAudioTracks"].items() if on)
            case "GetInputSettings":
                return {"inputSettings": dict(self.settings.get(d["inputName"], {}))}
            case "SetInputSettings":
                self.reopened.append(d["inputName"])  # OBS reopens the device on every settings change
                self.settings[d["inputName"]] = d["inputSettings"]
            case "SetInputVolume":
                self.volume[d["inputName"]] = d["inputVolumeMul"]
        return {}

    def live(self):
        """Which sources are audible, and on which tracks."""
        return {name: self.tracks.get(name) for name, muted in self.muted.items() if not muted}


def test_game_only_audio_puts_the_game_alone_on_tracks_1_and_2():
    obs = FakeObs()
    a = Audio(obs)
    a.start(CS2, mic_enabled=False, mic_device="default", mic_volume=100)
    assert obs.live() == {GAME: [1, 2]}  # desktop (Discord, music) is out; nothing is doubled
    assert obs.settings[GAME] == {"window": WINDOW, "priority": 2}
    assert a.check() is False


def test_default_is_all_desktop_sound_and_no_microphone():
    obs = FakeObs()
    Audio(obs).start({"exe": "cs2.exe"}, mic_enabled=False, mic_device="default", mic_volume=100)
    assert obs.live() == {DESKTOP: [1, 2]} and MIC not in obs.settings


def test_falls_back_to_desktop_until_the_game_window_exists():
    obs = FakeObs(window=None)
    a = Audio(obs)
    a.start(CS2, mic_enabled=False, mic_device="default", mic_volume=100)
    assert obs.live() == {DESKTOP: [1, 2]}  # never silent while waiting
    assert a.check() is True
    obs.window = WINDOW
    assert a.check() is False and obs.live() == {GAME: [1, 2]}


def test_microphone_goes_on_the_mix_and_its_own_track_at_the_chosen_volume():
    obs = FakeObs()
    Audio(obs).start(CS2, mic_enabled=True, mic_device="{0.0.1.00000000}.{abc}", mic_volume=150)
    assert obs.live() == {GAME: [1, 2], MIC: [1, 3]}
    assert obs.settings[MIC] == {"device_id": "{0.0.1.00000000}.{abc}"} and obs.volume[MIC] == 1.5


def test_devices_are_not_reopened_when_nothing_changed():
    obs = FakeObs()
    a = Audio(obs)
    a.start(CS2, mic_enabled=True, mic_device="default", mic_volume=100)
    assert sorted(obs.reopened) == [GAME, MIC]
    a.start(CS2, mic_enabled=True, mic_device="default", mic_volume=100)  # next match, same setup
    assert sorted(obs.reopened) == [GAME, MIC]  # reopening cost seconds of silence in a live test
    a.start(CS2, mic_enabled=True, mic_device="{0.0.1.00000000}.{other}", mic_volume=100)
    assert obs.reopened.count(MIC) == 2


def test_turning_the_microphone_off_mutes_it_for_the_next_game():
    obs = FakeObs()
    a = Audio(obs)
    a.start(CS2, mic_enabled=True, mic_device="default", mic_volume=100)
    a.start(CS2, mic_enabled=False, mic_device="default", mic_volume=100)
    assert MIC not in obs.live()


def test_encoder_settings():
    nvenc = encoders.settings("obs_nvenc_h264_tex", "Small")
    assert (nvenc["rate_control"], nvenc["cqp"], nvenc["lookahead"], nvenc["keyint_sec"]) == ("CQP", 23, False, 2)
    assert encoders.settings("obs_x264", "HQ") == {"keyint_sec": 2, "rate_control": "CRF", "crf": 18, "preset": "veryfast"}
    cbr = encoders.settings("obs_nvenc_h264_tex", "Small", bitrate_kbps=12000)
    assert (cbr["rate_control"], cbr["bitrate"]) == ("CBR", 12000) and "cqp" not in cbr
    assert all(e in encoders.NAMES for e in encoders.ORDER) and encoders.ORDER[-1] == "obs_x264"


def test_microphone_list_never_raises_without_the_registry_key():
    def missing(*args):
        raise OSError

    assert audio.microphones(open_key=missing) == []
    assert all(device.startswith("{0.0.1.00000000}.{") and name for device, name in audio.microphones())


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def audio_streams(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return len(json.loads(out)["streams"])


@pytest.fixture(scope="module")
def three_track_video(tmp_path_factory):
    root = tmp_path_factory.mktemp("tracks")
    folder = root / "Game" / "2026-11-11"
    folder.mkdir(parents=True)
    path = folder / "Replay multi.mkv"
    tones = [arg for hz in (440, 660, 880) for arg in ("-f", "lavfi", "-i", f"sine=frequency={hz}:duration=6")]
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x180:rate=30", *tones,
                    "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-g", "30", "-pix_fmt", "yuv420p", str(path)], check=True)
    return root, path


@needs_ffmpeg
def test_trimming_keeps_every_audio_track(three_track_video):
    _, path = three_track_video
    assert audio_streams(path) == 3
    assert audio_streams(library.trim(path, 1, 4)) == 3


@needs_ffmpeg
def test_filing_a_clip_keeps_every_audio_track(three_track_video, tmp_path):
    _, path = three_track_video
    copy = tmp_path / "Replay copy.mkv"
    shutil.copy(path, copy)
    clips.finish_clip(copy, {"exe": "g.exe"}, [], keep=3.0, pre=1.0)
    assert audio_streams(copy) == 3 and library.duration(copy) < 5


@needs_ffmpeg
def test_export_carries_only_the_mix_and_still_works_with_no_audio_at_all(three_track_video, tmp_path):
    root, path = three_track_video
    assert audio_streams(library.export(path, root)) == 1
    silent = root / "Game" / "2026-11-11" / "silent.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=30", "-pix_fmt", "yuv420p", str(silent)], check=True)
    assert audio_streams(library.export(silent, root)) == 0
