# ADR 0005: A main window, and updates from GitHub releases

Date: 2026-11-11. Status: accepted. Requested by the owner: "an exe I can open and send to
friends, and when I change it the update is required for all of them, with patch notes".

## This reverses an earlier rule

The v2 brief said any feature that goes online must be opt-in and off by default, and named
the update check. The owner has now asked for updates that reach every friend's copy, which
cannot work if the check is off by default. So the update check is **on by default**. It is
the only thing Laqta does online without a click, it is described in Settings, and one tick
box turns it off.

## Decisions

**Releases live on GitHub; the app asks `releases/latest`.** One anonymous HTTPS GET at start,
with the user agent `Laqta` and no identifiers. *Rejected: a server of our own* (cost, and a
second thing to trust) *and telemetry-style "phone home" with a version or machine id.*

**"Required" means: update or quit, but never locked out.** The window offers "Update now"
and "Quit Laqta". If the download or install fails, "Continue without updating" appears.
Being offline, GitHub being down or an odd answer all count as "no update" and Laqta starts
normally. The window never interrupts a game: a found update waits until nothing is recording.

**A download is installed only if its SHA-256 matches** the digest GitHub lists for the asset
(or the `SHA256:` line the release script writes into the notes). Only assets named
`Laqta-<version>-win64.zip` under this repository's own release-download address are
considered. This protects against a corrupted or swapped download. It does not protect
against someone who controls the GitHub account: they can publish a release, and every copy
will install it. The executable is not code-signed.

**The swap is a small batch file**, because a running exe cannot replace itself: wait for
Laqta to exit, delete the app's own `_internal` folder, copy the new files over, start
Laqta. It never mirrors or purges the folder Laqta.exe sits in, since a friend may have
unzipped it straight onto the Desktop. Settings and recordings live elsewhere and are not
touched. *Rejected: an installer and an updater service* (far more to build and to trust).

**The app opens a window.** Starting Laqta opens the clips window with a Settings button;
closing it keeps Laqta in the tray; starting Laqta again signals the running copy (a named
Windows event) to show the window. `--tray` starts silently and is what "Start with
Windows" uses.

## Consequences

- Publishing is one script (`packaging/release.ps1`): tests, build, release with the
  version's section of `CHANGELOG.md` as the patch notes.
- Friends must keep Laqta in a folder they can write to, or the update says so and stops.
- Copies running from source are told to use git.
