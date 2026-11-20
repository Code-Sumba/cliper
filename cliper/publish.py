"""Entry point: push approved clips to every enabled platform.

Five gates stand between a rendered clip and a published post. Each exists
because of a specific way these accounts get killed:

1. PERMISSION  - a creator marked `none` is never auto-published. Clipping
                 without permission is ordinary copyright infringement.
2. APPROVAL    - with AUTO_PUBLISH off (the default), a human marks each clip
                 `approved`. YouTube's reused-content policy is judged on the
                 whole channel, so a bad batch costs the channel, not the clip.
3. CAMPAIGN    - where a campaign covers the footage, its rules are checked:
                 required hashtags and mentions present, banned terms absent,
                 platform allowed. Rejected here rather than by the campaign
                 after you already did the work.
4. RATE        - post_per_week per creator, so one creator cannot flood the
                 feed and trip spam detection.
5. PLATFORM    - each publisher checks its own credentials and limits.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .config import ROOT, auto_publish_enabled, env, load_creators
from .platforms import NotConfigured, PublishError, enabled_publishers
from .queue import list_items, posted_this_week, set_status
from .storage import StorageError, get_storage

MAX_YOUTUBE_PER_RUN = 5      # videos.insert is ~1600 of 10,000 daily quota units


def check_campaign(item: dict, creator) -> tuple[bool, str]:
    rules = creator.campaign or {}
    if not rules:
        return True, ""
    caption = (item.get("meta", {}) or {}).get("caption", "").lower()
    for tag in rules.get("required_hashtags", []):
        if tag.lower() not in caption:
            return False, f"missing required hashtag {tag}"
    for mention in rules.get("required_mentions", []):
        if mention.lower() not in caption:
            return False, f"missing required mention {mention}"
    for banned in rules.get("banned_terms", []):
        if banned.lower() in caption:
            return False, f"caption contains banned term '{banned}'"
    return True, ""


def platforms_for(creator) -> list[str] | None:
    """Narrowest wins: campaign rules beat creator config beats everything."""
    allowed = (creator.campaign or {}).get("allowed_platforms")
    chosen = getattr(creator, "platforms", None)
    if allowed and chosen:
        return [p for p in chosen if p.lower() in [a.lower() for a in allowed]]
    return allowed or chosen


def eligible(item: dict, creator) -> tuple[bool, str]:
    if not creator.may_auto_publish:
        return False, f"permission='{creator.permission}' - review queue only"

    status = item.get("status")
    if status == "pending" and not auto_publish_enabled():
        return False, "awaiting your approval (AUTO_PUBLISH is off)"
    if status not in {"pending", "approved"}:
        return False, f"status={status}"

    if posted_this_week(creator.handle) >= creator.post_per_week:
        return False, f"weekly cap of {creator.post_per_week} reached"

    return check_campaign(item, creator)


def main() -> int:
    by_handle = {c.handle: c for c in load_creators()}
    items = [i for i in list_items() if i.get("status") in {"pending", "approved"}]
    if not items:
        print("[publish] queue empty")
        return 0

    storage = get_storage()
    store_ok, store_why = storage.configured()
    if not store_ok:
        print(f"[publish] storage: {storage.name} not ready ({store_why}); "
              "URL-fetching platforms will be skipped")

    yt_done = 0
    for item in items:
        creator = by_handle.get(item.get("creator"))
        if not creator:
            continue

        ok, why = eligible(item, creator)
        if not ok:
            print(f"[publish] skip {item['id']}: {why}")
            continue

        clip = ROOT / item["clip_path"]
        if not clip.exists():
            print(f"[publish] skip {item['id']}: file missing ({clip})")
            continue

        publishers = enabled_publishers(limit_to=platforms_for(creator))
        if not publishers:
            print(f"[publish] skip {item['id']}: no platform configured for this creator")
            continue

        meta = item.get("meta", {})
        public_url = None
        if any(p.needs_public_url for p in publishers) and store_ok:
            try:
                public_url = storage.upload(clip)
                print(f"[publish] uploaded -> {public_url}")
            except StorageError as exc:
                print(f"[publish] upload failed for {item['id']}: {exc}")

        results = []
        for pub in publishers:
            if pub.needs_public_url and not public_url:
                print(f"[publish]   {pub.name}: skipped, needs a public URL")
                continue
            if pub.name == "youtube" and yt_done >= MAX_YOUTUBE_PER_RUN:
                print("[publish]   youtube: daily quota guard reached for this run")
                continue
            fine, reason = pub.check_clip(clip, meta)
            if not fine:
                print(f"[publish]   {pub.name}: {reason}")
                continue
            try:
                res = pub.publish(clip, meta, public_url)
                results.append(str(res))
                if pub.name == "youtube":
                    yt_done += 1
                print(f"[publish]   {res}")
            except NotConfigured as exc:
                print(f"[publish]   {pub.name}: {exc}")
            except PublishError as exc:
                print(f"[publish]   {pub.name} FAILED: {exc}")

        if results:
            set_status(item["id"], "published", note="; ".join(results))
            item["published_at"] = int(time.time())
        else:
            print(f"[publish] {item['id']}: nothing published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
