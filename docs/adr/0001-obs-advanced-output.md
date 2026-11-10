# ADR 0001: Use OBS Advanced output mode for Laqta's profile

Date: 2026-11-11. Status: accepted.

## Context

Laqta drove OBS in Simple output mode. Simple mode records one audio track and exposes only
a coarse encoder choice and quality preset. Two requirements do not fit:

- the microphone on its own track, and the game separate from voice chat;
- real control of encoder, bitrate and preset. A real 3440×1440, 60 fps match skipped 4.3 %
  of frames with Simple mode's NVENC preset.

## Decision

Laqta's own OBS profile runs in **Advanced** output mode. Profile parameters are set over
obs-websocket (`AdvOut/*`); encoder settings are written to `recordEncoder.json` in the
profile folder while a different profile is current, then the profile is reloaded.

Audio tracks: 1 = everything, 2 = the game (or the desktop), 3 = the microphone. Track 1 is
always the full mix so ordinary players and exports keep working.

Encoders are tried in a fixed order (NVENC, AMF, Quick Sync, x264) by starting the replay
buffer for a moment; the first that starts is remembered.

## Evidence (spike S1, OBS 33.0.1, obs-websocket 5.7.6)

- `AdvOut` parameters survived a profile reload.
- The replay buffer was available and saved a file.
- Three audio tracks appeared in both the recording and the saved replay.
- A 12 000 kbit/s CBR setting written to `recordEncoder.json` produced a 12.5 Mbit/s file.
- No OBS crash across repeated runs, with the existing 1.5 s pause after each profile switch.

## Alternatives rejected

- **Stay in Simple mode, mix the microphone into the single track.** No way to mute the
  microphone afterwards, and no fix for the encoder overload.
- **List encoders by parsing the OBS log.** The log format is not a contract and changes
  between versions.
- **Game and microphone only on separate tracks, no mix.** Most players play track 1 only,
  so recordings would seem to have no microphone.

## Consequences

- Laqta now writes a file inside OBS's profile folder (its own profile only).
- Changing quality or the microphone setting reloads the profile at the next game start
  (about 3 s).
- AMF and Quick Sync settings are written from documentation and are **unverified**: this
  machine has an NVIDIA card.
- Everything that copies video with ffmpeg must pass `-map 0`, or extra tracks are dropped
  silently. Trim and clip filing do; MP4 export deliberately keeps only track 1.
