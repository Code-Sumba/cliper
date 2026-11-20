# Vendored: stream-clips

`vendor/sc/` is the `scripts/sc` package from the **stream-clips** skill, supplied by
the user as `stream-clips.zip`. It is kept intact rather than rewritten so it can be
updated by replacing the folder.

No LICENSE file shipped with the zip, so redistribution terms are unknown. Fine for
your own private use; check with the author before publishing this repo publicly or
shipping it to anyone else.

Cliper uses these modules from it:

| module | what Cliper uses it for |
| --- | --- |
| `frame.py` | 9:16 framing: picks stack / crop / blur per clip from detected faces |
| `subs.py` | word-by-word caption rendering |
| `titlecard.py` | the title bar above the clip |
| `listen.py` | Whisper transcript + per-second volume curve |
| `discover.py` | finds streams by topic with no API key |
| `media.py` | yt-dlp wrappers (audio first, video only for clip windows) |

Cliper does NOT use `brains.py`: it picks moments by shelling out to a coding-agent CLI
(`claude -p`, `codex exec`, `gemini -p`) that the user is signed in to. That works on a
desktop and cannot work in GitHub Actions, which has no subscription auth. See
`cliper/score.py` for what Cliper does instead.
