# Laqta · لقطة

A free, local-only game recorder for Windows. Laqta notices when a game starts and drives
[OBS Studio](https://obsproject.com) to record the whole session, each match, or short clips
around kills and other events.

- No ads, no account, no telemetry. Nothing leaves your PC.
- Nothing is injected into games and no game memory is read. Laqta uses OBS for capture, the
  games' own official local APIs for events, and screen analysis where no API exists.
- English and Arabic (right-to-left) interface.

![Settings, Games page](docs/screenshots/settings-games-en.png)

## What you need

- Windows 10 or 11
- OBS Studio 30.2 or newer (developed against 33.0)
- [ffmpeg](https://ffmpeg.org) on your PATH, for trimming clips, thumbnails and MP4 export
  (`winget install Gyan.FFmpeg`)
- To run from source: Python 3.11 or newer

## Set up OBS (once)

1. Start OBS.
2. Open **Tools → WebSocket Server Settings**.
3. Tick **Enable WebSocket server** and click **OK**.

That is all. Laqta reads the port and password from OBS's own settings file, creates its own
profile and scene collection named `Laqta`, and never changes your existing ones. If OBS is
closed when a game starts, Laqta launches it minimised to the tray.

## Install and run

**Packaged build:** unzip `Laqta-<version>-win64.zip` and run `Laqta.exe`. It lives in the
system tray.

**From source:**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m laqta
```

Check that Laqta can drive your OBS (records 5 seconds and saves a replay, then prints both paths):

```powershell
.venv\Scripts\python -m laqta obs-check
```

## Using it

Start a game from the list and Laqta records it. The tray icon gains a red dot while
recording. Right-click the tray icon for **Clip now**, **Clips**, **Settings** and **Quit**.

Recordings go to `Videos\Laqta\<Game>\<date>\`. Change the folder in **Settings → Recording**.

| Mode | What you get |
|---|---|
| Full session | One file from the moment the game opens until it closes. The default. |
| Hybrid | The full session, plus a `.json` next to it marking when each event happened. |
| Full match | One file per match. Needs a game with event detection; others record the session. |
| Events only | Nothing is kept except short clips around each event or hotkey press. |

The clip hotkey is **Ctrl+Alt+F9** by default. It saves a clip in Events only, and adds a mark
in Hybrid and Full match.

**Clips window:** filter by game, event and date; play, trim to a copy, export as MP4, show
in folder, or delete to the Recycle Bin.

**Settings:** language, hotkey, mode for new games, per-game mode / capture method / event
toggles, quality and frame rate, clip timing, automatic cleanup by age or folder size, and
the interface colour. Settings are stored in `%APPDATA%\Laqta\config.toml`.

### Games

Ten games are built in. **Settings → Games → Find installed games** adds what it finds in
Steam, Epic and GOG; **Add a running app…** adds anything else. When an unknown app goes
fullscreen, Laqta asks once with a toast, and clicking the toast adds it.

### Event detection

| Game | How | State |
|---|---|---|
| League of Legends | Riot's Live Client Data API | Works. Checked against a real match's event list. |
| Counter-Strike 2 | Valve Game State Integration | Written and unit-tested with hand-built payloads. Not yet run in a real match. |
| Valorant | Screen analysis | Framework only. It has no template images yet, so it detects nothing. |
| Everything else | Clip hotkey | Always available. |

The first time CS2 starts with Laqta running, Laqta writes
`gamestate_integration_laqta.cfg` into CS2's `cfg` folder and asks you to restart CS2 once.

## If the recording is black

Some anti-cheat systems block OBS's Game Capture. Laqta checks the picture a few seconds in
and falls back to Window Capture, then to capturing the whole screen, and remembers what
worked for that game. You can also force a method per game in **Settings → Games**.

## Known limits

- **Vision plugins are not confirmed safe by any publisher.** They only read the screen, and
  no bans are known, but no publisher has stated that this is permitted.
- **Desktop audio is recorded as a whole:** game, voice chat and music. Game-only audio and
  microphone recording are not implemented.
- **The primary monitor is assumed.** A game on a second monitor records at the primary's size.
- **Per-game resolution, bitrate and encoder** are not implemented; quality and frame rate
  are global.
- **Battle.net and EA libraries** are not scanned.
- **Trimming** uses start and end times, not a video scrubber, and cuts land on keyframes,
  so a trimmed copy can start a moment early.
- **OBS crash recovery:** if OBS dies mid-game, Laqta relaunches it and starts a new file. To
  do that unattended it clears OBS's "unclean shutdown" marker, which also skips OBS's
  safe-mode offer on that launch.

Per-game risks and the design are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Adding a game plugin

A plugin turns one game's signals into events. It subclasses `GamePlugin`
([laqta/core/events.py](laqta/core/events.py)):

```python
class MyGamePlugin(GamePlugin):
    name = "mygame"
    event_types = ("kill", "death", "match_start", "match_end")

    def start(self, emit, game):   # begin detecting on your own thread; must not block
        ...
    def stop(self):                # stop and release everything; safe to call twice
        ...
```

1. Put the detection logic in a pure class that takes the game's data and returns
   `GameEvent`s, so it can be tested without the game. See `Tracker` in
   [lol_live.py](laqta/plugins/lol_live.py) (polling an API),
   [cs2_gsi.py](laqta/plugins/cs2_gsi.py) (receiving posts) and
   [valorant_vision.py](laqta/plugins/valorant_vision.py) (screen regions).
2. Register the class in [laqta/plugins/\_\_init\_\_.py](laqta/plugins/__init__.py).
3. Add `"plugin": "mygame"` to the game's entry in
   [games.json](laqta/game_detector/games.json).
4. Add English and Arabic names for any new event types in [i18n.py](laqta/i18n.py).
5. Write tests from captured real data, not from memory of the format.

Rules for every plugin: no injection, no memory reading, no handles into the game process.
Official local APIs first, then screen or audio analysis.

A step-by-step guide for AI coding assistants is in
[skills/add-game-plugin](skills/add-game-plugin/SKILL.md).

## Development

```powershell
.venv\Scripts\python -m pytest                 # tests; OBS and games are faked
powershell -File packaging\build.ps1           # builds dist\Laqta\Laqta.exe and a zip
.venv\Scripts\python -m laqta vision-test valorant "recording.mkv"   # run a vision plugin over a video
```

Set `LAQTA_GSI_DUMP` or `LAQTA_LOL_DUMP` to a file path before starting Laqta to save the raw
data CS2 or League sends, for building test fixtures.

## بالعربية

**لقطة** مسجّل ألعاب مجاني يعمل محليًا على ويندوز. يكتشف بدء اللعبة ويتحكم في OBS Studio
لتسجيل الجلسة كاملة، أو كل مباراة، أو لقطات قصيرة حول الأحداث. بلا إعلانات ولا حساب ولا
إرسال بيانات، ولا يحقن شيئًا في الألعاب ولا يقرأ ذاكرتها.

1. شغّل OBS وافتح **Tools ← WebSocket Server Settings** وفعّل **Enable WebSocket server**.
2. شغّل لقطة؛ ستظهر أيقونتها بجانب الساعة.
3. شغّل لعبتك. تُحفظ التسجيلات في `Videos\Laqta`.

لتغيير اللغة: انقر بالزر الأيمن على الأيقونة ← **Settings** ← **Language** ← العربية.
اختصار اللقطة الافتراضي هو **Ctrl+Alt+F9**.
