"""Entry point: VOD -> chat spikes -> fetched segments -> rendered clips.

Never downloads a whole VOD. See fetch.py for why that matters.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import ytchat
from .audio import curve_for_stream
from .chat import download_twitch_chat, find_candidates, load_twitchdownloader_json
from .config import CLIPS_DIR, STATE_DIR, WORK_DIR, ensure_dirs, env, load_creators
from .fetch import check_tools, fetch_candidates
from .live import StreamRef, load_seen, mark_seen
from .meta import build_meta
from .queue import add as queue_add
from .render import build_ass, render_framed, transcribe
from .score import blend

PENDING_FILE = STATE_DIR / "pending_streams.json"
MAX_STREAMS_PER_RUN = 2       # keep inside the runner's 6-hour job limit
USE_VOLUME = env("CLIPER_VOLUME", "true").lower() in {"1", "true", "yes"}


def harvest_stream(ref: StreamRef, creator) -> int:
    print(f"[harvest] {ref.creator} {ref.platform}:{ref.vod_id}")

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
    print(f"[harvest] {len(messages)} chat messages -> {len(cands)} candidates")
    if not cands:
        return 0

    # Re-rank with the audio volume curve: a chat spike that lines up with the
    # streamer getting loud beats one that does not. Audio-only download, so a
    # 5-hour stream costs ~250 MB rather than tens of gigabytes.
    curve = curve_for_stream(ref.url, WORK_DIR, ref.vod_id) if USE_VOLUME else []
    before = [c.offset_seconds for c in cands[:3]]
    cands = blend(cands, volume_curve=curve)
    if curve and [c.offset_seconds for c in cands[:3]] != before:
        print(f"[harvest] volume re-ranked the top picks: {before} -> "
              f"{[c.offset_seconds for c in cands[:3]]}")

    made = 0
    cam_memory: dict = {}   # frame.plan reuses a stream's facecam box across clips
    for cand, raw in fetch_candidates(ref.url, cands, WORK_DIR, ref.vod_id):
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
            })
            made += 1
            print(f"[harvest]   clip at {cand.offset_seconds}s score={cand.score} look={look}")
        except Exception as exc:
            print(f"[harvest]   failed at {cand.offset_seconds}s: {type(exc).__name__}: {exc}")
        finally:
            raw.unlink(missing_ok=True)   # free runner disk immediately
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
    pending = [StreamRef(**d) for d in json.loads(PENDING_FILE.read_text(encoding="utf-8"))]
    # A watch run that fires while an earlier harvest is still working lists
    # the same streams again (they are only marked seen when it finishes).
    # This run starts from the latest commit, so skip anything already done.
    seen = load_seen()
    pending = [r for r in pending if r.key() not in seen]
    total = 0
    for ref in pending[:MAX_STREAMS_PER_RUN]:
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
