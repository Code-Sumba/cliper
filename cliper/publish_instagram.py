"""Publish a Reel through the Instagram Graph API.

Two constraints worth knowing before you build around this:

1. Instagram does NOT accept a file upload here. You give it a PUBLIC URL and
   it fetches the file itself, which is why the pipeline needs a bucket
   (Cloudflare R2's free tier is enough).
2. It needs an Instagram Business or Creator account linked to a Facebook
   Page. A personal account cannot use this API at all.

Publishing is two steps: create a media container, poll until it finishes
processing, then publish the container.
"""
from __future__ import annotations

import time

import requests

from .config import env

GRAPH = "https://graph.facebook.com/v21.0"


def create_reel_container(video_url: str, caption: str, share_to_feed: bool = True) -> str:
    r = requests.post(
        f"{GRAPH}/{env('IG_USER_ID')}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "share_to_feed": str(share_to_feed).lower(),
            "access_token": env("IG_ACCESS_TOKEN"),
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def wait_until_ready(container_id: str, timeout_seconds: int = 600) -> None:
    """Poll the container. Reels take tens of seconds to transcode."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code,status", "access_token": env("IG_ACCESS_TOKEN")},
            timeout=30,
        )
        r.raise_for_status()
        code = r.json().get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise RuntimeError(f"Instagram rejected the media: {r.json()}")
        time.sleep(10)
    raise TimeoutError(f"container {container_id} never finished processing")


def publish_container(container_id: str) -> str:
    r = requests.post(
        f"{GRAPH}/{env('IG_USER_ID')}/media_publish",
        data={"creation_id": container_id, "access_token": env("IG_ACCESS_TOKEN")},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def publish_reel(video_url: str, caption: str) -> str:
    cid = create_reel_container(video_url, caption)
    wait_until_ready(cid)
    return publish_container(cid)
