"""What every posting platform has to provide.

Adding a platform means writing one class here and registering it. Nothing in
the pipeline needs to change: publish.py walks whatever is registered and
enabled, and the doctor reports on it automatically.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class PublishError(RuntimeError):
    pass


class NotConfigured(PublishError):
    """Credentials or capability missing. Not a failure - just skip this one."""


@dataclass
class PostResult:
    platform: str
    post_id: str
    url: str = ""

    def __str__(self) -> str:
        return f"{self.platform}:{self.post_id}" + (f" ({self.url})" if self.url else "")


class Publisher(ABC):
    #: short lowercase id used in config, env and creators.yml
    name: str = "base"
    #: human label for logs and the doctor
    label: str = "Base"
    #: True when the platform fetches the file from a URL instead of taking an
    #: upload. Those platforms need a storage backend configured.
    needs_public_url: bool = False
    #: longest clip the platform accepts, seconds. None = no limit worth enforcing.
    max_seconds: int | None = None

    @abstractmethod
    def configured(self) -> tuple[bool, str]:
        """(ready, why-not). Never raises."""

    @abstractmethod
    def publish(self, clip: Path, meta: dict, public_url: str | None = None) -> PostResult:
        """Post the clip. Raise NotConfigured to be skipped, PublishError to fail loudly."""

    def check_clip(self, clip: Path, meta: dict) -> tuple[bool, str]:
        """Platform-specific sanity check before posting."""
        if not clip.exists():
            return False, f"file missing: {clip}"
        if clip.stat().st_size == 0:
            return False, "file is empty"
        return True, ""
