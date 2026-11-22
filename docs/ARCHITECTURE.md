# Laqta (لقطة) — architecture and milestone plan

Status: **built through M8** (see "Where things stand" at the end). Sections 1–7 are the
original plan; where the build differs, the last section says so.
Date: 2026-11-10. Target: Windows 10/11, Python 3.12, OBS 30.2+ (32.2.2 installed here).

Laqta is a free, local-only game recorder: it notices a game starting, drives OBS over
obs-websocket v5, and saves either the whole session, each match, or short clips around events.
No ads, accounts, telemetry, injection, memory reading or hooks.

Items marked **[verify]** come from research that could not be confirmed against a primary
source or a live run. Each is proven in the milestone named before anything is built on it.

## 1. Layout

*(The tree below is the original plan. Since then the UI has grown: `ui/cards.py` holds how one
video is drawn (card, event strip, the words under it), `ui/settings_parts.py` the pieces of the
settings window that stand alone, and `ui/player.py`, `ui/setup.py`, `ui/update.py`,
`ui/problem.py`, `ui/gameicons.py`, `ui/icons.py` are one window or one job each.)*


```
laqta/
  __main__.py          entry: load config, start core thread, run Qt; `obs-check` subcommand
  core/
    events.py          GameEvent dataclass + GamePlugin ABC
    recorder.py        pure state machine (injected clock + OBS object): modes, merging, post-roll
    app.py             core thread: one queue inbox, tick loop, wires everything
  obs_controller/
    client.py          thin obsws-python wrapper: timeouts, liveness, reconnect, any error -> ObsDown
    setup.py           read websocket config, launch OBS, ensure Laqta profile/collection/scene/source
  game_detector/
    detector.py        psutil poll + foreground-fullscreen heuristic (pure function over snapshots)
    library_scan.py    Steam / Epic / GOG / Battle.net / EA -> exe candidates
    games.json         community-editable DB: exe, display name, plugin
  plugins/
    __init__.py        PLUGINS = {"cs2": Cs2Plugin, ...}
    cs2_gsi.py  lol_live.py  valorant_vision.py
  ui/
    tray.py            tray icon, status, toggles, toasts, global hotkey
    settings.py        settings, game list, "is this a game?" prompt
    library.py         clip library
    i18n.py            one dict {key: (en, ar)} + RTL switch
  storage/
    config.py          TOML in %APPDATA%/Laqta
    clips.py           folder scan + sidecar JSON, ffmpeg thumbnail/trim/export, cleanup
tests/                 fake OBS, fake clock, recorded API payloads, sample frames
```

## 2. Threads

| Thread | Owns | Rule |
|---|---|---|
| Qt main | UI, `RegisterHotKey` message | never calls OBS |
| Core | the only `ReqClient`, recorder, detector poll | loop is `inbox.get(timeout=next_deadline)`; post-roll and debounce are deadlines |
| obsws `EventClient` | OBS events | callbacks only `inbox.put(...)`; never call OBS, never disconnect (deadlocks) |
| One per active plugin | GSI HTTP server / LoL poller / vision loop | calls `emit`, which is `inbox.put` |

UI -> core is `inbox.put`. Core -> UI is one `QObject` signal `state_changed(dict)`, queued by Qt.

`obsws-python` has no locks and no reconnect, its default timeout blocks forever, and a dead
socket raises raw websocket/OS errors. So exactly one thread touches the request client, every
call has a timeout, and any exception becomes `ObsDown`.

CPU budget: process names polled every 2 s when idle; while a game is active only
`pid_exists` plus the foreground window. Vision runs at 2–5 fps on ROI crops only.

## 3. Plugin interface

```python
@dataclass(frozen=True)
class GameEvent:
    type: str                 # kill | multikill | death | assist | headshot | ace | win | lose
                              # | match_start | match_end | manual | ...
    t: float                  # time.monotonic() at detection
    confidence: float = 1.0
    source: str = "api"       # api | vision | audio | manual
    data: dict = field(default_factory=dict)

class GamePlugin(ABC):
    name: ClassVar[str]
    event_types: ClassVar[tuple[str, ...]]            # drives the per-event user toggles
    def setup(self, game: dict) -> str | None: ...    # optional one-time install (e.g. GSI cfg)
    @abstractmethod
    def start(self, emit: Callable[[GameEvent], None], game: dict) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...
```

Match start/end are ordinary events (`match_start`, `match_end`) on the same stream rather than
separate `detect_match_start/end` methods: one path to test, same information. The recorder
drops events that are toggled off or under the confidence threshold. The manual "clip this"
hotkey is a `GameEvent("manual")` that bypasses both and works for every game.

## 4. Recording state machine

`IDLE -> WAIT_OBS -> ACTIVE -> EXIT_DEBOUNCE -> IDLE`

- **Game started**: ignored if a game is already active (first wins; absorbs duplicate
  triggers). Otherwise ensure OBS, retarget the capture source, apply the game's parameters,
  set the output folder `Videos/Laqta/<Game>/<date>/`, then act by mode.
- **Full Session**: start recording now, stop after exit debounce.
- **Full Match**: `match_start` starts, `match_end` stops after post padding. Game exit forces a stop.
- **Events Only**: replay buffer runs with length `pre + max_clip`.
  - First event opens a window `(t, t + post)`.
  - Another event inside the window moves the deadline to `t + post` — this is the merge.
  - Same event type within 1 s is dropped as a duplicate.
  - At the deadline: `SaveReplayBuffer`, wait for `ReplayBufferSaved`, move the file, write a
    sidecar JSON listing the events, trim with `ffmpeg -c copy` to `[first - pre, deadline]`.
  - `SaveReplayBuffer` takes no parameters, so post-roll is done by delaying the save.
- **Hybrid**: Full Session plus each event appended to `<recording>.events.json` as an offset
  from recording start. OBS chapters are added best-effort (Hybrid MP4 only). Cutting is done
  later from the library.
- **Game exited or crashed**: 10 s debounce, cancelled if the exe reappears. On expiry: flush
  any open clip window, stop outputs, stop the plugin.
- **OBS down** (exit event, socket error, or failed 5 s liveness check): keep the game, retry
  every 3 s, relaunch OBS if its process is gone. On reconnect, adopt a still-running recording
  or start a new segment. Recordings are MKV so a crashed file stays playable.
- **Alt-tab**: process liveness governs the session, not foreground.
- **Multi-monitor**: only the monitor-capture fallback cares; it picks the monitor holding the game window.

## 5. OBS ownership

Laqta creates and uses its own OBS **profile** and **scene collection**, and leaves the user's
existing ones (`LOL`, `Untitled`, …) untouched. It refuses to take over if OBS is already
streaming or recording.

Verified live on OBS 33.0.1 / obs-websocket 5.7.6 (M1):

- The replay buffer only exists once the profile has been *loaded* with `SimpleOutput/RecRB=true`.
  Setting the parameter is not enough; the profile must be reloaded (switch away and back).
- Parameters set immediately after `CreateProfile` can be silently overwritten while the new
  profile settles, so setup re-reads and repeats until they stick.
- `SetRecordDirectory` takes effect for both the next recording and the replay buffer.
- OBS crashed once (in its bundled browser component) when profiles were switched back to back.
  Setup now pauses 1.5 s after every profile or collection change, and Laqta launches OBS with
  `--profile Laqta --collection Laqta` so normal use involves no switching at all.
- After a crash or kill, OBS 33 blocks startup on a "launch in safe mode?" dialog
  (`--disable-shutdown-check` no longer exists). Laqta clears OBS's `.sentinel/run_*` markers
  before launching it; verified: relaunch connects in ~2 s with no prompt.

Capture: Game Capture (`any_fullscreen`, `anti_cheat_hook` on) first. If frames stay black
after a few seconds, fall back to Window Capture (WGC), then Display Capture **[verify, M1/M5]**.

Deviation from the brief: one Laqta scene with a retargeted source and one profile whose
parameters are rewritten per game, instead of a literal scene and profile per game. Behaviour
is the same with less state to keep in sync.

## 6. Milestones

| M | Scope | Done when | Deferred |
|---|---|---|---|
| M0 | `pyproject`, package skeleton, git init | `python -m laqta --version` and `pytest` pass | everything else |
| M1 | OBS spike: `client.py` + `setup.py` | `python -m laqta obs-check` launches OBS, creates the Laqta profile/collection/scene, records 5 s, saves a replay, prints both paths; killing OBS mid-run prints "reconnected" | per-game parameters |
| **M2 (MVP)** | Detector on `games.json` + manual entries; recorder with Full Session + Events Only via hotkey; tray status and toasts | tests green (merge, post-roll, debounce, OBS-down with fake OBS and clock; detector on snapshots). By hand: add `notepad.exe` as a game -> recording appears on close; hotkey -> clip after post-roll; CPU under 3 % | settings UI, library scan, Full Match, Hybrid, trim |
| M3 | CS2 GSI plugin, Full Match, Hybrid | recorded GSI payloads replayed through the listener yield the expected events; a live match produces a kill clip and a match file | Dota 2 |
| M4 | LoL Live Client plugin | recorded `eventdata` sequences yield expected events with no duplicates across polls; refused connection means idle | — |
| M5 | Vision template plugin (Valorant killfeed) | supplied screenshots: positives detect, negatives and black frames do not; loop under 3 % CPU at 3 fps | other vision games, audio |
| M6 | Settings UI, AR/EN + RTL, add/remove games, library scan, unknown-fullscreen prompt, per-game mode/parameters/toggles | language switch flips layout; a scanned Steam game appears; prompt fires once per unknown exe | — |
| M7 | Clip library: thumbnails, filters, trim, export, open folder, delete, cleanup | tests on cleanup by age/size and the ffmpeg command builder; a trimmed clip plays | — |
| M8 | Remaining tests, README (install, OBS setup, adding a plugin), PyInstaller build, "add a game plugin" skill | `dist/Laqta.exe` runs on a clean user profile | installer polish, auto-update |

Until M6, modes and toggles are edited in the TOML file.

## 7. Choices made

| Chosen | Rejected | Why |
|---|---|---|
| Core thread owns the OBS socket | separate OBS worker + lock | a second queue for no gain |
| `RegisterHotKey` | `keyboard` / `pynput` | those use low-level hooks, which the constraints forbid |
| Folder + sidecar JSON as clip index | SQLite | add when scanning is measurably slow |
| OBS Simple output mode | Advanced | more parameters, same result |
| 2 s `psutil` poll | WMI process events | COM threading, harder to test |
| Plugins as an in-tree dict | entry-point discovery | add when third-party plugins exist |
| i18n as one dict | Qt Linguist toolchain | two languages, few strings |
| Audio cue detection not built | sound fingerprinting | research found no reliability data; it is a confidence booster at best |

## 8. Risks and limits per game

| Game | Method | Risks / limits |
|---|---|---|
| CS2 | GSI (game POSTs JSON to localhost) | Only the local player's data; kills come from counter deltas, so two kills in one 0.1 s window can merge; clutch is a guess (no teammate data); must gate on own SteamID or spectated kills get counted. Field names **[verify, M3]** against live payloads. Needs a cfg file written into the game folder. |
| LoL | Live Client Data API, port 2999 | Verified against a real match's event list: `GameEnd` (`Result`), `FirstBlood` (`Recipient`), `Multikill`, `Ace`, and names without the `#tag`. If the game process exits before a poll sees `GameEnd`, the match still ends but without a win/lose. No key needed; wide distribution may fall under Riot's product registration. Game Capture worked in window mode. |
| Valorant | Vision (killfeed) | No official statement that passive screen reading is permitted — no known bans, but not confirmed safe. Game Capture may be black under Vanguard. HUD details are from memory; colour-blind modes and patches break templates. **Needs real screenshots from you.** |
| Dota 2 | GSI | Same mechanism as CS2; needs the `-gamestateintegration` launch option; Valve notes a possible framerate cost. Not in this plan's milestones. |
| Rocket League | Official Stats API (TCP 49123) | Plugin written. The API is the game's own feature, switched on in the user's `TAStatsAPI.ini`; the message format comes from third-party descriptions, and whether it streams in online matches under EAC is unconfirmed until a real match. No local-player marker: matched by Steam id or typed name. |
| Apex / Fortnite / OW2 / COD | Vision | Signals are mostly text, so they depend on language and need OCR. Later, using the Valorant plugin as the template. |

General: clips trimmed with stream copy can start up to ~2 s early (keyframes). The
OBS WebSocket server must be enabled once by the user (it is currently off on this machine).

## 9. Where things stand

Decisions made after the original plan are recorded in [docs/adr](adr/). The v2 work follows
[PLAN-v2.md](PLAN-v2.md).

| Area | State | How it was checked |
|---|---|---|
| OBS control, reconnect, crash relaunch | Works | Live on OBS 33.0.1, including killing OBS |
| Game detection (process list) | Works | Live with League of Legends |
| Full Session recording | Works | Real matches: 3440x1440, 60 fps, with sound |
| Advanced output, encoder choice | Works for NVENC and x264 | Live on OBS 33; AMF and Quick Sync unverified |
| Per-game video settings, second monitor | Works | Live on OBS 33: sizes, fps, bitrate, x264, portrait second monitor; not yet in a real game |
| Skipped-frames report | Works | A real League practice game (3440x1440, 60 fps, 12 Mbit) logged 0.3 % skipped, down from 4.3 % before the lighter preset; the over-2 % toast itself has not fired for real |
| Microphone track, game-only audio | Works against ordinary apps | Live with test tones; not yet in a real game |
| Events Only + clip hotkey | Works | In real Rocket League play: 351 clips saved in one evening (most of them wrongly, see the shout row); the hotkey itself not confirmed there |
| Hybrid and Full Match | Unit-tested | Not yet run in a real game |
| Capture fallback (black picture) | Works, was too eager | Real play: Deadlock and The Outlast Trials settled on window capture; Rocket League's black intro pushed it to whole-screen capture, which was then remembered. Now waits 20 s per method, announces whole-screen capture and never starts there |
| League of Legends events | Works | Replay of a real match's event list; and live in a Practice Tool game recorded in Full session: match start, a dragon, the win and match end all landed in the recording's marks |
| CS2 events | Unit-tested | Hand-built payloads only; no real match yet |
| Rocket League events | Works in real matches | The owner's evening: connected to the real Stats API, 41 match starts, 14 goals, 15 saves, 12 demolitions, 24 assists, 12 wins, 5 losses logged. Not compared event by event with the footage; no raw capture kept as a fixture yet |
| Valorant events | Not working | Framework only; no template images. Waits on a real recording |
| Template calibration (`calibrate`) | Works | Whole loop in tests on a generated video; frame seek and region outlines checked on a real 3440x1440 recording against ffmpeg |
| Loudness as a shout trigger | Failed in real use, demoted | 297 of 351 clips in one evening were ordinary speech into a loud microphone (recogniser on 47 of them: speech 43, laugh 4, shout 0; microphone peaks above full scale). Loudness now only nominates a moment in Events only and the recogniser decides |
| Laughs and shouts (sound recogniser) | Works on samples and on the owner's speech | Public-domain laughs as laughs, screams as shouts, synthetic speech as neither; on 47 of the owner's real clips it called loud talking speech every time and found 4 laughs. Its judging of nominated clips is tested end to end on generated clips; not yet run during real play |
| In-app video player | Works | Driven as the real windows on a 3440x1440 60 fps three-track file: plays at full rate, exact frame on seek while paused, sound tracks switch, event symbols jump, "Play selection" stops at the end handle; loads inside the packaged app. Not operated by hand |
| Event symbols (timeline, cards, side-panel strip) | Works | Rendered on a real recording; clicking a symbol was not exercised by hand |
| Multi-kills for screen-reading plugins | Unit-tested | No calibrated vision plugin exists to produce real kills yet |
| Settings, Arabic/RTL, colours | Works | Rendered and driven by script; not clicked through by hand |
| Clip library, tags, favourites, search, match summary | Works | Driven by script on test videos |
| Trim and export editor, export presets | Works | Real ffmpeg encodes in tests; driven by script, including on a real 17-minute recording, and with the video player; not operated by hand |
| Highlight reel | Works | Real join of mixed-size sources in tests; driven by script through the library window |
| Instant-replay hotkey | Unit-tested | Recorder logic tested; the second global hotkey registers; not pressed in a real game |
| Disk-space guard | Works | Live on OBS 33 with the free-space figure faked: refused at start, resumed, stopped mid-recording into a playable file. Never run on a disk that was really full |
| Start with Windows | Unit-tested | Real registry writes in a throwaway key; the real Run entry has not been through a sign-in |
| Health page, diagnostic zip | Works | Run on this PC from the window and the command line; secret removal tested with planted secrets |
| Copy clip to clipboard | Works | Driven through the Clips window; the clipboard held the file |
| Post to Discord | Unit-tested | Request built and checked against a stand-in; the window flow (confirm, too big, offline) driven by script; never uploaded to a real channel |
| Corner note window | Works on the desktop | On the real desktop: shown without taking the foreground window, click-through and topmost styles set. Not seen over a game |
| Main window on start, second launch re-opens it | Works | Driven as the real app: opens, closes to the tray, re-opened by a second launch |
| One window (clips and settings views) | Works | Driven as the real app: save, cancel, edits kept while switching views, Settings from the tray, rebuilt on a language change |
| Game icons | Works | Read from this PC's real installs (Steam and Riot): six games found in half a second; Epic and GOG paths untested here |
| Multi-select in the clips window | Works | Driven on sample videos: favourite, export, delete (with a question for several) |
| Storage page | Works | Rendered on the owner's real library (36 GB over two games) |
| Start/stop recording hotkey | Unit-tested | The decision of what to record and the press-again-to-stop logic tested; the foreground window read live; no hand-started recording made yet |
| Laughs and shouts in real play | Works | The owner's sessions on 0.6.1: laughs found in League and Pummel Party (59 in one 90-minute party game), clips cut around them; no false shout clips reported |
| Problem message instead of raw error boxes | Works | Rendered; hooks tested with errors on the GUI and a background thread (shown once, all logged) |
| Player speed, frame steps, marks while watching, remembered volume | Works | Driven on a 60 fps test video: 2x played 4 s in 2 s, one step moved 16 ms, M wrote a mark to the file |
| League champion and mode on cards | Unit-tested | From Riot's documented playerlist and gamestats shapes; LAQTA_LOL_DUMP saves the real answers to check against; not yet seen in a real match |
| Clips window sorting | Works | On the owner's real library: by date, size and length |
| Save without the selected part | Works | Real ffmpeg in tests: 12 s minus 3.5 s gave 8.5 s with both sound tracks; marks after the gap moved up, marks inside it dropped |
| Pause/resume hotkey | Works with OBS | OBS 33 paused and resumed Laqta's recording: 9 s with 3 paused gave a 5.9 s file; mark shifting and paused length tested in the recorder; not pressed during a real game |
| First-run setup screen | Works with a made-up empty PC | Every step and button driven by script with the installs faked; the WebSocket switch tested on a copy of OBS's real settings file. Never run on a PC that really lacks OBS or ffmpeg |
| Pre-publish smoke test | Works | Run on the packaged app: 5 of 5 passes; it caught a genuinely broken build during development |
| Update rollback | Works | For real, on a test copy: a deliberately broken build was undone after 45 s and the old version restarted; a good build was kept after about 12 s |
| Automatic clips per event | Works on a test recording | Real ffmpeg cut in tests; not yet seen after a real match |
| Self-update from GitHub releases | see ADR 0005 | Check, hash-verified download and file swap described there with what was run for real |
| Library scan | Steam, Epic, GOG, Riot, Battle.net, EA | Steam and Riot checked on real installs; Epic and GOG from fixtures; Battle.net and EA from Windows' installed-programs list, read for real here (410 entries in 0.01 s) but with no Blizzard or EA game on this PC, so matched only against a made-up list |
| Unknown-fullscreen prompt | Unit-tested | Has not fired for real |
| Packaged exe | Builds, imports cleanly | Not run as the only instance |

Differences from the plan above:

- **Capture** aims at the game's own window and falls back on a black picture; the
  "any fullscreen app" mode in the plan recorded 19 minutes of black in League.
- **Canvas** follows the primary monitor's size, and a desktop-audio source is added: a new
  OBS scene collection has neither.
- **Process detection** uses a Toolhelp snapshot instead of psutil, so no process is opened.
- **`i18n.py`** lives at the package root, not under `ui/`, because the core emits toasts.
- **Clip library data** is in `storage/library.py`; `storage/clips.py` only files new clips.
- **Unknown-app prompt** is a clickable toast rather than a dialog, so it cannot take focus
  from a fullscreen game.
- **`core/health.py`** holds the health checks (`gather` reads the machine, `checks` is pure)
  and the diagnostic zip; **`storage/autostart.py`** owns the Run-key entry. See
  [ADR 0003](adr/0003-reliability.md).
- **Laughs and shouts** are found after the recording, not live: the microphone track is read
  with ffmpeg and YAMNet (Google, Apache 2.0, the MediaPipe copy in `laqta/voice/`) runs on
  LiteRT, one thread, only on windows louder than -38 dBFS. Each kind's class scores are
  summed (a scream is spread over Yell, Screaming and Shout). See [ADR 0006](adr/0006-player-and-voice.md).
- **Loudness nominates, the recogniser decides.** OBS's volume-meter events are judged on OBS's
  event thread by `audio.Shout`, only while an Events-only game is running. A nominated clip
  is listened to by `voice` on its own thread and deleted unless a laugh or shout is in it;
  a clip that also has a game event or a hotkey press is always kept.
- **`plugins/scoring.py`** derives multi-kills from plain kills for screen-reading plugins.
  Clutch detection, in the v2 plan, was dropped: CS2's Game State Integration gives a playing
  client no teammate state, and no other supported game has the concept.
- **Full session keeps event marks.** It was built to keep none (that was Hybrid's job), and a
  real League game showed the result: objectives taken, nothing in the recording. Hybrid is
  now the same mode under an older name.
- **Not built:** audio cue detection, OBS chapter markers (recordings are MKV), a Dota 2
  plugin, an installer (the build produces a zip).
