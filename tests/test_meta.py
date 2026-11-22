from unittest import mock

from cliper import meta as M
from cliper.chat import Candidate


def cand(**kw):
    base = dict(offset_seconds=600, score=50.0, messages=120, reaction_weight=0.5,
                sample="LMAO LMAO OMEGALUL")
    base.update(kw)
    c = mock.Mock(spec=Candidate)
    for k, v in base.items():
        setattr(c, k, v)
    return c


TRANSCRIPT = ("Okay okay I got this. No way! He just jumped off the bridge! "
              "The bridge was right there chat, the bridge!")


def test_template_meta_uses_transcript_and_limits(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    m = M.build_meta("IShowSpeed", cand(), "Day 3 in Japan", transcript=TRANSCRIPT,
                     source_platform="youtube", pre_roll=10)
    assert m.source == "template"
    assert m.title.endswith("#shorts") and len(m.title) <= 100
    assert "jumped off the bridge" in m.title
    assert m.hashtags[0] == "#ishowspeed"
    assert 1 <= len(m.hashtags) <= 5
    assert "IShowSpeed on Youtube" in m.caption       # credit always present
    assert m.description and "#shorts" in m.description
    assert m.cover_ms == 11000
    assert sum(len(k) for k in m.keywords) < 500


def test_campaign_hashtags_first_and_kept():
    m = M.build_meta("Creator", cand(), transcript="", use_llm=False,
                     campaign_hashtags=["#ad", "brandx", "#c3", "#c4", "#c5", "#c6"])
    assert m.hashtags[:6] == ["#ad", "#brandx", "#c3", "#c4", "#c5", "#c6"]


def test_llm_path_parses_fenced_json(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "")            # empty var -> default endpoint
    body = ('```json\n{"hook":"he did NOT see that","title":"Speed jumps off the bridge #wow",'
            '"caption":"Would you jump?","hashtags":["#Japan","bridge","speed"],'
            '"keywords":["speed japan","speed bridge"]}\n```')
    resp = mock.Mock(ok=True)
    resp.raise_for_status = lambda: None
    resp.json = lambda: {"choices": [{"message": {"content": body}}]}
    with mock.patch("requests.post", return_value=resp) as post:
        m = M.build_meta("IShowSpeed", cand(), transcript=TRANSCRIPT)
    assert post.call_args.args[0].startswith(M.GEMINI_OPENAI)
    assert m.source == "llm"
    assert m.hook == "he did NOT see that"
    assert "#" not in m.title.replace("#shorts", "")
    assert "#japan" in m.hashtags


def test_llm_failure_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    with mock.patch("requests.post", side_effect=OSError("down")):
        m = M.build_meta("IShowSpeed", cand(), transcript=TRANSCRIPT)
    assert m.source == "template"


def test_titles_skip_profanity_and_non_latin():
    assert M._best_line("Get up me bitch! Give me a W in chat.") == "Give me a W in chat."
    assert M._best_line("Итекулок, а вот всё тут.") == ""
    assert not M._PROFANE.search("that class, I assume")


def test_retired_model_falls_back_to_newest_flash(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    M._MODEL_CACHE.clear()
    gone = mock.Mock(status_code=404)
    ok = mock.Mock(status_code=200, raise_for_status=lambda: None,
                   json=lambda: {"choices": [{"message": {"content":
                       '{"hook":"no way","title":"Kai Cenat drives into the ocean","caption":"why?"}'}}]})
    models = mock.Mock(raise_for_status=lambda: None, json=lambda: {"data": [
        {"id": "models/gemini-2.0-flash"}, {"id": "models/gemini-3.1-flash"},
        {"id": "models/gemini-3.1-flash-lite"}, {"id": "models/text-embedding-004"},
        {"id": "models/gemini-3.1-pro"}]})
    posts = iter([gone, ok])
    with mock.patch("requests.post", side_effect=lambda *a, **k: next(posts)) as post, \
         mock.patch("requests.get", return_value=models):
        out = M.llm_fields("Kai Cenat", "", "he drove into the ocean", "LMAO")
    assert out["hook"] == "no way"
    assert post.call_args.kwargs["json"]["model"] == "gemini-3.1-flash"
