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


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def auto_publish_enabled() -> bool:
    return env("AUTO_PUBLISH", "false").lower() in {"1", "true", "yes"}


def ensure_dirs() -> None:
    for d in (STATE_DIR, WORK_DIR, CLIPS_DIR):
        d.mkdir(parents=True, exist_ok=True)
