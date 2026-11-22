"""Search-demand keywords, tags and title scoring - a free, automatic subset
of what vidIQ / TubeBuddy do by hand.

Source of truth for "what do people search": YouTube's own search
autocomplete (no key, no quota). A phrase that autocomplete offers is a phrase
real people type, ranked roughly by popularity. That beats guessing tags.

    seo.research("Kai Cenat", game="Grand Theft Auto VI")
      -> Research(alias="kai cenat", game_tag="gta6",
                  phrases=["kai cenat gta 6", "gta 6 funny moments streamers", ...])

Everything degrades to "no data" on any network error - metadata is then built
from the transcript alone, as before.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

SUGGEST = "https://suggestqueries-clients6.youtube.com/complete/search"

# Twitch game names -> what people actually search / tag.
GAME_ALIASES = {
    "grand theft auto vi": ("gta 6", "gta6"),
    "grand theft auto v": ("gta 5", "gta5"),
    "counter-strike": ("cs2", "cs2"),
    "counter-strike 2": ("cs2", "cs2"),
    "league of legends": ("league of legends", "leagueoflegends"),
    "world of warcraft": ("wow", "worldofwarcraft"),
    "just chatting": ("", ""),           # not a game - no tag
    "irl": ("", ""),
    "slots": ("", ""),                  # gambling: keep it off the metadata
    "virtual casino": ("", ""),
}


@dataclass
class Research:
    alias: str = ""                       # the name people search, if different
    game_phrase: str = ""                 # "gta 6"
    game_tag: str = ""                    # "gta6"
    phrases: list[str] = field(default_factory=list)   # real search phrases, best first

    def tags(self, limit: int = 12) -> list[str]:
        return self.phrases[:limit]


@lru_cache(maxsize=256)
def suggest(query: str) -> tuple[str, ...]:
    """YouTube autocomplete for a query. Cached per run; () on any failure."""
    import requests

    try:
        r = requests.get(SUGGEST, params={"client": "youtube", "ds": "yt", "q": query,
                                          "hl": "en", "gl": "US"}, timeout=8)
        m = re.search(r"\((.*)\)\s*$", r.text, re.S)
        data = json.loads(m.group(1)) if m else []
        return tuple(s[0] for s in data[1]) if len(data) > 1 else ()
    except Exception:
        return ()


def game_terms(game: str) -> tuple[str, str]:
    g = (game or "").strip().lower()
    if not g:
        return "", ""
    if g in GAME_ALIASES:
        return GAME_ALIASES[g]
    phrase = re.sub(r"[^a-z0-9 ]+", "", g).strip()
    return phrase, phrase.replace(" ", "")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()


def research(creator: str, game: str = "", topics: list[str] | None = None) -> Research:
    """Which phrases around this clip have real search demand."""
    name = _norm(creator)
    game_phrase, game_tag = game_terms(game)
    res = Research(game_phrase=game_phrase, game_tag=game_tag)

    base = suggest(name)
    # Autocomplete answering a handle with another name (zackrawrr -> asmongold)
    # means people know the creator by that name: use it.
    if base and _norm(base[0]) != name and not _norm(base[0]).startswith(name):
        res.alias = _norm(base[0])

    who = res.alias or name
    queries = [who, f"{who} {game_phrase}".strip() if game_phrase else "",
               f"{game_phrase} funny moments" if game_phrase else "",
               f"{who} funny", f"{who} clips"]
    queries += [f"{who} {t}" for t in (topics or [])[:2]]

    scores: dict[str, float] = {}
    for qi, q in enumerate(q for q in queries if q):
        for rank, phrase in enumerate(suggest(q)):
            p = _norm(phrase)
            if not p or len(p) > 40:
                continue
            # Earlier queries are about this clip; earlier ranks are more searched.
            relevant = who.split()[0] in p or (game_phrase and game_phrase in p)
            if not relevant:
                continue
            scores[p] = scores.get(p, 0) + (10 - rank) * (1.0 if qi < 3 else 0.6)
    res.phrases = [p for p, _ in sorted(scores.items(), key=lambda kv: -kv[1])]
    return res


# ------------------------------------------------------------------- scoring

def score_title(title: str, keywords: list[str]) -> int:
    """0-100 heuristic in the spirit of vidIQ's title score: the checks that
    actually move Shorts CTR and search, each visible below."""
    t = title.replace("#shorts", "").strip()
    low = t.lower()
    s = 0
    n = len(t)
    s += 25 if 30 <= n <= 60 else 15 if 20 <= n <= 70 else 5          # fits on screen
    first = low[:25]
    if any(k and k in first for k in keywords):                       # keyword up front
        s += 25
    elif any(k and k in low for k in keywords):
        s += 15
    if re.search(r"[!?]|\d", t):                                       # emotion / number
        s += 10
    if re.search(r"\b(this|when|how|why|nobody|never|finally|accidentally|actually)\b", low):
        s += 10                                                       # curiosity words
    if not t.isupper() and sum(c.isupper() for c in t) / max(1, sum(c.isalpha() for c in t)) < 0.5:
        s += 10                                                       # not shouting
    if t.count("#") <= 1:
        s += 10                                                       # clean, few hashtags
    if not re.search(r"(.)\1{3,}", t):                                 # no "sooooo"
        s += 10
    return min(100, s)


def best_title(candidates: list[str], keywords: list[str]) -> str:
    cands = [c.strip() for c in candidates if c and c.strip()]
    return max(cands, key=lambda c: score_title(c, keywords)) if cands else ""


# Stream titles name the game far more reliably than a channel's *current*
# category (which may be Just Chatting by the time the VOD is clipped).
_TITLE_GAMES = [
    (r"gta ?(6|vi)\b", "Grand Theft Auto VI"),
    (r"gta ?(5|v)\b|\bgta ?rp\b", "Grand Theft Auto V"),
    (r"\bminecraft\b", "Minecraft"),
    (r"\bfortnite\b", "Fortnite"),
    (r"\bvalorant\b", "Valorant"),
    (r"\bcs ?2\b|counter[- ]strike", "Counter-Strike 2"),
    (r"elden ring", "Elden Ring"),
    (r"\broblox\b", "Roblox"),
    (r"\bwarzone\b", "Call of Duty: Warzone"),
    (r"\bapex\b", "Apex Legends"),
    (r"league of legends|\blol\b ranked", "League of Legends"),
    (r"\bfifa\b|\bea fc\b|\bfc ?2[0-9]\b", "EA Sports FC"),
    (r"\bnba ?2k", "NBA 2K"),
    (r"\bwow\b|world of warcraft", "World of Warcraft"),
]


def game_from_title(title: str) -> str:
    t = (title or "").lower()
    for pattern, game in _TITLE_GAMES:
        if re.search(pattern, t):
            return game
    return ""


def effective_game(category: str, stream_title: str) -> str:
    """The category if it is a game, else whatever game the title names."""
    return category if game_terms(category)[0] else (game_from_title(stream_title) or category)
