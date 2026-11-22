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
    game: str = ""         # Twitch category, for titles / tags ("Grand Theft Auto VI")

    def key(self) -> str:
        return f"{self.platform}:{self.vod_id}"


# ---------------------------------------------------------------- state

def load_seen() -> dict:
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


MAX_ATTEMPTS = 3


def is_done(entry: dict | None) -> bool:
    """Harvested, or failed too often to keep trying."""
    if not entry:
        return False
    return entry.get("status") != "failed" or entry.get("attempts", 1) >= MAX_ATTEMPTS


def mark_seen(ref: StreamRef, status: str = "harvested") -> None:
    seen = load_seen()
    prev = seen.get(ref.key()) or {}
    seen[ref.key()] = {
        "creator": ref.creator,
        "title": ref.title,
        "status": status,
        "at": int(time.time()),
        "attempts": prev.get("attempts", 0) + 1,
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
        # Form body, not query string: a URL with the secret in it ends up in
        # exception messages and logs.
        data={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"},
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
    # The channel's category: Twitch VODs don't carry one, and a streamer's
    # latest VODs are almost always the game they are on now.
    game = ""
    try:
        c = requests.get(f"{TWITCH_API}/channels", params={"broadcaster_id": uid}, headers=h, timeout=20)
        game = ((c.json().get("data") or [{}])[0]).get("game_name", "") if c.ok else ""
    except requests.RequestException:
        pass
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
            game=game,
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
    """Recent finished livestreams on a channel.

    2 quota units per call (uploads playlist + videos.list), not the 100 a
    search costs - this runs every 2 hours for every YouTube creator, and the
    same 10,000/day quota pays for Shorts uploads (~1,600 each).
    Ordinary uploads and Shorts are skipped: only ended live broadcasts have
    the chat replay the scorer needs.
    """
    key = env("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY not set")
    uploads = "UU" + channel_id[2:]
    r = requests.get(
        f"{YOUTUBE_API}/playlistItems",
        params={"part": "contentDetails", "playlistId": uploads,
                "maxResults": 15, "key": key},
        timeout=20,
    )
    r.raise_for_status()
    ids = [it["contentDetails"]["videoId"] for it in r.json().get("items", [])]
    if not ids:
        return []
    v = requests.get(
        f"{YOUTUBE_API}/videos",
        params={"part": "snippet,liveStreamingDetails,contentDetails",
                "id": ",".join(ids), "key": key},
        timeout=20,
    )
    v.raise_for_status()
    out = []
    for it in v.json().get("items", []):
        live = it.get("liveStreamingDetails") or {}
        if not live.get("actualEndTime"):
            continue                       # not a stream, or still live
        vid = it["id"]
        out.append(StreamRef(
            creator=channel_id, platform="youtube", vod_id=vid,
            title=it["snippet"].get("title", ""),
            url=f"https://www.youtube.com/watch?v={vid}",
            duration_seconds=_parse_iso_duration(it.get("contentDetails", {}).get("duration", "")),
        ))
        if len(out) >= limit:
            break
    return out


def _parse_iso_duration(s: str) -> int:
    """PT3H2M10S -> seconds."""
    total, num = 0, ""
    for ch in s.replace("PT", ""):
        if ch.isdigit():
            num += ch
        else:
            total += int(num or 0) * {"H": 3600, "M": 60, "S": 1}.get(ch, 0)
            num = ""
    return total


# ---------------------------------------------------------------- driver

def find_new_streams(creators: list[Creator]) -> list[StreamRef]:
    """Every unprocessed VOD across all configured creators."""
    seen = load_seen()
    fresh: list[StreamRef] = []
    for c in creators:
        try:
            refs: list[StreamRef] = []
            if c.twitch:
                refs += twitch_latest_vods(c.twitch, c.vods_per_check)
            if c.youtube_channel_id:
                refs += youtube_recent_uploads(c.youtube_channel_id, c.vods_per_check)
            for r in refs:
                r.creator = c.handle
                if not is_done(seen.get(r.key())):
                    fresh.append(r)
        except Exception as exc:  # one bad creator must not kill the run
            print(f"[live] {c.handle}: {type(exc).__name__}: {exc}")
    return fresh
