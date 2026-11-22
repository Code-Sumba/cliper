from cliper import meta as M
from cliper import seo
from tests.test_meta import cand

FAKE = {
    "zackrawrr": ("asmongold", "zackrawrr shorts"),
    "asmongold": ("asmongold", "asmongold clips", "asmongold gta 6"),
    "asmongold gta 6": ("asmongold gta 6", "asmongold gta 6 reaction"),
    "gta 6 funny moments": ("gta 6 funny moments", "gta 6 funny moments streamers"),
}


def test_research_finds_alias_game_and_phrases(monkeypatch):
    monkeypatch.setattr(seo, "suggest", lambda q: FAKE.get(q, ()))
    r = seo.research("zackrawrr", game="Grand Theft Auto VI")
    assert r.alias == "asmongold" and r.game_tag == "gta6" and r.game_phrase == "gta 6"
    assert r.phrases[0].startswith("asmongold")
    assert "gta 6 funny moments" in r.phrases


def test_non_games_get_no_game_tag():
    assert seo.game_terms("Just Chatting") == ("", "")
    assert seo.game_terms("Slots") == ("", "")


def test_title_score_prefers_keyword_first_and_length():
    good = seo.score_title("Asmongold: Oh my god, it actually killed me", ["asmongold"])
    bad = seo.score_title("wait for the end", ["asmongold"])
    shouting = seo.score_title("ASMONGOLD SOOOOO FUNNY!!!", ["asmongold"])
    assert good > bad and good > shouting


def test_meta_uses_search_name_and_game(monkeypatch):
    monkeypatch.setattr(seo, "suggest", lambda q: FAKE.get(q, ()))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    m = M.build_meta("zackrawrr", cand(), transcript="Oh my god, it actually killed me. I did not see that.",
                     game="Grand Theft Auto VI", source_platform="twitch")
    assert m.title.lower().startswith("asmongold")
    assert "#asmongold" in m.hashtags and "#gta6" in m.hashtags
    assert "zackrawrr on Twitch" in m.caption          # credit keeps the real channel
    assert "asmongold gta 6" in m.keywords


def test_game_falls_back_to_stream_title():
    assert seo.effective_game("Just Chatting", "EXPLORING GTA 6") == "Grand Theft Auto VI"
    assert seo.effective_game("Grand Theft Auto VI", "anything") == "Grand Theft Auto VI"
    assert seo.game_terms(seo.effective_game("Just Chatting", "VANITY FAIR"))[0] == ""
