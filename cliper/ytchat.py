"""YouTube live-chat replay -> the same (offset, text) pairs Twitch chat gives.

Needed because creators who stream on YouTube rather than Twitch were otherwise
invisible to the pipeline: no chat source meant no spike signal meant no clips.

yt-dlp writes the replay as newline-delimited JSON, one action per line, with
each message's offset in milliseconds from the start of the stream.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def download_chat(url: str, work_dir: Path, key: str, extra_args: list[str] | None = None) -> Path | None:
    """Fetch the live-chat replay. Returns None when the video has none."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / key
    try:
        subprocess.run(
            ["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
             "-o", str(out), *(extra_args or []), url],
            check=True, capture_output=True, timeout=900,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        err = (exc.stderr or b"")
        err = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)
        tail = " | ".join(l.strip() for l in err.splitlines() if "ERROR" in l or "WARNING" in l)[-400:]
        print(f"[ytchat] replay download failed: {type(exc).__name__}: {tail or 'no stderr'}")
        return None
    files = sorted(work_dir.glob(f"{key}*.live_chat.json*"))
    return files[0] if files else None


def _text_of(renderer: dict) -> str:
    """Flatten a message into plain text, keeping emoji shortcodes as words."""
    parts = []
    for run in (renderer.get("message", {}) or {}).get("runs", []) or []:
        if "text" in run:
            parts.append(run["text"])
        elif "emoji" in run:
            emoji = run["emoji"]
            shortcuts = emoji.get("shortcuts") or []
            parts.append(shortcuts[0].strip(":") if shortcuts else emoji.get("emojiId", ""))
    return " ".join(p for p in parts if p).strip()


def parse(path: Path) -> list[tuple[float, str]]:
    """Read yt-dlp's live_chat file into (offset_seconds, text), sorted."""
    out: list[tuple[float, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                action = json.loads(line)
            except ValueError:
                continue
            offset_ms = action.get("videoOffsetTimeMsec")
            replay = action.get("replayChatItemAction") or {}
            if offset_ms is None:
                offset_ms = replay.get("videoOffsetTimeMsec")
            for item in replay.get("actions", []) or []:
                add = (item.get("addChatItemAction") or {}).get("item") or {}
                renderer = (
                    add.get("liveChatTextMessageRenderer")
                    or add.get("liveChatPaidMessageRenderer")
                )
                if not renderer:
                    continue
                stamp = offset_ms or renderer.get("timestampUsec")
                try:
                    seconds = float(stamp) / (1000.0 if offset_ms else 1_000_000.0)
                except (TypeError, ValueError):
                    continue
                text = _text_of(renderer)
                if text:
                    out.append((seconds, text))
    out.sort(key=lambda t: t[0])
    return out


def messages_for(url: str, work_dir: Path, key: str) -> list[tuple[float, str]]:
    path = download_chat(url, work_dir, key)
    if not path:
        return []
    msgs = parse(path)
    print(f"[ytchat] {len(msgs)} chat messages from the replay")
    return msgs
