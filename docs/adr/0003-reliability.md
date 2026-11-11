# ADR 0003: Disk guard, start with Windows, health checks, diagnostic file

Date: 2026-11-11. Status: accepted.

## Decisions

**The disk guard stops recording; it never deletes.** Below `min_free_gb` (default 5) the
recorder refuses to start, and a running recording is stopped and saved at the next 5-second
look. It tries again every 30 seconds and says so once, not every time.
*Rejected: deleting old recordings to make room.* Cleanup already has its own explicit
settings; a guard that deletes would surprise the person whose clips they are.
The check sits in the recorder (pure, with an injected free-space function) so it is covered
by the same fake-clock tests as the rest of the state machine. A recording that survived an
OBS reconnect is adopted first and stopped by the next look, so OBS is never left recording
a file nobody owns.

**The registry is the only record of "start with Windows".** One value in the user's own Run
key (`HKCU`, no administrator rights, nothing machine-wide). There is no copy in
`config.toml`, because two records can disagree. Windows' `StartupApproved` value is read, so
an entry switched off in Task Manager shows as off, with a note; ticking the box in Laqta
removes that block, since the tick is the user asking for it.
*Rejected: a Startup-folder shortcut* (needs COM to create) *and a scheduled task* (needs
elevation for some triggers, and is harder for the user to find and remove).

**Health checks are a pure function over gathered facts.** `gather()` touches the machine
(registry, a test write, `which`), `checks()` grades the facts and picks the sentence. The
window, the `diagnose` command and the zip all use the same list. The checks do not connect
to OBS or the network, so opening the page is instant and can never disturb a recording.
One check has a fix button: a remembered software encoder is never retried on its own, so
"Try the graphics card again" forgets it.

**The diagnostic zip is built by allow-list and then scrubbed.** Only named files go in (log,
`obs.json`, `capture.json`, redacted settings, health results, version lines). Settings whose
key contains `webhook`, `password`, `token` or `secret` are replaced; the OBS WebSocket
password, the CS2 token and any Discord webhook address are also wiped from the log text; the
user's home folder becomes `%USERPROFILE%`. The file is written by the user to a place they
choose and is never sent anywhere.

## Consequences

- Game names and window titles stay in the log: they are what a bug report is about. The
  Health page says so.
- A very short secret (a one-letter OBS password) would be replaced everywhere it occurs in
  the log, making the log harder to read. Safe, and rare.
- The guard measures the drive, not the folder: a quota on the folder is not seen.
