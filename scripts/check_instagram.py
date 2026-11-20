"""Check the Instagram credentials without posting anything.

    python scripts/check_instagram.py

Reads IG_USER_ID and IG_ACCESS_TOKEN from the environment or from .env.
Makes read-only Graph API calls; never prints the token.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cliper.config import env  # noqa: E402  (loads .env)
from cliper.platforms.instagram import GRAPH  # noqa: E402


def get(path: str, token: str, **params) -> tuple[bool, dict]:
    r = requests.get(f"{GRAPH}/{path}", params={**params, "access_token": token}, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = {"error": {"message": r.text[:200]}}
    return r.ok, body


def why(body: dict) -> str:
    e = body.get("error", {})
    return f"{e.get('message', body)} (code {e.get('code')}, subcode {e.get('error_subcode')})"


def main() -> int:
    user, token = env("IG_USER_ID"), env("IG_ACCESS_TOKEN")
    missing = [n for n, v in (("IG_USER_ID", user), ("IG_ACCESS_TOKEN", token)) if not v]
    if missing:
        print("missing:", ", ".join(missing), "- put them in .env")
        return 1
    if not user.isdigit():
        print(f"IG_USER_ID should be a number like 1784..., got {user!r}")
        return 1

    problems = 0

    ok, me = get("me", token, fields="id,name")
    if ok:
        print(f"[  ok  ] token works - belongs to: {me.get('name')} (id {me.get('id')})")
    else:
        print(f"[ FAIL ] token rejected: {why(me)}")
        return 1

    ok, acct = get(user, token, fields="username,name,followers_count,media_count")
    if ok:
        print(f"[  ok  ] IG account: @{acct.get('username')} - "
              f"{acct.get('followers_count', '?')} followers, {acct.get('media_count', '?')} posts")
    else:
        problems += 1
        print(f"[ FAIL ] cannot read IG account {user}: {why(acct)}")
        print("         Check IG_USER_ID is the instagram_business_account id, not the Page id.")

    ok, lim = get(f"{user}/content_publishing_limit", token, fields="quota_usage,config")
    if ok:
        data = (lim.get("data") or [{}])[0]
        total = (data.get("config") or {}).get("quota_total", "?")
        print(f"[  ok  ] publishing allowed - used {data.get('quota_usage', 0)} of {total} posts in 24h")
    else:
        problems += 1
        print(f"[ FAIL ] publishing check failed: {why(lim)}")
        print("         Usually a missing instagram_content_publish permission.")

    print()
    print("All good - Instagram can publish." if not problems else f"{problems} problem(s).")
    print("Expiry: paste the token into developers.facebook.com/tools/debug/accesstoken - "
          "a Page token should say 'Expires: Never'.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
