"""Titles, captions, hashtags, cover frame - per platform.

Two paths:
  1. LLM (optional, free): if LLM_API_KEY is set, the clip's own transcript,
     the chat reaction and the stream title go to any OpenAI-compatible chat
     endpoint. Defaults to Google Gemini's free tier (aistudio.google.com, no
     card). Groq and OpenRouter work too - set LLM_BASE_URL and LLM_MODEL.
  2. Templates: no key, or the call fails -> deterministic text built from the
     transcript and chat. The pipeline never blocks on the LLM.

Every string here is yours, not the creator's. That matters: platform review
treats added original framing as transformation, and copied titles as reposting.
Credit to the creator is always appended by code, never left to the model.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field

from .chat import Candidate
from .config import env

GEMINI_OPENAI = "https://generativelanguage.googleapis.com/v1beta/openai"

HOOK_TEMPLATES = [
    "chat could not believe this",
    "he really just did that",
    "wait for the end",
    "this is why chat went insane",
    "nobody expected this",
    "the reaction says it all",
    "this moment broke chat",
]

GENERIC_TAGS = ["shorts", "clips", "stream", "gaming", "funny"]
_CLEAN = re.compile(r"[^a-z0-9]+")
_STOP = set("""a an the and or but if then so to of in on at for with from by is are was
were be been it its this that these those i you he she we they me my your his her our
their im youre dont cant just like yeah oh uh um okay ok gonna wanna what why how when
where who not no yes do did does have has had get got go going lol bro
about after again all also always any back because before being can come could
even ever every from here into know let look make more much need never now only
other over really right said say see should some still sure take than thing think
though through time too very want way well went what whole will would yeah yo
alright actually literally gonna gotta kinda wait guys chat okay maybe something
nothing everything anything someone everyone people there their these those where while""" .split())


@dataclass
class ClipMeta:
    title: str                 # YouTube title, <= 100 chars, ends with #shorts
    caption: str               # Instagram / TikTok caption (hook + credit + hashtags)
    description: str           # YouTube description
    hashtags: list[str]        # 3-5, '#'-prefixed, campaign ones first
    keywords: list[str]        # YouTube tags (hidden), up to 15
    hook: str                  # burned into the title bar, short
    cover_ms: int = 0          # frame used as the Reel / TikTok cover
    source: str = "template"   # "llm" or "template", for the review list
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _tag(word: str) -> str:
    return _CLEAN.sub("", word.lower())


def _uniq(items):
    seen, out = set(), []
    for i in items:
        k = i.lower()
        if i and k not in seen:
            seen.add(k)
            out.append(i)
    return out


def _topic_words(text: str, n: int = 4) -> list[str]:
    """Most frequent meaningful words in the transcript - cheap keywording."""
    counts: dict[str, int] = {}
    for w in re.findall(r"[a-zA-Z']{5,}", text.lower()):
        w = w.strip("'")
        if w not in _STOP:
            counts[w] = counts.get(w, 0) + 1
    return [w for w, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c > 1][:n]


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


# --------------------------------------------------------------------------- LLM

PROMPT = """You write metadata for a short vertical clip (YouTube Shorts, Instagram Reels, TikTok)
cut from a livestream. Goal: maximum retention and click-through WITHOUT lying.

Streamer: {creator}
Stream title: {stream_title}
What the streamer says in the clip (auto transcript, may have errors):
\"\"\"{transcript}\"\"\"
What chat spammed at that moment: {chat}

Return ONLY a JSON object with these keys:
- "hook": 2-6 words, shown as a text bar on the video for the first seconds. Creates curiosity
  about what happens, e.g. "he did NOT see that coming". No hashtags, no emojis, no names.
- "title": YouTube Shorts title, max 60 characters. Lead with the most surprising concrete detail
  from the transcript. Include the streamer's name only if it fits naturally. No hashtags.
- "caption": 1-2 short lines for Instagram/TikTok. First line is a hook or question that makes
  people comment. Max 150 characters. No hashtags.
- "hashtags": 3 lowercase topic hashtags without '#' specific to THIS moment or game
  (not generic words like viral, fyp, shorts).
- "keywords": 8 search phrases people would type to find this moment.

Rules: never invent events not supported by the transcript or chat; never claim to be the
streamer; no slurs; English."""


def _llm_config() -> tuple[str, str, str] | None:
    key = env("LLM_API_KEY") or env("GEMINI_API_KEY")
    if not key:
        return None
    base = env("LLM_BASE_URL", GEMINI_OPENAI).rstrip("/")
    model = env("LLM_MODEL", "gemini-2.5-flash")
    return base, key, model


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def llm_fields(creator: str, stream_title: str, transcript: str, chat: str) -> dict | None:
    cfg = _llm_config()
    if not cfg or not transcript.strip():
        return None
    base, key, model = cfg
    import requests

    prompt = PROMPT.format(
        creator=creator, stream_title=stream_title or "(unknown)",
        transcript=transcript.strip()[:1800], chat=chat.strip()[:300] or "(none)",
    )
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        r.raise_for_status()
        data = _parse_json(r.json()["choices"][0]["message"]["content"])
    except Exception as exc:  # network, quota, bad JSON: fall back, never fail the clip
        print(f"[meta] LLM skipped ({type(exc).__name__}: {str(exc)[:120]}) - using templates")
        return None

    out = {
        "hook": str(data.get("hook", "")).strip().strip('"#')[:40],
        "title": str(data.get("title", "")).replace("#", "").strip()[:70],
        "caption": str(data.get("caption", "")).replace("#", "").strip()[:220],
        "hashtags": [_tag(h) for h in data.get("hashtags", []) if _tag(h)][:3],
        "keywords": [str(k).strip()[:30] for k in data.get("keywords", []) if str(k).strip()][:10],
    }
    if not out["hook"] or not out["title"]:
        return None
    return out


# ----------------------------------------------------------------------- build

def build_meta(
    creator_display: str,
    cand: Candidate,
    stream_title: str = "",
    extra_tags: list[str] | None = None,
    campaign_hashtags: list[str] | None = None,
    transcript: str = "",
    source_platform: str = "",
    pre_roll: int = 10,
    use_llm: bool = True,
) -> ClipMeta:
    creator_tag = _tag(creator_display)
    topics = _topic_words(transcript)

    gen = llm_fields(creator_display, stream_title, transcript, cand.sample) if use_llm else None
    if gen:
        hook, yt_title, line = gen["hook"], gen["title"], gen["caption"]
        topic_tags, keywords, source = gen["hashtags"], gen["keywords"], "llm"
    else:
        hook = pick_hook(cand)
        quote = _best_line(transcript)
        yt_title = f"{creator_display}: {quote}" if quote else f"{creator_display} - {hook}"
        line = (f"\"{quote}\" 💀" if quote else hook.capitalize()) + "\nWould you have reacted the same?"
        topic_tags = [_tag(t) for t in (extra_tags or []) + topics][:3]
        keywords = [creator_display, f"{creator_display} clips", f"{creator_display} live",
                    f"{creator_display} funny moments", *topics]
        source = "template"

    # Hashtags: campaign-required first (never dropped), then the creator, then
    # topic tags. Instagram caps posts at 5 hashtags; YouTube shows the first 3.
    camp = [h if h.startswith("#") else f"#{h}" for h in (campaign_hashtags or [])]
    hashtags = _uniq(camp + [f"#{creator_tag}"] + [f"#{t}" for t in topic_tags if t])
    hashtags = hashtags[:max(5, len(camp))]

    credit = f"🎥 {creator_display}" + (f" on {source_platform.capitalize()}" if source_platform else "")
    caption = "\n".join([line, "", credit, "", " ".join(hashtags)]).strip()

    title = yt_title.strip()
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    title = f"{title} #shorts"

    description = "\n".join([
        line,
        "",
        credit + (f" - from the stream \"{stream_title}\"" if stream_title else ""),
        "Follow for more clips every day.",
        "",
        " ".join(["#shorts"] + hashtags[:4]),
    ])

    tags = _uniq([*keywords, creator_display, *GENERIC_TAGS])
    # YouTube: total tag text must stay under 500 chars.
    kept, total = [], 0
    for t in tags:
        if total + len(t) + 2 > 480:
            break
        kept.append(t)
        total += len(t) + 2

    return ClipMeta(
        title=title,
        caption=caption,
        description=description,
        hashtags=hashtags,
        keywords=kept[:15],
        hook=hook,
        # The chat spike sits pre_roll seconds in; a frame just after it is
        # the most expressive cover.
        cover_ms=(pre_roll + 1) * 1000,
        source=source,
    )


def _best_line(transcript: str, max_len: int = 60) -> str:
    """The most quotable sentence: short, and ideally loud (! or ?)."""
    parts = [p.strip(" -\"'") for p in re.split(r"(?<=[.!?])\s+", transcript) if p.strip()]
    parts = [p for p in parts if 3 <= len(p.split()) <= 12 and len(p) <= max_len]
    if not parts:
        return ""
    return max(parts, key=lambda p: (p.endswith(("!", "?")), -abs(len(p.split()) - 6)))
