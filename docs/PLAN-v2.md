# Laqta v2 — milestone plan

Status: **awaiting approval. No code for this plan has been written.**
Date: 2026-11-11. Based on four research reports, a probe of the live OBS 33.0.1 on this
machine, and a read of the current code. Items marked **[verify]** are not yet proven.

## 1. Scope recommendation

The brief lists about 40 features. The recommendation is to build about 22 and defer the
rest, under one rule: **nothing ships that cannot be checked on this machine.** Two plugins
(CS2, Valorant) are already unverified; adding more of those makes the app less trustworthy,
not more capable.

### Proposed cuts and deferrals

| Feature | Recommendation | Why |
|---|---|---|
| Dota 2 plugin | Defer | Not installed here; no real payload capture exists anywhere to build from |
| Apex and Fortnite vision plugins | Defer | Not installed here; templates cannot be cut or tested |
| Overwatch 2 vision plugin | Defer until Valorant is calibrated | Installed, but the template game must work first |
| Battle.net and EA library scan | Defer | Neither launcher is installed; their formats are unconfirmed |
| Update checker | Defer | There is no GitHub repository for it to check |
| Inno Setup installer | Defer | Tied to having a release channel; the zip works meanwhile |
| Local HTTP trigger for Stream Deck | Cut | Stream Deck's built-in Hotkey action can press the existing clip hotkey |
| "Share over Tailscale" | Defer | A network-facing server is the riskiest item on the list for the least daily use |
| Slow motion, zoom, captions | Defer | Recipes are proven, but each needs its own editing UI |
| Generic audio-spike detector | Defer | No evidence it is reliable; the opt-in mic trigger covers the cheap case |
| games.json import/export packs | Defer | The file is already hand-editable and shareable |
| "Smart cut" (re-encode only the edges) | Cut | Tested here: it produces broken joins while reporting success |

Any of these can be put back; say which.

### Order

Spike S1, then M1 and M2 (every recording improves, and the skipped frames get fixed),
M3, M6 (protects recordings), M4 (mostly waits on play sessions, so it interleaves), M5.

## 2. Milestones

| M | Scope | Main files | Tests | Needs you | Size |
|---|---|---|---|---|---|
| S1 | Spike: OBS Advanced output mode on the Laqta profile | throwaway script; findings into ARCHITECTURE.md | none | OBS running | S |
| M1 | Game-only audio (per-process capture, desktop audio as fallback); microphone with device, on/off, volume; tracks 1 = mix, 2 = game, 3 = mic | `obs_controller/capture.py`, `setup.py`, `storage/config.py`, `ui/settings.py` (Audio page), `storage/library.py` | fake OBS: retarget, fallback, mic off; trim/export keep all tracks | Listen to one recording; pick the mic | M |
| M2 | Per-game fps, output height, bitrate, encoder with global fallback; canvas and capture follow the game's monitor; skipped-frames report | `setup.py`, `capture.py`, `game_detector/detector.py`, `core/app.py`, `ui/settings.py` | override merge, monitor match by position, encoder fallback | One 3440×1440 match; a second-monitor test | M |
| M3a | Tags, favourites (exempt from cleanup), text search, match summary card; second hotkey "last 30 s" | `storage/library.py`, `clips.py`, `ui/library.py`, `ui/hotkey.py`, `core/recorder.py` | library and recorder tests | One click-through | S |
| M3b | Filmstrip scrubber with handles and event markers; fast or exact trim; export presets (9:16, Discord ≤ 10 MB, 1080p60) with progress and cancel | new `storage/export.py`, `ui/library.py` | command builders, bitrate formula, progress parsing, cancel | Judge the scrubber; play the outputs | L |
| M3c | Highlight reel: score = type weight × confidence, top windows, joined with ffmpeg | `storage/export.py`, `ui/library.py` | selection, merging overlapping windows | Watch one reel | S |
| M4a | Shared multi-kill / ace / clutch scoring; `calibrate` command; CS2 live check; Valorant calibration | new `plugins/scoring.py`, `cs2_gsi.py`, `vision.py`, `__main__.py` | scoring; CS2 and vision tests on real captures | Recordings (section 5) | M |
| M4b | Rocket League plugin; opt-in microphone-loudness trigger | new `plugins/rocket_league.py`, `games.json`, `config.py` | replay of a payload captured here | Enable the game's ini; a bot match and an online match | M |
| M5 | Discord webhook post; copy clip to clipboard; small click-through "Clip saved" window | new `storage/share.py`, new `ui/toast.py`, `ui/library.py`, `ui/tray.py` | fake HTTP; window flags | A webhook URL; check over a borderless game | M |
| M6 | Disk-space guard; start with Windows; Health page with fix-it hints; diagnostic zip with secrets stripped | new `core/health.py`, `core/recorder.py`, `ui/settings.py`, `__main__.py` | each check; redaction of webhook URL and OBS password | One reboot | M |

Every milestone adds its strings to `i18n.py` in both languages, its settings to `DEFAULTS`
and `_check()`, a README and ARCHITECTURE update, a code-review pass, and one commit per feature.

## 3. Key decisions

**M1 Audio**
- Switch the Laqta profile to OBS Advanced output if S1 passes. *Rejected:* stay in Simple
  mode with everything mixed into one track; that is the fallback if S1 fails, and
  "microphone on its own track" is dropped in that case.
- Track 1 is always the full mix, so every player and existing export keeps working.
  *Rejected:* game and mic only on separate tracks, which most players would play with no mic.
- Fall back to desktop audio only when no game window matches. *Rejected:* silence detection.

**M2 Video**
- Encoder choice is "try it, fall back to x264, remember what worked". *Rejected:* parsing
  the OBS log, whose format changes between versions.
- Fix the overload seen in a real match (4.3 % of frames skipped at 3440×1440, 60 fps) with a
  lighter default preset plus optional downscale, and report skipped frames after each
  recording. *Rejected:* tuning quality automatically mid-match.
- Find the game's monitor from its window position. *Rejected:* a monitor picker setting.

**M3 Library**
- Scrubber is a filmstrip of ffmpeg thumbnails with single-frame preview. *Rejected:*
  Qt's video player, which adds about 150 MB to the build.
- Exact trim re-encodes the whole clip. *Rejected:* smart cut, proven unreliable here.
- Tags and favourites live in each video's sidecar JSON. *Rejected:* a database.
- "Last 30 s" saves at once in Events mode and adds a marker in other modes. *Rejected:*
  running a replay buffer beside every full recording, until its cost is measured **[verify]**.

**M4 Detection**
- No plugin without a real capture from this machine as its test fixture.
- Rocket League identifies you by a name or platform ID typed in settings; the game provides
  no "this is you" marker.
- Mic loudness reads OBS's own volume meters **[verify]**. *Rejected:* a Python audio dependency.

**M5 Sharing**
- The webhook URL is stored in settings, the feature is off until it is set, and each post is
  an explicit click. *Rejected:* posting automatically when a clip is saved.
- The toast is a frameless, no-focus, click-through window. It will not show over exclusive
  fullscreen. *Rejected:* anything drawn inside the game, which the constraints forbid.

**M6 Reliability**
- Health checks are pure functions returning a status and a hint, shown in Settings and
  reused by the diagnostic zip.
- The disk guard refuses to start and stops cleanly below a threshold. *Rejected:* deleting
  old clips beyond the existing cleanup rules.

## 4. Spikes that come first

| Spike | Question | Blocks | If it fails |
|---|---|---|---|
| S1 | In Advanced mode: do the parameters stick, does the replay buffer still work, do tracks 1–3 land in the file, and does OBS pick up encoder settings written into the profile folder? | M1 tracks, M2 bitrate and encoder | Stay in Simple mode: one track, coarse encoder choice, downscale only |
| S2 | Does per-process audio capture deliver sound for Valorant and CS2? | M1 default | Desktop audio stays the default for those games |
| S3 | Does Rocket League's Stats API stream in online matches, and what does a real payload look like? | M4b | Offline and private matches only, or dropped |
| S4 | How long does a filmstrip take to build for a 30-minute recording? | M3b | A coarser strip, or the number boxes stay |

## 5. What only you can provide or decide

**Decisions**
1. Approve, or change, the cut list in section 1.
2. Approve switching Laqta's own OBS profile to Advanced output mode.
3. Confirm the filmstrip scrubber rather than a full video player.
4. The default key for the "last 30 s" hotkey.
5. Whether to create a GitHub repository (unblocks the update check and installer later).
6. Consent to Laqta editing Rocket League's Stats API ini file.
7. A Discord webhook URL, when M5 is reached.

**Recordings and sessions** (native resolution, default HUD scale, English client)
- **CS2:** one bot match with Laqta running: at least 5 kills, 1 death, a round win and a
  round loss, played to the end.
- **Valorant:** one Laqta recording of 3+ minutes: at least 5 kills including one headshot and
  one double kill, 1 death, and 2 minutes with no kills. Say whether the picture was black.
- **Rocket League:** your display name or platform ID; one bot exhibition with a goal, an
  assist or save, and the match end; then one online match.
- **Audio:** one recording while talking, then confirm the three tracks.
- **Video:** one 3440×1440 match after M2; the skipped-frames figure.
- **Other:** a review of the Arabic wording; one reboot for autostart.

## 6. Top risks

1. **S1 fails or is flaky.** Profile reloads already crashed OBS once, and encoder settings
   in Advanced mode live in a file OBS reads, outside websocket control.
2. **Existing trim and export may drop extra audio tracks.** Unchecked; it is the first test in M1.
3. **Anti-cheat titles** may give no per-process audio or a black picture. Only real sessions tell.
4. **Rocket League** has no local-player marker and an undocumented format; a patch can break it silently.
5. **The scrubber** is the largest item and its quality is subjective; S4 limits the waste.
6. **Vision templates** break with game patches and HUD settings. `calibrate` makes re-cutting
   cheap but does not remove the upkeep.
7. **The webhook URL** is a secret in a plain settings file. The diagnostic zip strips it, but
   the file itself is not protected.
