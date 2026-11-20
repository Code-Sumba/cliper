"""Generic webhook: post anywhere this project does not speak natively.

Fires a JSON payload with the clip's public URL and metadata at a URL you
control - Make, n8n, Zapier, a Discord webhook, your own server. That is the
escape hatch for any platform without an official API, or one you would rather
drive through a tool you already pay for.
"""
from __future__ import annotations

from pathlib import Path

import requests

from ..config import env
from . import register
from .base import NotConfigured, PostResult, PublishError, Publisher


@register
class Webhook(Publisher):
    name = "webhook"
    label = "Webhook"
    needs_public_url = True
    max_seconds = None

    def configured(self) -> tuple[bool, str]:
        return (True, "") if env("WEBHOOK_URL") else (False, "missing WEBHOOK_URL")

    def publish(self, clip: Path, meta: dict, public_url: str | None = None) -> PostResult:
        url = env("WEBHOOK_URL")
        if not url:
            raise NotConfigured("missing WEBHOOK_URL")

        payload = {
            "clip_url": public_url,
            "filename": clip.name,
            "title": meta.get("title"),
            "caption": meta.get("caption"),
            "hashtags": meta.get("hashtags"),
            "keywords": meta.get("keywords"),
        }
        headers = {}
        if env("WEBHOOK_SECRET"):
            headers["X-Cliper-Secret"] = env("WEBHOOK_SECRET")

        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if not r.ok:
            raise PublishError(f"webhook failed: {r.status_code} {r.text[:200]}")
        return PostResult(self.name, r.headers.get("X-Post-Id", "sent"), url)
