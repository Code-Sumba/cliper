"""TikTok via the Content Posting API.

Worth knowing before you build on this: direct posting needs your app to pass
TikTok's review. Until it does, an unaudited app can only post to the creator's
*drafts* (SELF_ONLY / inbox), which is still useful - the clip lands in the app
and you tap post.

Two upload routes: FILE_UPLOAD (you PUT the bytes) and PULL_FROM_URL (TikTok
fetches a URL, but the domain must be verified with TikTok first). FILE_UPLOAD
is the default here because it needs no domain verification.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from ..config import env
from . import register
from .base import NotConfigured, PostResult, PublishError, Publisher

API = "https://open.tiktokapis.com/v2"


@register
class TikTok(Publisher):
    name = "tiktok"
    label = "TikTok"
    needs_public_url = False      # FILE_UPLOAD route
    max_seconds = 600

    def configured(self) -> tuple[bool, str]:
        if not env("TIKTOK_ACCESS_TOKEN"):
            return False, "missing TIKTOK_ACCESS_TOKEN"
        return True, ""

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {env('TIKTOK_ACCESS_TOKEN')}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def publish(self, clip: Path, meta: dict, public_url: str | None = None) -> PostResult:
        ok, why = self.configured()
        if not ok:
            raise NotConfigured(why)

        size = clip.stat().st_size
        # Single-chunk upload keeps this simple; TikTok allows one chunk for
        # files under 64 MB, which a sub-60s clip comfortably is.
        if size > 64 * 1024 * 1024:
            raise PublishError(f"clip is {size / 1e6:.0f} MB; chunked upload not implemented")

        direct = env("TIKTOK_DIRECT_POST", "false").lower() in {"1", "true", "yes"}
        endpoint = f"{API}/post/publish/video/init/" if direct else f"{API}/post/publish/inbox/video/init/"

        payload: dict = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            }
        }
        if direct:
            payload["post_info"] = {
                "title": meta.get("caption", meta.get("title", ""))[:2200],
                "privacy_level": env("TIKTOK_PRIVACY", "SELF_ONLY"),
                "disable_comment": False,
                "disable_duet": False,
                "disable_stitch": False,
            }

        r = requests.post(endpoint, headers=self._headers(), json=payload, timeout=60)
        if not r.ok:
            raise PublishError(f"tiktok init failed: {r.text[:300]}")
        data = r.json().get("data", {})
        upload_url, publish_id = data.get("upload_url"), data.get("publish_id")
        if not upload_url:
            raise PublishError(f"tiktok init returned no upload_url: {r.text[:300]}")

        with clip.open("rb") as fh:
            up = requests.put(
                upload_url,
                data=fh,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size),
                    "Content-Range": f"bytes 0-{size - 1}/{size}",
                },
                timeout=600,
            )
        if not up.ok:
            raise PublishError(f"tiktok upload failed: {up.status_code} {up.text[:200]}")

        where = "posted" if direct else "sent to drafts"
        return PostResult(self.name, publish_id or "unknown", f"TikTok {where}")
