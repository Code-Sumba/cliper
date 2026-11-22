"""Put a rendered clip somewhere the platforms can fetch it.

Instagram and TikTok do not accept a file upload from you - they take a URL and
fetch it themselves. So every clip has to be readable over plain HTTPS before
it can be posted.

Backends are pluggable. `s3` covers Cloudflare R2, Backblaze B2, AWS S3 and
anything else speaking the S3 API, which is almost everything. `none` is the
default so the pipeline runs end to end with no bucket: clips simply stay local
and the URL-hungry platforms report themselves as unconfigured rather than
crashing.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Protocol

from .config import env


class StorageError(RuntimeError):
    pass


class Storage(Protocol):
    name: str

    def configured(self) -> tuple[bool, str]: ...
    def upload(self, path: Path, key: str | None = None) -> str: ...


class NullStorage:
    """No bucket. Clips stay on disk; nothing gets a public URL."""

    name = "none"

    def configured(self) -> tuple[bool, str]:
        return False, "no storage backend set (CLIPER_STORAGE)"

    def upload(self, path: Path, key: str | None = None) -> str:
        raise StorageError(
            "No storage backend configured. Set CLIPER_STORAGE=s3 and the S3_* "
            "variables, or publish only to platforms that take a file upload."
        )


class S3Storage:
    """Any S3-compatible bucket. Cloudflare R2's free tier (10 GB) is plenty.

    R2 settings:
      S3_ENDPOINT_URL = https://<account-id>.r2.cloudflarestorage.com
      S3_BUCKET       = your bucket
      S3_PUBLIC_BASE  = https://pub-<hash>.r2.dev  (or your custom domain)

    The bucket must serve objects publicly, or the platforms cannot fetch them.
    """

    name = "s3"

    def __init__(self) -> None:
        self.endpoint = env("S3_ENDPOINT_URL")
        self.bucket = env("S3_BUCKET")
        self.public_base = env("S3_PUBLIC_BASE") or env("MEDIA_PUBLIC_BASE_URL")
        self.key_id = env("S3_ACCESS_KEY_ID")
        self.secret = env("S3_SECRET_ACCESS_KEY")
        self.region = env("S3_REGION", "auto")
        self.prefix = env("S3_PREFIX", "clips")

    def configured(self) -> tuple[bool, str]:
        missing = [
            n for n, v in (
                ("S3_BUCKET", self.bucket),
                ("S3_ACCESS_KEY_ID", self.key_id),
                ("S3_SECRET_ACCESS_KEY", self.secret),
                ("S3_PUBLIC_BASE", self.public_base),
            ) if not v
        ]
        if missing:
            return False, "missing " + ", ".join(missing)
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False, "boto3 not installed (pip install boto3)"
        return True, ""

    def _client(self):
        import boto3
        from botocore.config import Config

        kwargs = {
            "aws_access_key_id": self.key_id,
            "aws_secret_access_key": self.secret,
            "region_name": self.region,
            # boto3 >= 1.36 adds CRC checksums to every upload by default, which
            # non-AWS services (Supabase, Backblaze, older MinIO) can reject.
            # Path-style keeps the bucket out of the hostname, which they need too.
            "config": Config(
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                s3={"addressing_style": "path"},
                # Supabase sits behind Cloudflare, which throws the odd 520.
                retries={"max_attempts": 6, "mode": "standard"},
            ),
        }
        if self.endpoint:
            kwargs["endpoint_url"] = self.endpoint
        return boto3.client("s3", **kwargs)

    def upload(self, path: Path, key: str | None = None) -> str:
        ok, why = self.configured()
        if not ok:
            raise StorageError(f"s3 storage not ready: {why}")
        key = key or f"{self.prefix}/{path.name}" if self.prefix else (key or path.name)
        ctype = mimetypes.guess_type(path.name)[0] or "video/mp4"
        # One PUT, not multipart: a sub-minute clip is well under 50 MB, and
        # Supabase's multipart UploadPart is where the 520s came from.
        try:
            with path.open("rb") as fh:
                self._client().put_object(
                    Bucket=self.bucket, Key=key, Body=fh,
                    ContentType=ctype, CacheControl="public, max-age=86400",
                )
        except Exception as exc:          # botocore/boto3 errors -> one type for callers
            raise StorageError(f"upload of {path.name} failed: {type(exc).__name__}: {exc}") from exc
        return f"{self.public_base.rstrip('/')}/{key}"


BACKENDS: dict[str, type] = {"none": NullStorage, "s3": S3Storage}


def get_storage() -> Storage:
    name = env("CLIPER_STORAGE", "none").lower()
    cls = BACKENDS.get(name, NullStorage)
    return cls()
