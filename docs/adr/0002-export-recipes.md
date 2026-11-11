# ADR 0002: How Laqta trims and exports video

Date: 2026-11-11. Status: accepted.

## Context

The library needs a frame-accurate trim, sharing presets, a scrubber, progress and cancel.
All recipes below were run on this machine's ffmpeg 9.0.2 before being chosen.

## Decisions

**Exact trim re-encodes the whole selection** (`libx264 -crf 18 -preset veryfast`). The fast
trim stays a stream copy.
*Rejected: "smart cut"* (re-encode only the edges, stream-copy the middle, join). Tested: the
joins produced non-monotonic timestamps and duplicate frames, MKV applied the wrong encoder
header to the copied part, and ffmpeg still exited with success, so failure could not even
be detected. Measured instead: for a cut at 3.3–7.7 s the stream copy is off by more than
0.3 s; the re-encode is within 0.08 s.

**The scrubber is a filmstrip of stills, not a video player.**
*Rejected: Qt Multimedia*, which is not in the Qt package Laqta ships and would add about
150 MB. Measured on a real 17-minute 3440×1440 recording: 24 strip tiles in about 7 s on four
threads, one preview frame in about 0.5 s. So tiles appear progressively, the preview updates
when the hand rests, and "Zoom to selection" refetches the strip for fine cuts.

**Presets**

| Preset | Recipe | Checked |
|---|---|---|
| Vertical 9:16 | centre crop `ih*9/16`, scale to 1080×1920 | output is 1080×1920 |
| Discord ≤ 10 MB | 720p, 30 fps, bitrate from length with 5 % headroom; size checked after, retried at 15 % and 28 % headroom | 10 s and 15 s clips under the limit |
| 1080p 60 MP4 | fit inside 1920×1080 keeping shape, even dimensions | 2560×1080 → 1920×810 |

All use software x264: it is present everywhere and its output size is predictable.
*Rejected for now: GPU encoders for export*, whose bitrate control is looser and which may be
busy recording.

**Cancel writes `q` to ffmpeg's stdin.** *Rejected: `terminate()`*, which left an MP4 with no
index and an empty MKV in testing. A cancelled or failed export deletes its partial file.

**"Leave my microphone out"** maps audio track 2 (the game) instead of track 1 (the mix).

**Exports are written to `<name>.part.<ext>` and renamed when complete.** A failed or
cancelled export can therefore never damage an earlier file of the same name. Found by
driving the highlight reel: rebuilding one and cancelling would have deleted the previous reel.

**Highlight reel:** score = event-type weight x confidence; windows of 8 s before and 4 s after
each event, overlapping windows merged with scores added; best windows kept until 60 s is
full; sources fitted into 1920x1080 (letterboxed) and joined with the concat filter, since
they can differ in size and frame rate.

## Consequences

- Exports are slower than a remux: roughly real-time-or-better at these settings.
- A 21:9 source cropped to 9:16 keeps only the middle 607 pixels and is upscaled; it looks soft.
- The Discord preset reports "too long to fit in 10 MB" rather than producing mush.
- Captions, slow motion and zoom recipes were also tested but are not built (see PLAN-v2.md).
