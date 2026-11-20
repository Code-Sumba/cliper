"""Titles, captions, hashtags.

Deliberately template-driven rather than LLM-driven by default: it costs
nothing, runs offline in a workflow, and is predictable. Set OPENAI_API_KEY or
ANTHROPIC_API_KEY and wire `llm_hook()` if you want generated hooks later.

Every string here is yours, not the creator's. That matters: platform review
treats added original framing as transformation, and copied titles as reposting.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, asdict

from .chat import Candidate

HOOK_TEMPLATES = [
    "chat could not believe this",
    "he really just did that",
    "wait for it",
    "this is why chat went insane",
    "nobody expected this",
    "the chat reaction says it all",
    "this moment broke chat",
]

BASE_TAGS = ["shorts", "clips", "live", "gaming", "viral", "funny", "stream"]
_CLEAN = re.compile(r"[^a-z0-9 ]+")


@dataclass
class ClipMeta:
    title: str
    caption: str
    hashtags: list[str]
    keywords: list[str]
    hook: str

    def as_dict(self) -> dict:
        return asdict(self)


def _slug_tags(words: list[str]) -> list[str]:
    out = []
    for w in words:
        w = _CLEAN.sub("", w.lower()).replace(" ", "")
        if 2 < len(w) < 24 and w not in out:
            out.append(w)
    return out


def pick_hook(cand: Candidate, rng: random.Random | None = None) -> str:
    """A hook line chosen from how chat reacted, not from the stream's title."""
    rng = rng or random.Random(cand.offset_seconds)
    sample = cand.sample.lower()
    if "clip it" in sample or "clipped" in sample:
        return "chat demanded this get clipped"
    if cand.reaction_weight > 0.8:
        return "chat completely lost it here"
    if cand.messages > 150:
        return "the whole chat reacted at once"
    return rng.choice(HOOK_TEMPLATES)


def build_meta(
    creator_display: str,
    cand: Candidate,
    stream_title: str = "",
    extra_tags: list[str] | None = None,
    campaign_hashtags: list[str] | None = None,
) -> ClipMeta:
    hook = pick_hook(cand)
    minute = cand.offset_seconds // 60

    title = f"{creator_display} - {hook} #shorts"[:95]
    tags = _slug_tags(
        [creator_display, *BASE_TAGS, *(extra_tags or [])]
    )
    # Campaign hashtags are mandatory where a campaign requires them, so they
    # go first and are never deduplicated away.
    hashtags = list(campaign_hashtags or []) + [f"#{t}" for t in tags[:12]]

    caption_bits = [
        hook.capitalize() + ".",
        f"From {creator_display}'s stream" + (f": {stream_title}" .rstrip() if stream_title else "") + ".",
        f"(around the {minute} minute mark)",
        "",
        " ".join(hashtags),
    ]
    return ClipMeta(
        title=title,
        caption="\n".join(caption_bits).strip(),
        hashtags=hashtags,
        keywords=tags[:15],
        hook=hook,
    )
