"""Is a creator live, and has a new VOD appeared?

Run on a cron. Keeps a small JSON state file in the repo so we only ever
process a VOD once.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import STATE_DIR, Creator, env

TWITCH_OAUTH = "https://id.twitch.tv/oauth2/token"
TWITCH_API = "https://api.twitch.tv/helix"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
SEEN_FILE = STATE_DIR / "seen.json"


@dataclass
class StreamRef:
    creator: str
    platform: str          # twitch | youtube
    vod_id: str            # Twitch video id, or YouTube video id
    title: str
    url: str
    duration_seconds: int = 0

    def key(self) -> str:
        return f"{self.platform}:{self.vod_id}"


# ---------------------------------------------------------------- state

def load_seen() -> dict:
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def mark_seen(ref: StreamRef, status: str = "harvested") -> None:
    seen = load_seen()
    seen[ref.key()] = {
        "creator": ref.creator,
        "title": ref.title,
        "status": status,
        "at": int(time.time()),
    }
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- twitch

_token_cache: dict[str, object] = {}


def twitch_token() -> str:
    """App access token, cached until shortly before expiry."""
    now = time.time()
    if _token_cache.get("exp", 0) > now + 60:
        return str(_token_cache["tok"])
    cid, secret = env("TWITCH_CLIENT_ID"), env("TWITCH_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set")
    r = requests.post(
        TWITCH_OAUTH,
        params={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["tok"] = data["access_token"]
    _token_cache["exp"] = now + int(data.get("expires_in", 3600))
    return data["access_token"]


def _twitch_headers() -> dict:
    return {"Client-Id": env("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {twitch_token()}"}


def twitch_is_live(login: str) -> bool:
    r = requests.get(
        f"{TWITCH_API}/streams", params={"user_login": login},
        headers=_twitch_headers(), timeout=20,
    )
    r.raise_for_status()
    return bool(r.json().get("data"))


def twitch_latest_vods(login: str, limit: int = 3) -> list[StreamRef]:
    """Most recent archived broadcasts for a channel."""
    h = _twitch_headers()
    u = requests.get(f"{TWITCH_API}/users", params={"login": login}, headers=h, timeout=20)
    u.raise_for_status()
    users = u.json().get("data") or []
    if not users:
        return []
    uid = users[0]["id"]
    v = requests.get(
        f"{TWITCH_API}/videos",
        params={"user_id": uid, "type": "archive", "first": limit},
        headers=h, timeout=20,
    )
    v.raise_for_status()
    return [
        StreamRef(
            creator=login, platform="twitch", vod_id=item["id"],
            title=item.get("title", ""), url=item.get("url", ""),
            duration_seconds=_parse_twitch_duration(item.get("duration", "")),
        )
        for item in v.json().get("data", [])
    ]


def _parse_twitch_duration(s: str) -> int:
    """Twitch returns durations like '3h21m7s'."""
    total, num = 0, ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            mult = {"h": 3600, "m": 60, "s": 1}.get(ch, 0)
            total += int(num or 0) * mult
            num = ""
    return total


# ---------------------------------------------------------------- youtube

def youtube_is_live(channel_id: str) -> bool:
    key = env("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY not set")
    r = requests.get(
        f"{YOUTUBE_API}/search",
        params={"part": "id", "channelId": channel_id, "eventType": "live",
                "type": "video", "key": key},
        timeout=20,
    )
    r.raise_for_status()
    return bool(r.json().get("items"))


def youtube_recent_uploads(channel_id: str, limit: int = 3) -> list[StreamRef]:
    """Recent videos, which for a streamer are usually the stream archives.

    Note the quota cost: a search call is 100 units of a 10,000/day default,
    so poll a handful of creators, not hundreds.
    """
    key = env("YOUTUBE_API_KEY")
    r = requests.get(
        f"{YOUTUBE_API}/search",
        params={"part": "snippet", "channelId": channel_id, "order": "date",
                "type": "video", "maxResults": limit, "key": key},
        timeout=20,
    )
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        vid = it["id"]["videoId"]
        out.append(StreamRef(
            creator=channel_id, platform="youtube", vod_id=vid,
            title=it["snippet"].get("title", ""),
            url=f"https://www.youtube.com/watch?v={vid}",
        ))
    return out


# ---------------------------------------------------------------- driver

def find_new_streams(creators: list[Creator]) -> list[StreamRef]:
    """Every unprocessed VOD across all configured creators."""
    seen = load_seen()
    fresh: list[StreamRef] = []
    for c in creators:
        try:
            refs: list[StreamRef] = []
            if c.twitch:
                refs += twitch_latest_vods(c.twitch)
            if c.youtube_channel_id:
                refs += youtube_recent_uploads(c.youtube_channel_id)
            for r in refs:
                r.creator = c.handle
                if r.key() not in seen:
                    fresh.append(r)
        except Exception as exc:  # one bad creator must not kill the run
            print(f"[live] {c.handle}: {type(exc).__name__}: {exc}")
    return fresh
