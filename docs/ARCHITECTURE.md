# Laqta (لقطة) — architecture and milestone plan

Status: **draft, awaiting approval**. No code exists yet.
Date: 2026-11-10. Target: Windows 10/11, Python 3.12, OBS 30.2+ (32.2.2 installed here).

Laqta is a free, local-only game recorder: it notices a game starting, drives OBS over
obs-websocket v5, and saves either the whole session, each match, or short clips around events.
No ads, accounts, telemetry, injection, memory reading or hooks.

Items marked **[verify]** come from research that could not be confirmed against a primary
source or a live run. Each is proven in the milestone named before anything is built on it.

## 1. Layout

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
- After a kill, OBS relaunches and Laqta reconnects. Whether OBS's "unclean shutdown" prompt
  can block that unattended is **[verify]**: the log shows the prompt path ran despite
  `--disable-shutdown-check`.

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
| LoL | Live Client Data API, port 2999 | `GameEnd` event not in Riot's sample file **[verify, M4]**; name format in kill events may or may not include the `#tag`; API is up only once in game. No key needed; wide distribution may fall under Riot's product registration. |
| Valorant | Vision (killfeed) | No official statement that passive screen reading is permitted — no known bans, but not confirmed safe. Game Capture may be black under Vanguard. HUD details are from memory; colour-blind modes and patches break templates. **Needs real screenshots from you.** |
| Dota 2 | GSI | Same mechanism as CS2; needs the `-gamestateintegration` launch option; Valve notes a possible framerate cost. Not in this plan's milestones. |
| Rocket League | Official Stats API (TCP 49123) | Exists since the April 2026 EAC update; payload schema undocumented and whether it works in online matches is unconfirmed. Later plugin. |
| Apex / Fortnite / OW2 / COD | Vision | Signals are mostly text, so they depend on language and need OCR. Later, using the Valorant plugin as the template. |

General: clips trimmed with stream copy can start up to ~2 s early (keyframes). An OBS crash
may show a safe-mode prompt on relaunch that blocks automatic recovery **[verify, M1]**. The
OBS WebSocket server must be enabled once by the user (it is currently off on this machine).
