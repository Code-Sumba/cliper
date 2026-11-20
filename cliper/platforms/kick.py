"""Kick.

Kick is a place clips COME FROM, not somewhere you post short video: it has no
documented public endpoint for uploading a clip to a channel the way YouTube
and TikTok do. Its public API, where available, covers channel and chat data.

This class is deliberately left as a wired-up stub rather than deleted:
everything around it - registration, config, the doctor, per-creator platform
lists - already works, so if Kick ships a posting endpoint the only thing to
write is the body of publish(). It reports itself as unconfigured, so the
pipeline simply skips it.

For INGESTING Kick streams (which is the useful direction), see cliper/live.py.
"""
from __future__ import annotations

from pathlib import Path

from ..config import env
from . import register
from .base import NotConfigured, PostResult, Publisher


@register
class Kick(Publisher):
    name = "kick"
    label = "Kick"
    needs_public_url = True
    max_seconds = None

    def configured(self) -> tuple[bool, str]:
        if not env("KICK_ACCESS_TOKEN"):
            return False, "no public posting API; set KICK_ACCESS_TOKEN only if one exists"
        return False, "posting not implemented - Kick has no documented upload endpoint"

    def publish(self, clip: Path, meta: dict, public_url: str | None = None) -> PostResult:
        raise NotConfigured(
            "Kick has no documented video-posting API. If that changes, implement "
            "this method; everything else is already wired."
        )
