# Laqta patch notes

The section for a version is what people see in the update window. Newest first.

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
