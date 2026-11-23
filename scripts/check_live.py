"""Live checks of every external service, from wherever this runs (GitHub's
runner via .github/workflows/check.yml, or your PC). Read-only; never posts,
never prints a secret.

    python scripts/check_live.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cliper.config import env  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        try:
            ok, info = fn()
        except Exception as exc:
            ok, info = False, f"{type(exc).__name__}: {str(exc)[:160]}"
        results.append((name, ok, info))
        return fn
    return wrap


@check("AI titles (LLM_API_KEY)")
def _llm():
    from cliper import meta
    from cliper.chat import Candidate  # noqa: F401
    cfg = meta._llm_config()
    if not cfg:
        return False, "LLM_API_KEY not set"
    base, _, model = cfg
    out = meta.llm_fields("Kai Cenat", "EXPLORING GTA 6",
                          "Bro he just stole the police car and drove it straight into the ocean. No way!",
                          "LMAO OMEGALUL KEKW", game="GTA 6", keywords=["kai cenat gta 6", "gta 6 funny moments"])
    if not out:
        return False, f"no usable answer from {meta._MODEL_CACHE.get(base, model)} at {base.split('/')[2]} (see log above)"
    return True, f"{meta._MODEL_CACHE.get(base, model)}: hook='{out['hook']}' | title='{out['title']}'"


@check("YouTube autocomplete (SEO)")
def _suggest():
    from cliper import seo
    got = seo.suggest("kai cenat")
    return bool(got), f"{len(got)} suggestions, e.g. {list(got[:3])}" if got else "blocked or empty"


@check("Twitch API")
def _twitch():
    from cliper.live import twitch_latest_vods
    v = twitch_latest_vods("kaicenat", 1)
    return True, f"latest VOD found, game='{v[0].game}'" if v else "no VODs"


@check("YouTube upload login")
def _yt():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YOUTUBE_CLIENT_ID"), "client_secret": env("YOUTUBE_CLIENT_SECRET"),
        "refresh_token": env("YOUTUBE_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=20)
    return r.ok, "token refresh works" if r.ok else r.json().get("error_description", r.text[:120])


@check("Instagram")
def _ig():
    g = "https://graph.facebook.com/v21.0"
    t, u = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    a = requests.get(f"{g}/{u}", params={"fields": "username,media_count", "access_token": t}, timeout=20).json()
    if "error" in a:
        return False, a["error"].get("message", "")[:150]
    d = requests.get(f"{g}/debug_token", params={"input_token": t, "access_token": t}, timeout=20).json().get("data", {})
    exp = "never expires" if d.get("expires_at") == 0 else f"EXPIRES at {d.get('expires_at')}"
    return True, f"@{a.get('username')}, {a.get('media_count')} posts, token {exp}"


@check("Facebook Reels")
def _fb():
    from cliper.platforms.facebook import Facebook, _token
    ok, why = Facebook().configured()
    if not ok:
        return False, why
    g = "https://graph.facebook.com/v21.0"
    pg = requests.get(f"{g}/{env('FB_PAGE_ID')}", params={"fields": "name", "access_token": _token()}, timeout=20).json()
    if "error" in pg:
        return False, pg["error"].get("message", "")[:150]
    return True, f"page '{pg.get('name')}' reachable"


@check("Storage (Supabase)")
def _storage():
    from cliper.storage import get_storage
    ok, why = get_storage().configured()
    return ok, "configured" if ok else why


def main() -> int:
    width = max(len(n) for n, _, _ in results)
    for name, ok, info in results:
        print(f"[{' ok ' if ok else 'FAIL'}] {name:<{width}}  {info}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
