"""Tiny CLI for the review queue.

    python -m cliper.review list [status]
    python -m cliper.review approve <id> [<id> ...]
    python -m cliper.review reject  <id> [<id> ...]
    python -m cliper.review from-issue        # ISSUE_BODY from the website's bulk form
"""
from __future__ import annotations

import difflib
import os
import re
import sys

from .queue import list_items, set_status


def _split_ids(text: str) -> list[str]:
    seen, out = set(), []
    for tok in re.split(r"[\s,;]+", text or ""):
        tok = tok.strip().strip("`'\"")
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def apply(action: str, ids: list[str]) -> tuple[list[str], list[str]]:
    """Set status on every id. Returns (done, problems)."""
    status = "approved" if action == "approve" else "rejected"
    known = {i["id"]: i for i in list_items()}
    done, problems = [], []
    for cid in ids:
        item = known.get(cid)
        if not item:
            near = difflib.get_close_matches(cid, known, n=1, cutoff=0.85)
            problems.append(f"`{cid}` not found" + (f" - did you mean `{near[0]}`?" if near else ""))
            continue
        if item.get("status") == "published":
            problems.append(f"`{cid}` is already published")
            continue
        set_status(cid, status)
        done.append(cid)
    return done, problems


def from_issue(body: str) -> tuple[str, bool]:
    from .creators import parse_issue   # same "### Label" issue-form format

    f = parse_issue(body)
    action_text = f.get("action", "").lower()
    action = "reject" if action_text.startswith("reject") else "approve"
    publish_now = action == "approve" and "now" in action_text
    done, problems = apply(action, _split_ids(f.get("clip ids", "")))

    verb = "Approved" if action == "approve" else "Rejected"
    lines = [f"**{verb} {len(done)} clip(s).**"]
    if done:
        lines.append(", ".join(f"`{d}`" for d in done))
    if problems:
        lines += ["", "Problems:"] + [f"- {p}" for p in problems]
    if publish_now and done:
        lines += ["", "Publish has started - links appear on the website (Posted section) "
                      "within about 10 minutes."]
    elif action == "approve" and done:
        lines += ["", "They post at the next scheduled publish."]
    return "\n".join(lines), publish_now and bool(done)


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
            for p in i.get("posts", []):
                print(f"           posted: {p.get('platform')} {p.get('url')}")
        return 0

    if cmd in {"approve", "reject"} and len(args) > 1:
        done, problems = apply(cmd, _split_ids(" ".join(args[1:])))
        for d in done:
            print(f"{d} -> {'approved' if cmd == 'approve' else 'rejected'}")
        for p in problems:
            print(p.replace("`", ""))
        return 0 if done and not problems else 1

    if cmd == "from-issue":
        msg, publish_now = from_issue(os.environ.get("ISSUE_BODY", ""))
        print(msg)
        if os.environ.get("RESULT_FILE"):
            with open(os.environ["RESULT_FILE"], "w", encoding="utf-8") as fh:
                fh.write(msg)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
                fh.write(f"publish_now={'true' if publish_now else 'false'}\n")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
