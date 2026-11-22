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
Game: {game}
Stream title: {stream_title}
Phrases people actually search on YouTube right now (from autocomplete): {keywords}
What the streamer says in the clip (auto transcript, may have errors):
\"\"\"{transcript}\"\"\"
What chat spammed at that moment: {chat}

Return ONLY a JSON object with these keys:
- "hook": 2-6 words, shown as a text bar on the video for the first seconds. Creates curiosity
  about what happens, e.g. "he did NOT see that coming". No hashtags, no emojis, no names.
- "title": YouTube Shorts title, 35-60 characters. Put the streamer's name or one searched phrase
  in the first few words, then the most surprising concrete detail from the transcript.
  Curiosity over summary; never reveal the punchline. No hashtags, no ALL CAPS, no swearing.
- "caption": 1-2 short lines for Instagram/TikTok. First line is a hook or question that makes
  people comment. Max 150 characters. No hashtags.
- "hashtags": 3 lowercase topic hashtags without '#' specific to THIS moment or game
  (not generic words like viral, fyp, shorts).
- "keywords": 8 search phrases people would type to find this moment; prefer the searched
  phrases above when they fit.

Rules: never invent events not supported by the transcript or chat; never claim to be the
streamer; no slurs; English."""


_MODEL_CACHE: dict[str, str] = {}


def pick_model(base: str, key: str, wanted: str) -> str:
    """When the configured model 404s (providers retire names often), ask the
    endpoint what it serves and take the newest fast model. Cached per run."""
    if base in _MODEL_CACHE:
        return _MODEL_CACHE[base]
    import requests

    r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    ids = [m.get("id", "").split("/")[-1] for m in r.json().get("data", [])]
    ids = [i for i in ids if i and not re.search(r"embed|tts|image|audio|vision|live|aqa|learnlm|gemma", i)]
    family = wanted.split("-")[0] if "-" in wanted else ""

    def rank(i: str):
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", i)[:1]] or [0.0]
        return ("flash" in i and "lite" not in i, family and i.startswith(family),
                "preview" not in i and "exp" not in i, nums[0], -len(i))

    best = max(ids, key=rank) if ids else wanted
    print(f"[meta] model '{wanted}' unavailable - using '{best}' (set LLM_MODEL to pin one)")
    _MODEL_CACHE[base] = best
    return best


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


def llm_fields(creator: str, stream_title: str, transcript: str, chat: str,
               game: str = "", keywords: list[str] | None = None) -> dict | None:
    cfg = _llm_config()
    if not cfg or not transcript.strip():
        return None
    base, key, model = cfg
    import requests

    prompt = PROMPT.format(
        creator=creator, stream_title=stream_title or "(unknown)",
        transcript=transcript.strip()[:1800], chat=chat.strip()[:300] or "(none)",
        game=game or "(unknown)", keywords=", ".join(keywords or []) or "(none)",
    )
    try:
        def ask(m: str, low_effort: bool = True):
            body = {
                "model": m,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "response_format": {"type": "json_object"},
            }
            if low_effort:
                # Thinking models spend a minute reasoning about a 6-word hook.
                body["reasoning_effort"] = "low"
            resp = requests.post(f"{base}/chat/completions", json=body, timeout=150,
                                 headers={"Authorization": f"Bearer {key}"})
            if resp.status_code == 400 and low_effort:      # endpoint without that knob
                return ask(m, low_effort=False)
            return resp
        r = ask(_MODEL_CACHE.get(base, model))
        if r.status_code == 404:                  # retired model name
            r = ask(pick_model(base, key, model))
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
    game: str = "",
    use_seo: bool = True,
) -> ClipMeta:
    topics = _topic_words(transcript)

    # Search demand (YouTube autocomplete): the name people search, the game,
    # and real phrases. Empty on network failure - the rest still works.
    from . import seo
    game = seo.effective_game(game, stream_title)
    res = seo.research(creator_display, game=game, topics=topics) if use_seo else seo.Research()
    name = res.alias.title() if res.alias else creator_display.strip("_")   # zackrawrr -> Asmongold
    game_phrase = res.game_phrase.upper() if res.game_phrase in {"gta 6", "gta 5", "cs2", "wow"} \
        else res.game_phrase.title()
    focus = [k for k in (res.alias or creator_display.lower(), res.game_phrase) if k]

    gen = llm_fields(name, stream_title, transcript, cand.sample,
                     game=game_phrase, keywords=res.tags(8)) if use_llm else None
    if gen:
        hook, line = gen["hook"], gen["caption"]
        options = [gen["title"], f"{name}: {gen['title']}" if name.lower() not in gen["title"].lower() else ""]
        topic_tags, keywords, source = gen["hashtags"], gen["keywords"], "llm"
    else:
        hook = pick_hook(cand)
        quote = _best_line(transcript)
        options = [
            f"{name}: {quote}" if quote else "",
            f"{quote} ({name} {game_phrase})" if quote and game_phrase else "",
            f"{name} {game_phrase}: {quote}" if quote and game_phrase else "",
            # A stock hook only when there is no real line to quote: a quote
            # from the moment out-clicks "nobody expected this" every time.
            "" if quote else (f"{name} {game_phrase} - {hook}" if game_phrase else f"{name} - {hook}"),
        ]
        line = (f"\"{quote}\" 💀" if quote else hook.capitalize()) + "\nWould you have reacted the same?"
        topic_tags = [_tag(t) for t in (extra_tags or []) + topics][:2]
        keywords = [name, f"{name} clips", f"{name} funny moments", *topics]
        source = "template"
    # Pick the option that scores best on the checks that move Shorts CTR.
    yt_title = seo.best_title([o for o in options if o and len(o) <= 70] or options, focus)

    # Hashtags: campaign-required first (never dropped), then the name people
    # search, the game, a searched phrase, then topics. Instagram allows 5.
    camp = [h if h.startswith("#") else f"#{h}" for h in (campaign_hashtags or [])]
    phrase_tags = [_tag(p) for p in res.phrases if res.game_phrase and res.game_phrase in p][:1]
    hashtags = _uniq(camp + [f"#{_tag(name)}"] + ([f"#{res.game_tag}"] if res.game_tag else [])
                     + [f"#{t}" for t in phrase_tags + topic_tags if t])
    hashtags = hashtags[:max(5, len(camp))]

    credit = f"🎥 {creator_display}" + (f" on {source_platform.capitalize()}" if source_platform else "")
    caption = "\n".join([line, "", credit, "", " ".join(hashtags)]).strip()

    title = yt_title.strip()
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    title = f"{title} #shorts"

    # The description is indexed: one plain sentence carrying the top searched
    # phrases reads naturally and ranks better than a wall of tags.
    searchable = ", ".join(res.tags(3))
    description = "\n".join([
        line,
        "",
        credit + (f" - from the stream \"{stream_title}\"" if stream_title else ""),
        (f"More {searchable} clips every day - follow so you don't miss the next one."
         if searchable else "Follow for more clips every day."),
        "",
        " ".join(["#shorts"] + hashtags[:4]),
    ])

    tags = _uniq([*res.tags(10), *keywords, name, creator_display, *GENERIC_TAGS])
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


# Titles/captions that swear get age-restricted or quietly down-ranked; the
# burned-in captions still carry the real words.
_PROFANE = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|ass(hole)?|dick\w*|pussy|cunt|nigg\w*|fag\w*|"
                      r"retard\w*|whore|slut|bastard|damn|motherf\w*|wtf|stfu)\b", re.I)


def _mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.9


def _best_line(transcript: str, max_len: int = 60) -> str:
    """The most quotable sentence: short, and ideally loud (! or ?)."""
    parts = [p.strip(" -\"'") for p in re.split(r"(?<=[.!?])\s+", transcript) if p.strip()]
    parts = [p for p in parts if 3 <= len(p.split()) <= 12 and len(p) <= max_len
             and not _PROFANE.search(p) and _mostly_latin(p)]
    if not parts:
        return ""
    return max(parts, key=lambda p: (p.endswith(("!", "?")), -abs(len(p.split()) - 6)))
