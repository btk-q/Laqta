---
name: add-game-plugin
description: Add event detection for a new game to Laqta (the local OBS-based game recorder), or fix or calibrate an existing game plugin. Use this whenever the user wants Laqta to detect kills, deaths, wins, match start/end or any other in-game event for a game, mentions writing or debugging a GamePlugin, a Tracker, GSI, a live-client API, killfeed or HUD template matching, or says a game's clips or match recordings are not triggering, even if they do not say the word "plugin".
---

# Adding a game plugin to Laqta

A plugin turns one game's signals into `GameEvent`s. The recorder does everything else:
clips, marks, match boundaries, toggles, de-duplication. A new plugin is usually one file
of 100–200 lines plus a test file.

Read these first; they are short and are the patterns to copy:

- `laqta/core/events.py` — `GameEvent` and the `GamePlugin` interface
- `laqta/plugins/lol_live.py` — polling a local API
- `laqta/plugins/cs2_gsi.py` — receiving data the game posts to us, plus installing a config file
- `laqta/plugins/vision.py` and `valorant_vision.py` — reading regions of the screen

## The rule that is not negotiable

No injection, no reading game memory, no handles into the game process, no hooks. Anti-cheat
systems (VAC, EAC, BattlEye, Vanguard) ban for these, and a ban lands on the user, not on us.
The only allowed sources are, in order of preference:

1. An official local API or data feed the game itself provides
2. The screen (small regions, a few samples per second)
3. Audio, only to raise confidence in something else
4. The manual hotkey, which already works for every game and needs no plugin

If the only way to get a signal breaks this rule, say so and stop. Do not look for a clever
workaround.

## Step 1: find out what the game really offers

Do not design from memory of how an API "should" look. Field names and behaviour drift, and
two of the three existing plugins had details that only real data settled. Look up the
current official documentation, then get real data before writing detection logic:

- API or feed: add a dump switch like the existing ones (`LAQTA_LOL_DUMP`, `LAQTA_GSI_DUMP`
  write the raw data to a file) and ask the user to play one match with it set.
- Screen: ask the user for one recorded match (Laqta records it in session mode), then pull
  frames out of the video with ffmpeg.

Things to establish, because each has bitten an existing plugin:

- How do you tell the **local player's** events from everyone else's? (CS2 swaps `player` to
  whoever you spectate after death; League reports names without the `#tag`.)
- What marks a match starting and ending, and what happens if the game process dies first?
- What does the data look like when you join late or reconnect? Old events must not replay
  as new clips.

## Step 2: write the logic as a pure Tracker

Put detection in a class with no threads, sockets or screen access: data in, list of
`GameEvent` out. This is what makes a plugin testable without the game, and it is where
all the real bugs live.

```python
class Tracker:
    def update(self, data, now: float) -> list[GameEvent]: ...
```

Conventions the recorder relies on:

- Emit `match_start` and `match_end` as ordinary events. Full Match mode records between them.
- Compare against your own remembered state, not against any "previous" block the game
  sends; those can be coalesced or missing.
- A counter going down (new round) is a reset, not an event.
- Use existing event type names where they fit (`kill`, `death`, `assist`, `multikill`, `ace`,
  `headshot`, `objective`, `win`, `lose`, `round_win`, `round_lose`, `first_blood`).
- Set `confidence` below 1.0 for vision or audio, and `source` accordingly. The recorder
  drops events under its confidence threshold.
- Do not de-duplicate rapid repeats yourself; the recorder already merges same-type events
  within one second.

## Step 3: wrap it in a GamePlugin

`start(emit, game)` must return immediately: begin work on your own daemon thread and call
`emit(event)` from it. `stop()` must release everything (ports, threads) and be safe to call
twice. `setup(game)` is optional, for a one-time install such as a config file the game
reads; return a short message if the user has to do something (for example restart the game).

If you open a local listener, bind `127.0.0.1` only and require a secret, as `cs2_gsi.py` does.
For screen plugins, subclass `VisionPlugin` and return `Signal`s; do not write a capture loop.

## Step 4: register it

1. `laqta/plugins/__init__.py` — add the class to `PLUGINS`.
2. `laqta/game_detector/games.json` — add `"plugin": "<name>"` to the game's entry (add the
   entry, with the exact exe name, if the game is new).
3. `laqta/i18n.py` — add English and Arabic names for any new event types, as
   `"event.<type>"`. A test fails if a language is missing.

Nothing else needs to change: the settings window lists the plugin's `event_types` as
toggles automatically.

## Step 5: test from real data

Write tests that feed the Tracker sequences and assert the events. Anonymise player names
before committing a real capture (see `tests/test_lol_real.py` and its fixture). Cover at
least: a normal kill and death, an event belonging to another player, joining late, the
game ending normally, and the game process vanishing mid-match.

For vision plugins, calibrate and measure with the offline runner before trusting it live:

```
python -m laqta vision-test <plugin> "<recording.mkv>"
```

Report how many real events it caught and how many false ones it raised.

## Step 6: say what is and is not verified

End with a plain statement of what was checked against real data and what was only checked
against data you built by hand. Add the game to the event-detection table in `README.md`
and to the per-game risks in `docs/ARCHITECTURE.md`, including any anti-cheat uncertainty.
