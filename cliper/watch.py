"""Entry point: who is live, and which VODs are new? Run on a short cron."""
from __future__ import annotations

import json
import sys

from .config import STATE_DIR, ensure_dirs, load_creators
from .live import find_new_streams

PENDING_FILE = STATE_DIR / "pending_streams.json"


def main() -> int:
    ensure_dirs()
    creators = load_creators()
    fresh = find_new_streams(creators)
    PENDING_FILE.write_text(
        json.dumps([f.__dict__ for f in fresh], indent=2), encoding="utf-8"
    )
    print(f"[watch] {len(fresh)} new stream(s) to harvest")
    for f in fresh:
        print(f"  - {f.creator} [{f.platform}] {f.vod_id} :: {f.title[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
