# ADR 0004: Sharing

Date: 2026-11-11. Status: accepted.

## Context

Laqta's promise is that nothing leaves the PC. Sharing a clip is the one thing users want
that breaks it, so it has to be impossible by accident.

## Decisions

**Post to Discord is a webhook upload, one clip per explicit click.** The button does not
exist until the user pastes a webhook address; each press asks "send this clip?" first.
*Rejected: posting automatically when a clip is saved* (data leaving the PC without a
decision each time) *and a Discord bot or OAuth login* (an account, tokens to refresh, and a
server to trust).

**Only Discord webhook addresses are accepted**, by a full-match pattern checked in settings,
again when the settings file is loaded, and again in `share.request` before any byte is
sent. A typo or a pasted link to somewhere else therefore cannot become an upload target.

**The upload uses the standard library** (`urllib`, a hand-built multipart body).
*Rejected: adding `requests`*, a new dependency for one POST.

**What is sent:** the clip as MP4 (an MKV is remuxed first, since Discord plays MP4 in place),
the game's name as the message, `allowed_mentions` empty so no text can ping a channel, and
the user agent `Laqta`. Nothing about the PC or the user. Files over 10 MB are refused before
asking, with a pointer to the Discord export preset, instead of being shrunk silently.

**Errors never repeat the address.** HTTP errors are turned into plain sentences and the
original exception is dropped (`from None`), because its text and traceback contain the URL,
which would otherwise reach the log.

**Copy to clipboard copies the file, not its path or pixels**, the same data Explorer's Copy
produces, so it pastes into chat apps and folders alike.

**The corner note is an ordinary top-level window**: frameless, always on top, no focus,
transparent to input. *Rejected: anything drawn inside the game* (injection, which the
project forbids). The cost is that it cannot appear over exclusive fullscreen.

## Consequences

- The webhook address sits in `config.toml` in plain text. Anyone who can read that file can
  post to the channel. The diagnostic zip removes it; the file itself is not protected.
- The upload runs on the window's thread, so the Clips window is busy for the few seconds it
  takes (at most 60).
- The corner note uses the primary screen, not the game's.
