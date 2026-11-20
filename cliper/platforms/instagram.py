"""Instagram Reels via the Graph API.

Two hard requirements, both easy to trip over:
  - an Instagram Business or Creator account linked to a Facebook Page. A
    personal account cannot use this API at all.
  - a PUBLIC URL for the file. Instagram fetches it; it will not take an upload.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from ..config import env
from . import register
from .base import NotConfigured, PostResult, PublishError, Publisher

GRAPH = "https://graph.facebook.com/v21.0"


@register
class Instagram(Publisher):
    name = "instagram"
    label = "Instagram Reels"
    needs_public_url = True
    max_seconds = 900

    def configured(self) -> tuple[bool, str]:
        missing = [n for n in ("IG_USER_ID", "IG_ACCESS_TOKEN") if not env(n)]
        return (False, "missing " + ", ".join(missing)) if missing else (True, "")

    def publish(self, clip: Path, meta: dict, public_url: str | None = None) -> PostResult:
        ok, why = self.configured()
        if not ok:
            raise NotConfigured(why)
        if not public_url:
            raise NotConfigured("Instagram needs a public URL; configure CLIPER_STORAGE")

        token = env("IG_ACCESS_TOKEN")
        user = env("IG_USER_ID")

        r = requests.post(
            f"{GRAPH}/{user}/media",
            data={
                "media_type": "REELS",
                "video_url": public_url,
                "caption": meta.get("caption", "")[:2200],
                "share_to_feed": env("IG_SHARE_TO_FEED", "true"),
                "access_token": token,
            },
            timeout=60,
        )
        if not r.ok:
            raise PublishError(f"instagram container failed: {r.text[:300]}")
        container = r.json()["id"]

        deadline = time.time() + int(env("IG_PROCESS_TIMEOUT", "600"))
        while time.time() < deadline:
            s = requests.get(
                f"{GRAPH}/{container}",
                params={"fields": "status_code,status", "access_token": token},
                timeout=30,
            )
            s.raise_for_status()
            code = s.json().get("status_code")
            if code == "FINISHED":
                break
            if code == "ERROR":
                raise PublishError(f"instagram rejected the media: {s.json()}")
            time.sleep(10)
        else:
            raise PublishError(f"container {container} never finished processing")

        p = requests.post(
            f"{GRAPH}/{user}/media_publish",
            data={"creation_id": container, "access_token": token},
            timeout=60,
        )
        if not p.ok:
            raise PublishError(f"instagram publish failed: {p.text[:300]}")
        pid = p.json()["id"]
        return PostResult(self.name, pid, f"https://www.instagram.com/reel/{pid}/")
