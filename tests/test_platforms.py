"""Platform registry, storage and the publish gates."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cliper.config import Creator
from cliper.platforms import all_publishers, enabled_publishers
from cliper.platforms.base import Publisher
from cliper.publish import check_campaign, eligible, platforms_for
from cliper.storage import NullStorage, S3Storage, get_storage
from cliper.ytchat import parse


def test_every_platform_registered():
    names = set(all_publishers())
    assert {"youtube", "instagram", "tiktok", "kick", "webhook"} <= names


def test_publishers_implement_the_interface():
    for name, pub in all_publishers().items():
        assert isinstance(pub, Publisher), name
        ok, why = pub.configured()
        assert isinstance(ok, bool) and isinstance(why, str)
        if not ok:
            assert why, f"{name} must say why it is not configured"


def test_nothing_enabled_without_credentials(monkeypatch):
    for var in ("YOUTUBE_REFRESH_TOKEN", "IG_USER_ID", "TIKTOK_ACCESS_TOKEN", "WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CLIPER_PLATFORMS", raising=False)
    assert enabled_publishers() == []


def test_webhook_turns_on_with_just_a_url(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.delenv("CLIPER_PLATFORMS", raising=False)
    assert "webhook" in [p.name for p in enabled_publishers()]


def test_platform_env_filter(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setenv("CLIPER_PLATFORMS", "youtube")
    assert "webhook" not in [p.name for p in enabled_publishers()]


def test_kick_is_registered_but_never_ready():
    ok, why = all_publishers()["kick"].configured()
    assert ok is False and why


def test_url_fetching_platforms_declared():
    pubs = all_publishers()
    assert pubs["instagram"].needs_public_url is True
    assert pubs["youtube"].needs_public_url is False


# ---------------------------------------------------------------- storage

def test_null_storage_refuses_clearly():
    ok, why = NullStorage().configured()
    assert ok is False and "CLIPER_STORAGE" in why


def test_s3_lists_every_missing_variable(monkeypatch):
    for v in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
              "S3_PUBLIC_BASE", "MEDIA_PUBLIC_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    ok, why = S3Storage().configured()
    assert ok is False
    for v in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_PUBLIC_BASE"):
        assert v in why


def test_storage_backend_selected_by_env(monkeypatch):
    monkeypatch.setenv("CLIPER_STORAGE", "s3")
    assert get_storage().name == "s3"
    monkeypatch.setenv("CLIPER_STORAGE", "nonsense")
    assert get_storage().name == "none"


# ---------------------------------------------------------------- gates

def _creator(**kw):
    base = dict(handle="x", display_name="X", permission="campaign")
    base.update(kw)
    return Creator(**base)


def test_permission_none_never_publishes_on_its_own(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH", "true")
    ok, why = eligible({"status": "pending", "meta": {}}, _creator(permission="none"))
    assert ok is False and "permission" in why


def test_permission_none_publishes_once_you_approve(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH", "false")
    ok, _ = eligible({"status": "approved", "meta": {}}, _creator(permission="none"))
    assert ok is True


def test_pending_blocked_while_auto_publish_off(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH", "false")
    ok, why = eligible({"status": "pending", "meta": {}}, _creator())
    assert ok is False and "approval" in why


def test_pending_allowed_when_auto_publish_on(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH", "true")
    ok, _ = eligible({"status": "pending", "meta": {"caption": "hi"}}, _creator())
    assert ok is True


def test_campaign_requires_its_hashtags():
    c = _creator(campaign={"required_hashtags": ["#Expedia"]})
    assert check_campaign({"meta": {"caption": "nice clip"}}, c)[0] is False
    assert check_campaign({"meta": {"caption": "nice #expedia clip"}}, c)[0] is True


def test_campaign_banned_terms():
    c = _creator(campaign={"banned_terms": ["giveaway"]})
    ok, why = check_campaign({"meta": {"caption": "free giveaway now"}}, c)
    assert ok is False and "giveaway" in why


def test_campaign_narrows_creator_platforms():
    c = _creator(platforms=["youtube", "instagram", "tiktok"],
                 campaign={"allowed_platforms": ["instagram"]})
    assert platforms_for(c) == ["instagram"]


def test_no_platform_config_means_all():
    assert not platforms_for(_creator())


# ---------------------------------------------------------------- yt chat

def test_youtube_chat_replay_parsing():
    lines = []
    for ms, txt in ((5000, "KEKW"), (5200, "CLIP IT"), (60000, "hello")):
        lines.append(json.dumps({"replayChatItemAction": {
            "actions": [{"addChatItemAction": {"item": {
                "liveChatTextMessageRenderer": {"message": {"runs": [{"text": txt}]}}}}}],
            "videoOffsetTimeMsec": str(ms)}}))
    p = Path(tempfile.mkdtemp()) / "c.live_chat.json"
    p.write_text("\n".join(lines), encoding="utf-8")
    msgs = parse(p)
    assert [m[0] for m in msgs] == [5.0, 5.2, 60.0]
    assert msgs[1][1] == "CLIP IT"


def test_chat_parser_survives_junk_lines():
    p = Path(tempfile.mkdtemp()) / "c.live_chat.json"
    p.write_text("not json\n\n{}\n", encoding="utf-8")
    assert parse(p) == []


def test_set_status_persists_extra_fields(tmp_path, monkeypatch):
    from cliper import queue
    monkeypatch.setattr(queue, "QUEUE_FILE", tmp_path / "q.json")
    queue.add({"id": "a", "creator": "c"})
    queue.set_status("a", "published", published_at=123, posts=[{"platform": "youtube", "url": "u"}])
    item = queue.list_items()[0]
    assert item["published_at"] == 123 and item["posts"][0]["url"] == "u"
    assert queue.posted_this_week("c", now=200) == 1
