"""The review queue.

Rendered clips land here as JSON records. publish.py only ever touches records
whose status is `approved`, or `pending` when AUTO_PUBLISH is on AND the
creator's permission allows it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import STATE_DIR

QUEUE_FILE = STATE_DIR / "queue.json"


def _load() -> list[dict]:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    return []


def _save(items: list[dict]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def add(record: dict) -> None:
    items = _load()
    record.setdefault("status", "pending")
    record.setdefault("created_at", int(time.time()))
    record.setdefault("id", f"{record.get('creator','x')}-{record.get('vod_id','0')}-{len(items):03d}")
    items.append(record)
    _save(items)


def list_items(status: str | None = None) -> list[dict]:
    items = _load()
    return [i for i in items if status is None or i.get("status") == status]


def set_status(item_id: str, status: str, note: str = "") -> bool:
    items = _load()
    for i in items:
        if i["id"] == item_id:
            i["status"] = status
            if note:
                i["note"] = note
            _save(items)
            return True
    return False


def posted_this_week(creator: str, now: float | None = None) -> int:
    now = now or time.time()
    week_ago = now - 7 * 86400
    return sum(
        1 for i in _load()
        if i.get("creator") == creator
        and i.get("status") == "published"
        and i.get("published_at", 0) >= week_ago
    )
