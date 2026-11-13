# Laqta patch notes

The section for a version is what people see in the update window. Newest first.

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
