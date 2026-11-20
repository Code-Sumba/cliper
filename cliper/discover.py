"""Find the creators worth clipping, every day, automatically.

    python -m cliper.discover sample    # record who is live right now (every watch run)
    python -m cliper.discover promote   # once a day: add the top creators to the list
    python -m cliper.discover top       # print the leaderboard

How creators are ranked
-----------------------
Every watch run (2 hours) records the biggest live streams and their viewer
counts. From those snapshots, per creator, over the last 24 hours:

    hours_watched = sum(viewers in each snapshot) x hours between snapshots

That is the industry measure of how much a stream was actually watched: a
creator live for 8 hours at 20k beats one who peaked at 60k for 30 minutes,
and has 8x the moments worth clipping. Peak viewers, days live out of the last
3, and "live now" are kept for the website.

Twitch: the top 100 live streams per snapshot (free, 1 API call).
YouTube: top 50 live streams once a day (101 quota units of 10,000), because a
search is expensive and the same quota pays for Shorts uploads.

Promotion
---------
Once a day, the top-ranked creators not already listed are added to
state/auto_creators.json (never to creators.yml - that file stays yours).
Auto creators are ALWAYS permission=none: clipped into the review queue, never
auto-published. Twitch creators with VODs switched off are skipped (no VOD, no
chat replay, nothing to clip). An auto creator who stays out of the top ranks
for DISCOVER_DROP_DAYS is removed again. Creators in state/discover_exclude.txt
are never added.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests

from .config import AUTO_CREATORS_FILE, STATE_DIR, env, load_creators
from .live import TWITCH_API, YOUTUBE_API, _twitch_headers

DISCOVERY_FILE = STATE_DIR / "discovery.json"
EXCLUDE_FILE = STATE_DIR / "discover_exclude.txt"
SAMPLE_HOURS = 2.0           # watch.yml runs every 2 hours
KEEP_SECONDS = 3 * 86400     # history kept for the leaderboard


def _int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def _now() -> int:
    return int(time.time())


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_db() -> dict:
    if DISCOVERY_FILE.exists():
        return json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
    return {"channels": {}, "last_youtube": 0, "last_promote": ""}


def save_db(db: dict) -> None:
    db["updated"] = _now()
    DISCOVERY_FILE.parent.mkdir(parents=True, exist_ok=True)
    DISCOVERY_FILE.write_text(json.dumps(db, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def excluded() -> set[str]:
    out = {x.strip().lower() for x in env("DISCOVER_EXCLUDE").split(",") if x.strip()}
    if EXCLUDE_FILE.exists():
        for line in EXCLUDE_FILE.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip().lower()
            if line:
                out.add(line)
    return out


def _record(db: dict, key: str, info: dict, viewers: int, now: int) -> None:
    ch = db["channels"].setdefault(key, {"samples": []})
    ch.update(info)
    ch["live_now"] = viewers
    ch["last_seen"] = now
    ch["samples"].append([now, viewers])


# ------------------------------------------------------------------ sampling

def sample_twitch(db: dict, now: int) -> int:
    if not (env("TWITCH_CLIENT_ID") and env("TWITCH_CLIENT_SECRET")):
        return 0
    langs = [x.strip() for x in env("DISCOVER_LANGUAGES", "en").split(",") if x.strip()]
    params: list[tuple[str, str]] = [("first", "100")]
    params += [("language", lang) for lang in langs if lang != "all"]
    r = requests.get(f"{TWITCH_API}/streams", params=params, headers=_twitch_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json().get("data", [])
    for s in rows:
        login = s["user_login"].lower()
        _record(db, f"twitch:{login}", {
            "platform": "twitch", "id": login, "name": s.get("user_name") or login,
            "url": f"https://www.twitch.tv/{login}", "game": s.get("game_name", ""),
            "title": s.get("title", "")[:120], "lang": s.get("language", ""),
        }, int(s.get("viewer_count", 0)), now)
    return len(rows)


def sample_youtube(db: dict, now: int) -> int:
    """Top live streams on YouTube. 101 units, so at most once per ~20 hours."""
    key = env("YOUTUBE_API_KEY")
    if not key or now - db.get("last_youtube", 0) < 20 * 3600:
        return 0
    params = {"part": "snippet", "eventType": "live", "type": "video", "order": "viewCount",
              "maxResults": 50, "key": key}
    lang = env("DISCOVER_LANGUAGES", "en").split(",")[0].strip()
    if lang and lang != "all":
        params["relevanceLanguage"] = lang
    if env("DISCOVER_YOUTUBE_CATEGORY", "20"):          # 20 = Gaming; "" for all
        params["videoCategoryId"] = env("DISCOVER_YOUTUBE_CATEGORY", "20")
    r = requests.get(f"{YOUTUBE_API}/search", params=params, timeout=30)
    r.raise_for_status()
    items = r.json().get("items", [])
    ids = [it["id"]["videoId"] for it in items]
    viewers: dict[str, int] = {}
    if ids:
        v = requests.get(f"{YOUTUBE_API}/videos", params={
            "part": "liveStreamingDetails", "id": ",".join(ids), "key": key}, timeout=30)
        v.raise_for_status()
        for it in v.json().get("items", []):
            viewers[it["id"]] = int((it.get("liveStreamingDetails") or {}).get("concurrentViewers", 0) or 0)
    for it in items:
        sn = it["snippet"]
        cid = sn["channelId"]
        _record(db, f"youtube:{cid}", {
            "platform": "youtube", "id": cid, "name": sn.get("channelTitle", cid),
            "url": f"https://www.youtube.com/channel/{cid}", "game": "",
            "title": sn.get("title", "")[:120], "lang": lang,
        }, viewers.get(it["id"]["videoId"], 0), now)
    db["last_youtube"] = now
    return len(items)


def prune(db: dict, now: int) -> None:
    for key in list(db["channels"]):
        ch = db["channels"][key]
        ch["samples"] = [s for s in ch["samples"] if now - s[0] <= KEEP_SECONDS]
        if not ch["samples"]:
            del db["channels"][key]
        elif now - ch.get("last_seen", 0) > SAMPLE_HOURS * 3600 + 900:
            ch["live_now"] = 0


def sample() -> dict:
    db, now = load_db(), _now()
    counts = {}
    for name, fn in (("twitch", sample_twitch), ("youtube", sample_youtube)):
        try:
            counts[name] = fn(db, now)
        except Exception as exc:       # discovery must never break watching
            print(f"[discover] {name} sample failed: {type(exc).__name__}: {exc}")
            counts[name] = 0
    prune(db, now)
    save_db(db)
    print(f"[discover] sampled {counts}")
    return counts


# ------------------------------------------------------------------- ranking

def stats(ch: dict, now: int) -> dict:
    """Leaderboard numbers for one channel."""
    day = [v for t, v in ch["samples"] if now - t <= 86400]
    yday = [v for t, v in ch["samples"] if 86400 < now - t <= 2 * 86400]
    hours = SAMPLE_HOURS if ch.get("platform") == "twitch" else 4.0   # youtube: 1 snapshot/day
    days_live = len({time.strftime("%Y-%m-%d", time.gmtime(t)) for t, _ in ch["samples"]})
    return {
        "hours_watched_24h": int(sum(day) * hours),
        "hours_watched_prev_24h": int(sum(yday) * hours),
        "avg_viewers_24h": int(sum(day) / len(day)) if day else 0,
        "peak_24h": max(day) if day else 0,
        "hours_live_24h": round(len(day) * hours, 1),
        "days_live_3d": min(days_live, 3),
        "live_now": ch.get("live_now", 0),
    }


def leaderboard(db: dict | None = None, limit: int = 50) -> list[dict]:
    db, now = db or load_db(), _now()
    rows = []
    for key, ch in db["channels"].items():
        st = stats(ch, now)
        if st["hours_watched_24h"] == 0 and st["live_now"] == 0:
            continue
        # Consistency bonus: someone live 3 days running is a reliable source.
        score = st["hours_watched_24h"] * (1 + 0.15 * (st["days_live_3d"] - 1))
        row = {"key": key, "platform": ch["platform"], "id": ch["id"], "name": ch["name"],
               "url": ch["url"], "game": ch.get("game", ""), "title": ch.get("title", ""),
               "score": int(score), **st}
        row["brand"] = is_brand(row)
        rows.append(row)
    rows.sort(key=lambda r: -r["score"])
    return rows[:limit]


# ----------------------------------------------------------------- promotion

_BRAND = re.compile(r"esports|official|_tv$|^tv_|league$|championship|gaming$|_live$", re.I)


def is_brand(row: dict) -> bool:
    """Official game / esports / company channels: reruns and tournament
    feeds, not a personality whose reactions make clips."""
    def norm(x: str) -> str:
        x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]", "", x.lower())
    login, game = norm(row["id"]), norm(row.get("game") or "")
    name = row["name"]
    if _BRAND.search(row["id"]) or _BRAND.search(name):
        return True
    return bool(game) and len(game) > 3 and (game in login or login in game)


def _handle(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "", name.lower().replace(" ", "_"))[:24] or "creator"
    h, n = base, 2
    while h in taken:
        h, n = f"{base}{n}", n + 1
    return h


def _twitch_has_vods(login: str) -> bool:
    h = _twitch_headers()
    u = requests.get(f"{TWITCH_API}/users", params={"login": login}, headers=h, timeout=20)
    u.raise_for_status()
    users = u.json().get("data") or []
    if not users:
        return False
    v = requests.get(f"{TWITCH_API}/videos", params={"user_id": users[0]["id"], "type": "archive",
                                                     "first": 1}, headers=h, timeout=20)
    v.raise_for_status()
    return bool(v.json().get("data"))


def load_auto() -> list[dict]:
    if AUTO_CREATORS_FILE.exists():
        return json.loads(AUTO_CREATORS_FILE.read_text(encoding="utf-8"))
    return []


def save_auto(items: list[dict]) -> None:
    AUTO_CREATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTO_CREATORS_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def promote(force: bool = False) -> list[str]:
    """Daily: refresh who is 'still top', drop the faded, add the new top."""
    db = load_db()
    today = _today()
    if db.get("last_promote") == today and not force:
        print("[discover] already promoted today")
        return []
    max_auto = _int("DISCOVER_MAX_AUTO", 6)
    per_day = _int("DISCOVER_ADD_PER_DAY", 2)
    drop_days = _int("DISCOVER_DROP_DAYS", 7)
    min_avg = _int("DISCOVER_MIN_VIEWERS", 1000)

    board = leaderboard(db, limit=200)
    top_keys = {r["key"] for r in board[:max_auto * 3]}
    auto = load_auto()
    for a in auto:
        if a["key"] in top_keys:
            a["last_top"] = today

    # Drop auto creators who faded out of the top ranks.
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(_now() - drop_days * 86400))
    kept = [a for a in auto if a.get("last_top", a.get("added", today)) >= cutoff]
    for a in auto:
        if a not in kept:
            print(f"[discover] dropped {a['display_name']} (out of the top for {drop_days} days)")

    manual = load_creators(include_auto=False)
    listed = ({f"twitch:{c.twitch.lower()}" for c in manual if c.twitch}
              | {f"youtube:{c.youtube_channel_id}" for c in manual if c.youtube_channel_id}
              | {a["key"] for a in kept})
    blocked = excluded()
    taken = {c.handle for c in manual} | {a["handle"] for a in kept}

    added: list[str] = []
    for r in board:
        if len(kept) >= max_auto or len(added) >= per_day:
            break
        if r["key"] in listed or r["id"].lower() in blocked or r["name"].lower() in blocked:
            continue
        if r["avg_viewers_24h"] < min_avg:
            continue
        if is_brand(r):
            continue
        try:
            if r["platform"] == "twitch" and not _twitch_has_vods(r["id"]):
                print(f"[discover] skip {r['name']}: VODs are off, nothing to clip")
                continue
        except Exception as exc:
            print(f"[discover] skip {r['name']}: {exc}")
            continue
        handle = _handle(r["name"], taken)
        taken.add(handle)
        entry = {
            "key": r["key"], "handle": handle, "display_name": r["name"],
            "twitch": r["id"] if r["platform"] == "twitch" else None,
            "youtube_channel_id": r["id"] if r["platform"] == "youtube" else None,
            "added": today, "last_top": today,
            "permission_note": (f"Auto-discovered {today}: ~{r['hours_watched_24h']:,} hours watched "
                                f"in 24h, avg {r['avg_viewers_24h']:,} viewers. No permission - review only."),
        }
        kept.append(entry)
        added.append(r["name"])
        print(f"[discover] added {r['name']} ({r['platform']}), score {r['score']:,}")

    save_auto(kept)
    db["last_promote"] = today
    save_db(db)
    return added


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "top"
    if cmd == "sample":
        sample()
    elif cmd == "promote":
        promote(force="--force" in sys.argv)
    elif cmd == "top":
        for i, r in enumerate(leaderboard(limit=25), 1):
            live = f"LIVE {r['live_now']:,}" if r["live_now"] else ""
            print(f"{i:>2}. {r['name'][:22]:<22} {r['platform']:<8} {r['hours_watched_24h']:>10,} h  "
                  f"avg {r['avg_viewers_24h']:>7,}  peak {r['peak_24h']:>7,}  {live}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
