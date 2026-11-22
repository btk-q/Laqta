# Laqta patch notes

The section for a version is what people see in the update window. Newest first.

## Unreleased

### New
- **Find installed games** now also finds Battle.net and EA games (Overwatch, Diablo, Call of
  Duty, Apex Legends, EA FC and others), so you can add them with one click.

## 0.9.0

### New
- **Pause a recording** with Ctrl+Alt+F12 (or from the tray menu) during a queue, a loading
  screen or a break, and press it again to carry on. The paused time is left out of the video,
  and marks still point at the right moments.
- **Save a video without a part of it.** In the trim window, put the handles around a loading
  screen or a long wait and press "Save without this part". You get a copy with that part
  taken out; the original is kept.

## 0.8.1

### New
- **Sort your videos** newest first, oldest first, biggest first or longest first (the menu at
  the end of the filter row). Sorting by size makes it easy to see what to delete.

### Fixed
- Video pictures no longer disappear from the clips window after you favourite, delete or
  sort videos.

## 0.8.0

### New
- **The player got faster to use.** Choose the playback speed, step one frame at a time with
  `,` and `.`, and press **Mark this moment** (or M) to add your own mark while watching; it
  shows on the seek bar and in the trim window. The volume you set is remembered.
- **League cards name your champion**, and the side panel shows the champion and game mode
  (Summoner's Rift, ARAM, Arena and so on) for matches recorded from now on.
- **A clear message when something goes wrong**, with a "Save diagnostic file" button,
  instead of a box full of code.

## 0.7.0

### New
- **Start and stop a recording yourself** with Ctrl+Alt+F11 (or from the tray menu): for a
  game Laqta doesn't know, or anything else on screen. Press it again to stop.
- **Select several videos** with Ctrl-click or Shift-click, then favourite, export or delete
  them together, or make a highlight reel from just those.
- **Storage page** in Settings: how much space each game's videos take, how much is free, and
  the cleanup and folder settings in one place.

### Fixed
- Arabic: game names and lines that begin with a number now line up from the right, and
  choice tiles start on the right.

## 0.6.1

**Fixes from a real evening of play.**

### Fixed
- **No more clips of you just talking.** Loudness alone used to decide what a "shout" was,
  and with a loud microphone it saved a clip every few seconds. Now the sound recogniser
  listens to every such clip and keeps it only if a laugh or a shout is really in it.
- **One switch instead of three.** "Find my laughs and shouts" (Settings, Audio) now covers
  everything; if you had the old shout or laugh switch on, it is turned on for you.
- **Laqta no longer ends up recording your whole screen.** It waits longer before giving up
  on a game's own picture, tells you if it does fall back to the whole screen, and tries the
  game's own window again next time.
- **Clips window is fast with hundreds of videos.** Pictures are made for what is on screen
  first.

## 0.6.0

**One window, and every game has its picture.**

### New
- **Clips and Settings in one window.** Switch between them at the top. Settings you are in
  the middle of changing wait for you while you look at your clips.
- **Game icons.** Each game shows its own icon in the clips headings and in "My games".
- **Riot games in "Add a game".** Games installed through the Riot Client (League, 2XKO,
  Legends of Runeterra and others) are now found, alongside Steam, Epic and GOG.

## 0.5.1

**Easier to set up, and updates that cannot leave you with a broken Laqta.**

### New
- **Setup screen.** On a PC that is missing OBS, its connection to Laqta, or ffmpeg, Laqta
  now shows three steps with a button for each: it can install OBS and ffmpeg for you and
  switch on the OBS connection. Also under Settings, Health, "Open setup".
- **Clips of your best moments.** After a game, Laqta also saves a short clip around each ace
  and multi-kill. Choose which events in Settings, Recording, "Also save a clip of each".
- **Safer updates.** If a new version does not start, Laqta puts the previous one back by
  itself within a minute.

### Fixed
- ffmpeg installed while Laqta is running is now found without restarting Laqta.
- The video player is closed properly when Laqta quits.

## 0.5.0

**Watch and cut your videos without leaving Laqta, and Laqta now knows a laugh from a shout.**

### New
- **Video player.** Play opens your video inside Laqta, with each event's symbol on the seek
  bar: click one to jump to just before it. Space plays and pauses, the arrow keys jump five
  seconds, F goes full screen. Choose to hear everything, only the game, or only your
  microphone.
- **The trim window plays video.** The picture above the filmstrip is now the real video;
  "Play selection" plays exactly the part you have chosen.
- **Laughs and shouts are told apart.** Turn on "Find my laughs and shouts" in Settings, Audio
  (the microphone must be on). When the game closes, a sound recogniser on your PC listens to
  your microphone track and marks each laugh (pink) and each shout or scream (blue-green).
  Talking is ignored, even loud. Each laugh still gets its own "Funny" clip.
- **Health** now checks the video player too.

### Changed
- The old laugh guess (from loudness alone) is gone; the new recogniser replaces it.
- The download is about 23 MB bigger, for the player and the recogniser.

## 0.4.0

**A new look, and Laqta now shows you what happened in every video.**

### New
- **Redesigned main window.** A header shows what Laqta is doing right now (waiting,
  recording, or a problem). Your videos are grouped by game and day, each with its length on
  the picture and small tags for what happened. Select one to get a bigger preview and all
  its actions in a side panel.
- **A symbol for every event.** Kills, deaths, assists, objectives, goals, wins and more each
  have their own coloured symbol: on the trim timeline (with a legend), on the cards, and on
  a strip that shows where in the video each one happened. Click a symbol on the strip to
  jump straight to that moment.
- **Funny moments (optional).** Turn on "Catch funny moments when I laugh" in Settings, Audio.
  Laqta marks each laugh and, when the game closes, also saves the minutes around it as its
  own clip. It listens for the rhythm of laughing in your microphone's loudness, so it is
  approximate: loud, fast talking can set it off.
- **Add a game by searching.** Settings, "Add a game" lists the games installed on your PC
  (Steam, Epic, GOG) and the programs running now. Type to find one.
- **Deadlock** is in the built-in list (recording and hotkeys; no automatic events yet).
- **Video quality in one click:** Light, Balanced, High or Custom.
- **Settings sidebar:** your games are listed under the settings pages; click one to open it.
  Recording mode and events are now tiles you click.
- **The window can be maximised**, and the footer shows free disk space.

### Fixed
- Thumbnails no longer show a black loading screen.
- The update download (about 94 MB) is now removed after installing.
- A summary line of "Kills 0, deaths 0, assists 0" is no longer shown.

## 0.3.1

### Fixed
- The command-line updater (`Laqta.exe update`) stopped with an error when the patch notes
  contained a character the console could not show. The update window was not affected.

## 0.3.0

**Laqta now updates itself.** When a new version is out, Laqta shows these notes at start and
installs it with one click. Your settings and recordings are never touched.

### New
- **A real window.** Starting Laqta opens your clips, with a Settings button. Closing the
  window leaves Laqta running next to the clock, ready to record; start it again (or click
  its icon) to bring the window back.
- **Events are marked in every recording.** In the default mode, kills, objectives, goals and
  hotkey presses now show up as markers you can jump to and cut around.
- **Rocket League:** goals, assists, saves, demolitions and the result (new, still being tested).
- **Highlight reel:** joins the best moments of what you are looking at into one video.
- **Trim and export:** a filmstrip with handles, plus one-click vertical, Discord-size and
  1080p exports.
- **Sharing (all optional):** copy a clip to the clipboard, post one to your Discord channel,
  and a small "clip saved" note in the corner of the screen.
- **Health page** in Settings that checks OBS, ffmpeg, disk space and hotkeys and tells you
  what to fix.
- **Disk guard:** recording stops and saves before the disk fills up.
- **Start with Windows** (Settings → General).

### Fixed
- Recordings in the default mode kept no event marks.
- Recordings could be shifted sideways or silent on some setups.

### You need
- [OBS Studio](https://obsproject.com) with its WebSocket server turned on
  (Tools → WebSocket Server Settings).
- [ffmpeg](https://ffmpeg.org) on the PATH, for thumbnails, trimming and exports.
