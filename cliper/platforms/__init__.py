"""Registry of posting platforms.

Enable with CLIPER_PLATFORMS, comma separated, e.g.:

    CLIPER_PLATFORMS=youtube,instagram

Leave it unset to mean "every platform whose credentials are present", which is
usually what you want: add credentials for a platform and it starts posting,
remove them and it stops.

Per-creator overrides live in creators.yml (`platforms:`), and a campaign's
`allowed_platforms` narrows it further. The narrowest wins.
"""
from __future__ import annotations

from ..config import env
from .base import NotConfigured, PostResult, PublishError, Publisher

_REGISTRY: dict[str, type[Publisher]] = {}


def register(cls: type[Publisher]) -> type[Publisher]:
    _REGISTRY[cls.name] = cls
    return cls


# Import for side effects: each module registers itself.
from . import instagram, kick, tiktok, webhook, youtube  # noqa: E402,F401


def all_publishers() -> dict[str, Publisher]:
    return {name: cls() for name, cls in sorted(_REGISTRY.items())}


def enabled_publishers(limit_to: list[str] | None = None) -> list[Publisher]:
    """Configured publishers, filtered by env and by any caller-supplied list."""
    wanted = [p.strip().lower() for p in env("CLIPER_PLATFORMS").split(",") if p.strip()]
    out = []
    for name, pub in all_publishers().items():
        if wanted and name not in wanted:
            continue
        if limit_to and name not in [l.lower() for l in limit_to]:
            continue
        ok, _ = pub.configured()
        if ok:
            out.append(pub)
    return out


__all__ = [
    "register", "all_publishers", "enabled_publishers",
    "Publisher", "PostResult", "PublishError", "NotConfigured",
]
