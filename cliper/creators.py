"""Add / remove / block creators - from the website (via a GitHub issue) or locally.

    python -m cliper.creators add twitch xqc
    python -m cliper.creators add youtube https://www.youtube.com/@IShowSpeed
    python -m cliper.creators remove xqc
    python -m cliper.creators block somechannel      # never auto-discover
    python -m cliper.creators from-issue             # reads ISSUE_BODY (workflow)

creators.yml keeps its comments: new entries are appended as text and the file
is re-parsed to prove it is still valid before it is written.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import requests
import yaml

from .config import CREATORS_FILE, env, load_creators
from .discover import EXCLUDE_FILE, load_auto, save_auto
from .live import TWITCH_API, YOUTUBE_API, _twitch_headers


class CreatorError(ValueError):
    pass


# ------------------------------------------------------------------ resolve

def resolve_twitch(text: str) -> tuple[str, str]:
    login = re.sub(r"^.*twitch\.tv/", "", text.strip()).strip("/@ ").split("/")[0].lower()
    if not re.fullmatch(r"[a-z0-9_]{3,25}", login):
        raise CreatorError(f"'{text}' is not a Twitch channel name")
    r = requests.get(f"{TWITCH_API}/users", params={"login": login}, headers=_twitch_headers(), timeout=20)
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        raise CreatorError(f"Twitch channel '{login}' does not exist")
    return login, data[0].get("display_name") or login


def resolve_youtube(text: str) -> tuple[str, str]:
    text = text.strip()
    m = re.search(r"(UC[A-Za-z0-9_-]{22})", text)
    key = env("YOUTUBE_API_KEY")
    if m and key:
        cid = m.group(1)
        r = requests.get(f"{YOUTUBE_API}/channels", params={"part": "snippet", "id": cid, "key": key}, timeout=20)
        r.raise_for_status()
        items = r.json().get("items") or []
        if not items:
            raise CreatorError(f"YouTube channel {cid} does not exist")
        return cid, items[0]["snippet"]["title"]
    handle = re.sub(r"^.*youtube\.com/", "", text).strip("/ ").split("/")[0]
    if key and not m:
        h = handle if handle.startswith("@") else f"@{handle}"
        r = requests.get(f"{YOUTUBE_API}/channels", params={"part": "snippet", "forHandle": h, "key": key}, timeout=20)
        r.raise_for_status()
        items = r.json().get("items") or []
        if items:
            return items[0]["id"], items[0]["snippet"]["title"]
    # No API key: yt-dlp reads the channel page (1 request, no quota).
    url = text if text.startswith("http") else f"https://www.youtube.com/{handle if handle.startswith('@') else '@' + handle}"
    try:
        out = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-items", "0", "-J", url],
                             capture_output=True, text=True, timeout=90)
        info = json.loads(out.stdout or "{}")
    except Exception as exc:
        raise CreatorError(f"could not look up '{text}': {exc}") from exc
    cid = info.get("channel_id") or ""
    if not cid.startswith("UC"):
        raise CreatorError(f"'{text}' is not a YouTube channel")
    return cid, info.get("channel") or info.get("uploader") or cid


# --------------------------------------------------------------------- edit

def _handle_for(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "", name.lower().replace(" ", "_"))[:24] or "creator"
    h, n = base, 2
    while h in taken:
        h, n = f"{base}{n}", n + 1
    return h


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)        # JSON strings are valid YAML


def add(platform: str, channel: str, permission: str = "none", note: str = "",
        post_per_week: int | None = None, platforms: list[str] | None = None) -> str:
    platform = platform.strip().lower()
    permission = (permission or "none").strip().lower()
    if permission not in {"none", "granted", "campaign"}:
        raise CreatorError("permission must be none, granted or campaign")
    if platform == "twitch":
        cid, name = resolve_twitch(channel)
    elif platform == "youtube":
        cid, name = resolve_youtube(channel)
    else:
        raise CreatorError("platform must be twitch or youtube (Kick is not supported as a source yet)")

    existing = load_creators(include_auto=False)
    for c in existing:
        if (platform == "twitch" and (c.twitch or "").lower() == cid) or \
           (platform == "youtube" and c.youtube_channel_id == cid):
            raise CreatorError(f"{name} is already in the list as '{c.handle}'")
    handle = _handle_for(name, {c.handle for c in existing})

    lines = [
        "",
        f"  - handle: {handle}",
        f"    display_name: {_q(name)}",
        f"    twitch: {cid if platform == 'twitch' else 'null'}",
        f"    youtube_channel_id: {cid if platform == 'youtube' else 'null'}",
        "    kick: null",
        f"    permission: {permission}",
        f"    permission_note: {_q(note or 'Added from the website. No permission recorded.')}",
    ]
    if post_per_week:
        lines.append(f"    post_per_week: {int(post_per_week)}")
    if platforms:
        lines.append(f"    platforms: [{', '.join(platforms)}]")

    old = CREATORS_FILE.read_text(encoding="utf-8")
    new = old.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    parsed = yaml.safe_load(new)
    if not any(c.get("handle") == handle for c in parsed.get("creators", [])):
        raise CreatorError("internal: the new entry did not parse into the creators list")
    CREATORS_FILE.write_text(new, encoding="utf-8")

    # A manual entry replaces an auto-discovered one for the same channel.
    key = f"{platform}:{cid}"
    save_auto([a for a in load_auto() if a.get("key") != key])
    return f"Added **{name}** ({platform}: `{cid}`) as `{handle}`, permission `{permission}`."


def remove(handle_or_channel: str) -> str:
    target = handle_or_channel.strip().lower().lstrip("@")
    auto = load_auto()
    kept = [a for a in auto if target not in {a["handle"].lower(), (a.get("twitch") or "").lower(),
                                              (a.get("youtube_channel_id") or "").lower()}]
    if len(kept) != len(auto):
        save_auto(kept)
        block(target)     # otherwise discovery would add it straight back
        return f"Removed auto-discovered `{target}` and blocked it from discovery."

    text = CREATORS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    match = None
    for c in load_creators(include_auto=False):
        if target in {c.handle.lower(), (c.twitch or "").lower(), (c.youtube_channel_id or "").lower()}:
            match = c.handle
    if not match:
        raise CreatorError(f"no creator '{handle_or_channel}' in the list")
    start = next(i for i, line in enumerate(text) if re.match(rf"^\s*-\s*handle:\s*{re.escape(match)}\s*$", line))
    end = start + 1
    while end < len(text) and not re.match(r"^\s*-\s*handle:|^\S", text[end]):
        end += 1
    while end > start + 1 and not text[end - 1].strip():
        end -= 1                                        # keep the blank separator
    if start > 0 and not text[start - 1].strip():
        start -= 1                                      # and the blank line add() wrote
    new = "".join(text[:start] + text[end:])
    parsed = yaml.safe_load(new)
    if any(c.get("handle") == match for c in parsed.get("creators", [])):
        raise CreatorError("internal: removal failed")
    CREATORS_FILE.write_text(new, encoding="utf-8")
    return f"Removed `{match}` from creators.yml."


def block(channel: str) -> str:
    channel = channel.strip().lower().lstrip("@")
    current = EXCLUDE_FILE.read_text(encoding="utf-8") if EXCLUDE_FILE.exists() else \
        "# Channels discovery must never add. One per line.\n"
    if channel not in {line.split("#")[0].strip().lower() for line in current.splitlines()}:
        EXCLUDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        EXCLUDE_FILE.write_text(current.rstrip("\n") + f"\n{channel}\n", encoding="utf-8")
    return f"`{channel}` will never be auto-discovered."


# -------------------------------------------------------------------- issue

def parse_issue(body: str) -> dict:
    """GitHub issue forms render as '### Label\\n\\nvalue' blocks."""
    out, label = {}, None
    for line in body.replace("\r", "").split("\n"):
        if line.startswith("### "):
            label = line[4:].strip().lower()
            out[label] = ""
        elif label is not None:
            out[label] += line + "\n"
    return {k: ("" if v.strip() == "_No response_" else v.strip()) for k, v in out.items()}


def from_issue(body: str) -> str:
    f = parse_issue(body)
    action = f.get("action", "Add").lower()
    channel = f.get("channel", "")
    if not channel:
        raise CreatorError("the Channel field is empty")
    if action.startswith("remove"):
        return remove(channel)
    if action.startswith("block"):
        return block(channel)
    platforms = [p.strip().lower() for p in f.get("post to", "").split(",") if p.strip()]
    ppw = f.get("posts per week", "")
    return add(f.get("platform", ""), channel, f.get("permission", "none").split()[0],
               f.get("permission note", ""), int(ppw) if ppw.isdigit() else None,
               [p for p in platforms if p in {"youtube", "instagram", "tiktok", "webhook"}] or None)


def main() -> int:
    a = sys.argv[1:]
    try:
        if a[:1] == ["add"] and len(a) >= 3:
            msg = add(a[1], a[2], a[3] if len(a) > 3 else "none")
        elif a[:1] == ["remove"] and len(a) == 2:
            msg = remove(a[1])
        elif a[:1] == ["block"] and len(a) == 2:
            msg = block(a[1])
        elif a[:1] == ["from-issue"]:
            msg = from_issue(os.environ.get("ISSUE_BODY", ""))
        else:
            print(__doc__)
            return 2
    except (CreatorError, requests.RequestException) as exc:
        msg = f"❌ {exc}" if isinstance(exc, CreatorError) else f"❌ network error talking to the platform: {type(exc).__name__}"
        print(msg)
        _result(msg)
        return 1
    print(msg)
    _result("✅ " + msg)
    return 0


def _result(msg: str) -> None:
    path = os.environ.get("RESULT_FILE")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(msg)


if __name__ == "__main__":
    sys.exit(main())
