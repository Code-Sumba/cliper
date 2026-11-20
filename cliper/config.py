"""Config loading: creators.yml plus environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CREATORS_FILE = ROOT / "creators.yml"
STATE_DIR = ROOT / "state"
WORK_DIR = ROOT / "work"
CLIPS_DIR = ROOT / "clips"

# Permission levels that may ever be auto-published.
PUBLISHABLE = {"campaign", "granted"}


@dataclass
class Creator:
    handle: str
    display_name: str
    permission: str
    permission_note: str = ""
    twitch: str | None = None
    youtube_channel_id: str | None = None
    kick: str | None = None
    campaign: dict[str, Any] = field(default_factory=dict)
    platforms: list[str] = field(default_factory=list)
    clip_seconds: int = 38
    pre_roll: int = 10
    max_candidates: int = 20
    post_per_week: int = 3

    @property
    def may_auto_publish(self) -> bool:
        return self.permission in PUBLISHABLE


def load_creators(path: Path | None = None) -> list[Creator]:
    data = yaml.safe_load((path or CREATORS_FILE).read_text(encoding="utf-8"))
    defaults = data.get("defaults", {}) or {}
    out: list[Creator] = []
    for raw in data.get("creators", []) or []:
        merged = {**defaults, **{k: v for k, v in raw.items() if v is not None}}
        merged.setdefault("permission", "none")
        merged.setdefault("permission_note", "")
        allowed = Creator.__dataclass_fields__.keys()
        out.append(Creator(**{k: v for k, v in merged.items() if k in allowed}))
    return out


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read a local .env for desktop runs. Real environment variables win, so
    GitHub Actions secrets are never overridden. Supports `KEY=value`, quoted
    values and trailing `  # comments` as written in .env.example."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        else:
            for marker in (" #", "\t#"):
                if marker in value:
                    value = value.split(marker, 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def auto_publish_enabled() -> bool:
    return env("AUTO_PUBLISH", "false").lower() in {"1", "true", "yes"}


def ensure_dirs() -> None:
    for d in (STATE_DIR, WORK_DIR, CLIPS_DIR):
        d.mkdir(parents=True, exist_ok=True)
