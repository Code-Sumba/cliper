"""Mint a YouTube refresh token. Run this once, on your own machine.

    python scripts/get_youtube_token.py

A refresh token is what lets a GitHub Actions run upload to your channel
without a browser. You cannot generate one in CI - the consent screen needs a
human - so this is a local, one-time step.

Before running:
  1. console.cloud.google.com -> create a project
  2. Enable "YouTube Data API v3"
  3. OAuth consent screen -> External -> add yourself as a test user
  4. Credentials -> Create -> OAuth client ID -> Desktop app
  5. Download the JSON, or just note the client id and secret

The token it prints goes in your repo secrets as YOUTUBE_REFRESH_TOKEN.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install the dependency first:\n"
              "    pip install google-auth-oauthlib")
        return 1

    client_id = os.environ.get("YOUTUBE_CLIENT_ID") or input("OAuth client id: ").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET") or input("OAuth client secret: ").strip()
    if not client_id or not client_secret:
        print("Both the client id and secret are required.")
        return 1

    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    print("\nA browser window will open. Sign in with the Google account that owns")
    print("the YouTube channel you want to upload to.\n")
    creds = flow.run_local_server(port=0, prompt="consent")

    if not creds.refresh_token:
        print("\nNo refresh token came back. Revoke the app's access at")
        print("myaccount.google.com/permissions and run this again.")
        return 1

    print("\n" + "=" * 60)
    print("Add these three to your repo secrets:")
    print("=" * 60)
    print(f"YOUTUBE_CLIENT_ID      = {client_id}")
    print(f"YOUTUBE_CLIENT_SECRET  = {client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN  = {creds.refresh_token}")
    print("=" * 60)

    out = Path("youtube_token.json")
    out.write_text(json.dumps({"refresh_token": creds.refresh_token}, indent=2), encoding="utf-8")
    print(f"\nAlso written to {out} - delete it once the secrets are set.")
    print("It is already in .gitignore, but do not commit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
