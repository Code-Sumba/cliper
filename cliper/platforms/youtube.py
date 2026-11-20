"""YouTube Shorts via the Data API v3.

Quota is the constraint: videos.insert costs about 1600 units against a default
10,000 per day, so roughly 6 uploads daily before you must request more.
"""
from __future__ import annotations

from pathlib import Path

from ..config import env
from . import register
from .base import NotConfigured, PostResult, PublishError, Publisher

TOKEN_URI = "https://oauth2.googleapis.com/token"


@register
class YouTube(Publisher):
    name = "youtube"
    label = "YouTube Shorts"
    needs_public_url = False
    max_seconds = 180          # Shorts cap; 60s was the old limit

    def configured(self) -> tuple[bool, str]:
        missing = [n for n in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
                               "YOUTUBE_REFRESH_TOKEN") if not env(n)]
        if missing:
            return False, "missing " + ", ".join(missing)
        try:
            import googleapiclient  # noqa: F401
        except ImportError:
            return False, "google-api-python-client not installed"
        return True, ""

    def _client(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=env("YOUTUBE_REFRESH_TOKEN"),
            client_id=env("YOUTUBE_CLIENT_ID"),
            client_secret=env("YOUTUBE_CLIENT_SECRET"),
            token_uri=TOKEN_URI,
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def publish(self, clip: Path, meta: dict, public_url: str | None = None) -> PostResult:
        ok, why = self.configured()
        if not ok:
            raise NotConfigured(why)
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "title": meta.get("title", "")[:100],
                "description": meta.get("caption", "")[:4900],
                "tags": meta.get("keywords", [])[:15],
                "categoryId": env("YOUTUBE_CATEGORY_ID", "20"),   # 20 = Gaming
            },
            "status": {
                "privacyStatus": env("YOUTUBE_PRIVACY", "private"),
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(str(clip), chunksize=-1, resumable=True, mimetype="video/mp4")
        req = self._client().videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        try:
            while response is None:
                _, response = req.next_chunk()
        except Exception as exc:
            raise PublishError(f"youtube upload failed: {exc}") from exc
        vid = response["id"]
        return PostResult(self.name, vid, f"https://youtube.com/shorts/{vid}")
