# ADR 0006: A video player inside Laqta, and telling laughs from shouts

Date: 2026-11-13. Status: accepted. Requested by the owner: "a video player inside the app so
when cutting or viewing I can stay inside it", and "the app can see the difference between a
laugh and a shout (scream)".

## Video player

**Qt Multimedia** (from PySide6-Addons), with Qt's own FFmpeg backend, so playback does not
depend on anything installed on a friend's PC. Measured on a 3440x1440, 60 fps, three-track
file: full frame rate, about 50 ms to open, an exact frame on every seek while paused, audio
tracks switchable. This reverses ADR 0002's "no player" for the preview; the earlier estimate
of 150 MB was wrong: the packaged download grew from 94 MB to 117 MB (player and recogniser
together).

One `Player` widget serves both the watch window (with a seek bar carrying the event symbols)
and the trim window (where the filmstrip is the seek bar). Play falls back to the system's
player if Qt Multimedia cannot load, and the Health page reports that case.

## Laughs and shouts

The loudness-rhythm guess (fed by OBS's volume meters) could not tell a laugh from loud
talking or a scream. It is replaced by a sound classifier run **after** the recording, on the
microphone track (track 3), so nothing runs during play.

**YAMNet** (Google, Apache 2.0; the 4 MB float model published for MediaPipe, with its label
list inside) on **LiteRT** (`ai-edge-litert`, Google's TensorFlow Lite runtime, about 11 MB in
the package), one thread. Silent windows are skipped. A kind's class scores are summed, because
a scream spreads over Yell, Screaming and Shout and none of them is high alone.

| Sample | Result |
|---|---|
| Laughter (Wikimedia Commons, public domain), 3 recordings | laughs only, scores 0.69 to 1.0 |
| Wilhelm scream, a second scream (CC0) | one shout each, 0.46 to 0.48 |
| Calm and fast urgent synthetic speech | nothing (laugh and shout scores 0.00) |

About 0.12 s per minute of sound on one thread. *Rejected:* onnxruntime (no official YAMNet
model for it), TensorFlow (hundreds of MB), hand-tuned pitch and rhythm rules (no way to tune
them honestly without many real recordings).

## Consequences

- Events only keeps no full recording, so it gets no laugh and shout marks; its live,
  loudness-based shout trigger stays as it was.
- Detection has not yet been tried on the owner's own voice or with game sound bleeding into
  the microphone. `laqta voice <video>` lists what it hears, for checking.
- Test audio lives in `tests/fixtures/voice/` with its sources.
