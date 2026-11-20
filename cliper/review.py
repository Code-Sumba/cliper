"""Tiny CLI for the review queue.

    python -m cliper.review list
    python -m cliper.review approve <id>
    python -m cliper.review reject  <id>
"""
from __future__ import annotations

import sys

from .queue import list_items, set_status


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "list"

    if cmd == "list":
        items = list_items(args[1] if len(args) > 1 else None)
        if not items:
            print("queue empty")
        for i in items:
            m = i.get("meta", {})
            print(f"{i['status']:<10} {i['id']:<28} score={i.get('score',0):<8} {m.get('title','')[:50]}")
            print(f"           chat: {i.get('chat_sample','')[:70]}")
        return 0

    if cmd in {"approve", "reject"} and len(args) > 1:
        status = "approved" if cmd == "approve" else "rejected"
        ok = set_status(args[1], status)
        print(f"{args[1]} -> {status}" if ok else f"{args[1]} not found")
        return 0 if ok else 1

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
