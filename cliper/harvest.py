"""Entry point: VOD -> chat spikes -> fetched segments -> rendered clips.

Never downloads a whole VOD. See fetch.py for why that matters.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import ytchat
from .audio import curve_for_stream
from .chat import download_twitch_chat, find_candidates, load_twitchdownloader_json
from .config import CLIPS_DIR, STATE_DIR, WORK_DIR, ensure_dirs, env, load_creators
from .fetch import check_tools, fetch_candidates
from .live import StreamRef, is_done, load_seen, mark_seen
from .meta import build_meta
from .queue import add as queue_add
from .render import build_ass, render_framed, transcribe
from .score import blend

PENDING_FILE = STATE_DIR / "pending_streams.json"
MAX_STREAMS_PER_RUN = 6       # the time budget below is the real limit
USE_VOLUME = env("CLIPER_VOLUME", "true").lower() in {"1", "true", "yes"}

# GitHub kills the job at 330 min (timeout-minutes). Leave room to upload the
# clips and commit the queue, or the whole run's work is thrown away.
START = time.time()
NO_NEW_STREAM_AFTER = 150 * 60    # don't start another stream after 2.5 h
STOP_RENDERING_AFTER = 290 * 60   # finish up, keep what is done


def _elapsed() -> str:
    m = int((time.time() - START) // 60)
    return f"{m // 60}h{m % 60:02d}m"


def clips_per_stream() -> int:
    try:
        return max(1, int(env("CLIPS_PER_STREAM", "8")))
    except ValueError:
        return 8


def harvest_stream(ref: StreamRef, creator) -> int:
    print(f"[harvest] {_elapsed()} {ref.creator} {ref.platform}:{ref.vod_id}")

    if ref.platform == "twitch":
        chat_path = WORK_DIR / f"{ref.vod_id}_chat.json"
        download_twitch_chat(ref.vod_id, chat_path)
        messages = load_twitchdownloader_json(chat_path)
    elif ref.platform == "youtube":
        messages = ytchat.messages_for(ref.url, WORK_DIR, ref.vod_id)
        if not messages:
            print("[harvest] no chat replay on this video, nothing to score")
            return 0
    else:
        print(f"[harvest] no chat source for {ref.platform} yet, skipping")
        return 0

    cands = find_candidates(
        messages,
        clip_seconds=creator.clip_seconds,
        pre_roll=creator.pre_roll,
        max_candidates=creator.max_candidates,
    )
    print(f"[harvest] {_elapsed()} {len(messages)} chat messages -> {len(cands)} candidates")
    if not cands:
        return 0

    # Re-rank with the audio volume curve: a chat spike that lines up with the
    # streamer getting loud beats one that does not. Audio-only download, so a
    # 5-hour stream costs ~250 MB rather than tens of gigabytes.
    curve = curve_for_stream(ref.url, WORK_DIR, ref.vod_id) if USE_VOLUME else []
    before = [c.offset_seconds for c in cands[:3]]
    cands = blend(cands, volume_curve=curve)[:clips_per_stream()]
    print(f"[harvest] {_elapsed()} rendering the best {len(cands)}")
    if curve and [c.offset_seconds for c in cands[:3]] != before:
        print(f"[harvest] volume re-ranked the top picks: {before} -> "
              f"{[c.offset_seconds for c in cands[:3]]}")

    made = failed = 0
    cam_memory: dict = {}   # frame.plan reuses a stream's facecam box across clips
    for cand, raw in fetch_candidates(ref.url, cands, WORK_DIR, ref.vod_id):
        if time.time() - START > STOP_RENDERING_AFTER:
            print(f"[harvest] {_elapsed()} time budget reached, keeping {made} clip(s)")
            raw.unlink(missing_ok=True)
            break
        try:
            # Transcribe first: the title and caption are written from what
            # was actually said, not from a template.
            words = transcribe(raw)
            subs = build_ass(words, WORK_DIR / f"{raw.stem}.ass")
            meta = build_meta(
                creator.display_name, cand, ref.title,
                campaign_hashtags=(creator.campaign or {}).get("required_hashtags"),
                transcript=" ".join(w["text"] for w in words),
                source_platform=ref.platform,
                pre_roll=creator.pre_roll,
                game=ref.game,
            )
            final = CLIPS_DIR / f"{ref.vod_id}_{cand.offset_seconds}.mp4"
            _, look = render_framed(
                raw, final, subtitles=subs, title=meta.hook, memory=cam_memory,
            )
            queue_add({
                "creator": creator.handle,
                "permission": creator.permission,
                "vod_id": ref.vod_id,
                "platform": ref.platform,
                "source_url": ref.url,
                "clip_path": str(final.relative_to(CLIPS_DIR.parent)),
                "offset_seconds": cand.offset_seconds,
                "score": cand.score,
                "chat_sample": cand.sample,
                "look": look,
                # Lets the website link straight to the run holding the video.
                "run_id": env("GITHUB_RUN_ID"),
                "meta": meta.as_dict(),
                "transcript": " ".join(w["text"] for w in words)[:2000],
                "stream_title": ref.title,
                "game": ref.game,
            })
            made += 1
            print(f"[harvest]   clip at {cand.offset_seconds}s score={cand.score} look={look}")
        except Exception as exc:
            print(f"[harvest]   failed at {cand.offset_seconds}s: {type(exc).__name__}: {exc}")
            failed += 1
        finally:
            raw.unlink(missing_ok=True)   # free runner disk immediately
    if made == 0 and failed:
        # Every clip broke: a bug or a tool problem, not a dull stream. Mark
        # the stream failed (retried later) instead of done.
        raise RuntimeError(f"all {failed} clips failed")
    return made


def main() -> int:
    ensure_dirs()
    tools = check_tools()
    if not all(tools.values()):
        print(f"[harvest] missing tools: {tools}", file=sys.stderr)
        return 1
    if not PENDING_FILE.exists():
        print("[harvest] nothing pending")
        return 0

    by_handle = {c.handle: c for c in load_creators()}
    fields = StreamRef.__dataclass_fields__
    pending = [StreamRef(**{k: v for k, v in d.items() if k in fields})
               for d in json.loads(PENDING_FILE.read_text(encoding="utf-8"))]
    # A watch run that fires while an earlier harvest is still working lists
    # the same streams again (they are only marked seen when it finishes).
    # This run starts from the latest commit, so skip anything already done.
    seen = load_seen()
    pending = [r for r in pending if not is_done(seen.get(r.key()))]
    # Twitch first: its chat comes from the API and never hits YouTube's bot
    # check, so a blocked YouTube stream cannot eat the run.
    pending.sort(key=lambda r: r.platform != "twitch")
    total = 0
    for ref in pending[:MAX_STREAMS_PER_RUN]:
        if time.time() - START > NO_NEW_STREAM_AFTER:
            print(f"[harvest] {_elapsed()} not starting {ref.key()}: next run will")
            break
        creator = by_handle.get(ref.creator)
        if not creator:
            continue
        try:
            total += harvest_stream(ref, creator)
            mark_seen(ref, "harvested")
        except Exception as exc:
            print(f"[harvest] {ref.key()} failed: {type(exc).__name__}: {exc}")
            mark_seen(ref, "failed")
    print(f"[harvest] {total} clip(s) queued for review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
