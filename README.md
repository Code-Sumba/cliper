# Cliper

Watches creators, finds the moments their chat exploded, cuts those moments into
vertical captioned clips, and posts them. Runs on GitHub Actions, so your PC can
be off.

```
watch.yml   every 2h   who went live, which VODs are new
   |
   v
harvest.yml on demand  chat download -> spike scoring -> volume re-rank
                       -> segment fetch -> captions -> 9:16 render -> queue
   |
   v
publish.yml daily      permission + approval + campaign + rate + platform gates
                       -> YouTube / Instagram / TikTok / webhook
```

Everything is already wired. Add credentials and it starts working; leave them
out and the pieces that need them stay politely switched off.

## Start here

```powershell
cd D:\cliper\cliper
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m cliper.doctor
```

Activate the venv before running the doctor: `yt-dlp` is installed into
`.venv\Scripts`, and the doctor looks for it on `PATH`.

If C: is short on space, point pip's temp files at another drive first:
`$env:TMP="D:\_piptmp"; $env:TEMP="D:\_piptmp"; $env:PIP_CACHE_DIR="D:\_piptmp\cache"`.

The doctor prints every component, whether it is ready, and exactly which
variable is missing. Nothing in it costs quota or contacts a platform. Run it
again after every credential you add.

The workflows are already in `.github/workflows/`. Create the repo:

```bash
git init && git add . && git commit -m "cliper: initial"
gh repo create cliper --private --source=. --push
```

Public repos get unlimited Actions minutes, private ones 2,000/month. Harvest
runs are long. Secrets stay secret either way.

## Credentials

All optional. `.env.example` lists every variable with a comment. In GitHub they
go in **Settings -> Secrets and variables -> Actions** (the `vars.*` ones as
*variables*, the `secrets.*` ones as *secrets*).

| What | Where | Effort |
| --- | --- | --- |
| `TWITCH_CLIENT_ID` / `_SECRET` | dev.twitch.tv/console/apps | 2 min, no review |
| `YOUTUBE_API_KEY` | console.cloud.google.com, enable YouTube Data API v3 | 5 min |
| `YOUTUBE_REFRESH_TOKEN` | `python scripts/get_youtube_token.py`, once, locally | 10 min |
| `IG_USER_ID` / `IG_ACCESS_TOKEN` | developers.facebook.com | the slow one |
| `TIKTOK_ACCESS_TOKEN` | developers.tiktok.com | drafts work without review |
| `S3_*` | Cloudflare R2 free tier, 10 GB | 15 min |

**The Instagram catch:** it needs a Business or Creator account linked to a
Facebook Page. A personal account cannot use this API at all, so if yours is
personal, converting it is step zero.

**The storage catch:** Instagram and TikTok's pull route fetch a URL - they do
not accept a file upload. No bucket means those platforms skip themselves.
YouTube takes a direct upload and needs no bucket.

## Platforms

| Platform | Status | Notes |
| --- | --- | --- |
| YouTube Shorts | ready | Direct upload. ~1600 of 10,000 daily quota units per video, so ~6/day. |
| Instagram Reels | ready | Needs a public URL and a Business/Creator account. |
| TikTok | ready | Without app review you reach drafts only - the clip lands in the app and you tap post. |
| Webhook | ready | Fires JSON at Make, n8n, Zapier, Discord, your own server. The escape hatch for anything without an API. |
| Kick | wired, inert | Kick has no documented posting API. The class is registered and reports itself unconfigured; if an endpoint appears, only `publish()` needs writing. Kick works fine as a stream *source*. |

### Adding a platform

One file in `cliper/platforms/`, about 40 lines:

```python
from . import register
from .base import Publisher, PostResult, NotConfigured

@register
class Snapchat(Publisher):
    name = "snapchat"
    label = "Snapchat Spotlight"
    needs_public_url = True

    def configured(self):
        return (True, "") if env("SNAP_TOKEN") else (False, "missing SNAP_TOKEN")

    def publish(self, clip, meta, public_url=None):
        ...
        return PostResult(self.name, post_id)
```

Nothing else changes. The doctor, the publish loop, per-creator platform lists
and campaign restrictions all pick it up from the registry.

Scope is narrowed by the strictest rule that applies: `CLIPER_PLATFORMS` in env,
then `platforms:` per creator in `creators.yml`, then a campaign's
`allowed_platforms`.

## Why it fits on free infrastructure

A 5-hour Twitch VOD is 20-40 GB. A GitHub runner has ~14 GB of free disk and a
6-hour job limit, so downloading the VOD is impossible.

Cliper never downloads one. It takes the **chat** (tens of MB), scores it to
find the twenty moments worth clipping, optionally measures the **audio** (~250
MB for 5 hours) to re-rank them, then uses `yt-dlp --download-sections` to pull
only those twenty 38-second windows. A couple of hundred megabytes, and faster
than downloading everything to throw 99% away.

The same trick makes Whisper viable: it runs over 20 short segments, not 5 hours.

## How moments are picked

`cliper/chat.py` scores each 5-second bucket of chat against a **rolling local
baseline** (median and MAD over a 10-minute window), then multiplies by how much
of the spike was reaction spam rather than ordinary conversation. Raw message
count does not work - chat rate rises with viewer count, so a naive top-20 picks
twenty moments from the single busiest hour.

| moment | messages | score | why |
| --- | --- | --- | --- |
| 10m00s, quiet stretch | 203 | **397.0** | huge relative to a quiet baseline |
| 83m20s, busy stretch | 230 | 90.3 | more messages, but chat was already loud |

`cliper/score.py` then re-ranks with the audio volume curve: a chat spike that
lines up with the streamer getting loud beats one that does not, because chat
also reacts to things that make poor clips - a donation, a raid, a joke in chat
itself.

## What came from stream-clips

`vendor/sc/` is the clip engine from the **stream-clips** skill you supplied,
kept intact so it can be updated by replacing the folder.

| from stream-clips | why it replaced Cliper's version |
| --- | --- |
| `frame.py` | Picks **stack / crop / blur** per clip from detected faces. Stack is facecam-on-top, gameplay-below - the layout clip channels use. Cliper only had blur-pad, which throws the streamer's face away. |
| `titlecard.py` | A real title bar, not a drawtext overlay. |
| `discover.py` | Finds streams by topic with **no API key**. |
| `listen.py` | Whisper transcript and the volume-curve approach. |

Both projects reached the same conclusion independently: stream-clips' `media.py`
also fetches audio first and video only for the clip windows.

**What Cliper adds:** the workflows, scheduled monitoring, the platform layer,
auto-publishing, and the permission gates. stream-clips is explicitly a desktop
tool - its SKILL.md says "you run everything" - ending at a `POSTING.md` a human
posts by hand.

**The one thing that does not port.** stream-clips picks moments with
`brains.py`, which shells out to a coding-agent CLI you are signed in to. That
cannot work in CI, which has no subscription auth. Hence `cliper/score.py`. It
finds what an AI would on the obvious moments and will miss the subtle ones - a
quiet devastating line with no volume or chat spike. That is the price of running
unattended for free.

## The permission model

Wired into the code, not left to your judgement at 2am. A creator's `permission`
is one of:

- **`campaign`** - a live clipping campaign covers these streams. Put its rules
  in `campaign:` and `publish.py` checks every clip against them.
- **`granted`** - the creator said yes. Record the evidence.
- **`none`** - no permission. Clips are still made and queued, but
  `publish.py` **refuses to auto-publish them**, ever.

Both creators in `creators.yml` are `none`.

**Copyright.** Outside a campaign, clipping a stream copies a copyrighted work.
On YouTube the usual outcome is a Content ID claim routing that clip's revenue
to the rights holder, but three manual takedowns in 90 days ends a channel.

**YouTube's reused-content policy is separate from copyright and applies even
with the creator's permission.** It is judged channel-wide: if reviewers cannot
tell the content is yours, monetization goes from the entire channel regardless
of watch hours. Since 1 October 2026 reposted Shorts also get less reach.

That is why the renderer burns captions, adds a title bar and reframes rather
than just cutting, and why `AUTO_PUBLISH` defaults to `false`.

## Daily use

```bash
gh workflow run watch.yml              # or wait for the 2-hourly cron
python -m cliper.review list           # what is queued
python -m cliper.review approve <id>   # release one
gh workflow run publish.yml
```

## Status

**Working and tested** (31 tests pass)
- Chat spike scoring with baseline adjustment
- Volume curve measured from audio, verified against a known tone and silence
- Volume re-ranking: a spike aligned with a shout overtakes a higher-scoring one
- Framing via `vendor/sc/frame.py`, face model loading confirmed, blur fallback
  so framing never hard-fails
- Title bar and word captions (checked on an extracted frame)
- Platform registry: 5 platforms, each self-reporting what it needs
- S3/R2 uploader with its own readiness check
- YouTube live-chat replay parsing, junk-line tolerant
- All five publish gates
- Doctor
- All three workflows, YAML valid and anchor-free (Actions rejects anchors)

**Not verified**
- **No end-to-end run on a real VOD.** Every part is tested in isolation and the
  credentials are not in yet. The first real run will find things.
- **Stack layout unseen on real footage.** The model loads and the detector runs,
  but a test pattern has no face, so only the blur path has been observed.
- **No platform has actually been posted to.** All five are written against their
  documented APIs; none has been exercised against a live account.

**Known gaps**
- TikTok chunked upload for files over 64 MB (a sub-60s clip is far under)
- Kick posting, pending an API that may never exist
- Thumbnails
- Spike thresholds tuned on synthetic data, not real chat

## Build estimate

**10-18 hours** remain, almost all of it credentials and first-run debugging:

| Task | Hours |
| --- | --- |
| Credentials, Instagram Business + Facebook Page being the slow part | 4-8 |
| First real end-to-end run and the debugging it surfaces | 5-8 |
| Spike threshold tuning on real chat logs | 1-2 |

Evenings plus Sundays and your 2nd/4th Saturdays: **1.5 to 2.5 weeks**. With
exams from 25 November, **early December** is realistic if you leave it alone
during them.
