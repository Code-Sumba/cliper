"""Facebook Reels on a Page, via the Graph API's three-step Reels upload.

Uses the same never-expiring Page token as Instagram (the Page that the
Instagram account is linked to), so there is nothing new to set up:

    FB_PAGE_ID     the Page's id (Graph API Explorer: GET me?fields=id with the Page token)
    FB_PAGE_TOKEN  optional - defaults to IG_ACCESS_TOKEN, which is that Page's token

The file goes up directly (rupload.facebook.com), so unlike Instagram no
storage bucket is needed. Reels must be 3-90 s, vertical.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from ..config import env
from . import register
from .base import NotConfigured, PostResult, PublishError, Publisher

GRAPH = "https://graph.facebook.com/v21.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v21.0"


def _token() -> str:
    return env("FB_PAGE_TOKEN") or env("IG_ACCESS_TOKEN")


@register
class Facebook(Publisher):
    name = "facebook"
    label = "Facebook Reels"
    needs_public_url = False
    max_seconds = 90

    def configured(self) -> tuple[bool, str]:
        missing = [n for n, v in (("FB_PAGE_ID", env("FB_PAGE_ID")),
                                  ("FB_PAGE_TOKEN or IG_ACCESS_TOKEN", _token())) if not v]
        return (False, "missing " + ", ".join(missing)) if missing else (True, "")

    def publish(self, clip: Path, meta: dict, public_url: str | None = None) -> PostResult:
        ok, why = self.configured()
        if not ok:
            raise NotConfigured(why)
        page, token = env("FB_PAGE_ID"), _token()

        # 1. start: reserve a video id
        r = requests.post(f"{GRAPH}/{page}/video_reels",
                          data={"upload_phase": "start", "access_token": token}, timeout=60)
        if not r.ok:
            raise PublishError(f"facebook start failed: {r.text[:300]}")
        video_id = r.json().get("video_id")
        if not video_id:
            raise PublishError(f"facebook start returned no video_id: {r.text[:200]}")

        # 2. upload the bytes
        size = clip.stat().st_size
        with clip.open("rb") as fh:
            u = requests.post(f"{RUPLOAD}/{video_id}", data=fh, timeout=600, headers={
                "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
        if not u.ok or not u.json().get("success", False):
            raise PublishError(f"facebook upload failed: {u.text[:300]}")

        # 3. finish: publish with the caption
        description = meta.get("caption") or meta.get("description") or meta.get("title", "")
        f = requests.post(f"{GRAPH}/{page}/video_reels", timeout=60, data={
            "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
            "description": description[:2200], "access_token": token})
        if not f.ok or not f.json().get("success", False):
            raise PublishError(f"facebook finish failed: {f.text[:300]}")

        # Processing is async; wait briefly so an outright rejection is reported.
        deadline = time.time() + int(env("FB_PROCESS_TIMEOUT", "180"))
        while time.time() < deadline:
            s = requests.get(f"{GRAPH}/{video_id}", params={"fields": "status", "access_token": token},
                             timeout=30)
            st = (s.json().get("status") or {}) if s.ok else {}
            if st.get("video_status") == "error":
                raise PublishError(f"facebook rejected the reel: {st}")
            if st.get("video_status") == "ready" or \
               (st.get("publishing_phase") or {}).get("status") == "complete":
                break
            time.sleep(10)
        return PostResult(self.name, str(video_id), f"https://www.facebook.com/reel/{video_id}")
