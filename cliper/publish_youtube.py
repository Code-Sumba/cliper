"""Upload a Short to your own YouTube channel.

Quota note: videos.insert costs ~1600 units against a 10,000/day default, so
about 6 uploads a day before you must request more quota. The workflow caps
itself accordingly.
"""
from __future__ import annotations

from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import env

TOKEN_URI = "https://oauth2.googleapis.com/token"


def _client():
    creds = Credentials(
        token=None,
        refresh_token=env("YOUTUBE_REFRESH_TOKEN"),
        client_id=env("YOUTUBE_CLIENT_ID"),
        client_secret=env("YOUTUBE_CLIENT_SECRET"),
        token_uri=TOKEN_URI,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_short(
    video: Path, title: str, description: str, tags: list[str],
    privacy: str = "private", category_id: str = "20",
) -> str:
    """Upload and return the video id.

    Defaults to `private` on purpose. Flip to `public` only once you have
    watched a few of these go out correctly.
    """
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:15],
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video), chunksize=-1, resumable=True, mimetype="video/mp4")
    req = _client().videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = req.next_chunk()
    return response["id"]
