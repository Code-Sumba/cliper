import json
from unittest import mock

import pytest

from cliper import config, creators, discover


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "DISCOVERY_FILE", tmp_path / "discovery.json")
    monkeypatch.setattr(discover, "EXCLUDE_FILE", tmp_path / "exclude.txt")
    monkeypatch.setattr(discover, "AUTO_CREATORS_FILE", tmp_path / "auto.json")
    monkeypatch.setattr(config, "AUTO_CREATORS_FILE", tmp_path / "auto.json")
    monkeypatch.setattr(creators, "EXCLUDE_FILE", tmp_path / "exclude.txt")
    yml = tmp_path / "creators.yml"
    yml.write_text(
        "defaults:\n  post_per_week: 3\n\ncreators:\n"
        "  - handle: manual1\n    display_name: Manual One\n    twitch: manualone\n"
        "    permission: none\n\n# trailing comment block\n#  - handle: example\n",
        encoding="utf-8")
    monkeypatch.setattr(config, "CREATORS_FILE", yml)
    monkeypatch.setattr(creators, "CREATORS_FILE", yml)
    return tmp_path


def db_with(now, rows):
    """rows: (platform, id, name, game, [viewers per 2h sample, newest last])"""
    db = {"channels": {}, "last_youtube": 0, "last_promote": ""}
    for plat, cid, name, game, views in rows:
        n = len(views)
        db["channels"][f"{plat}:{cid}"] = {
            "platform": plat, "id": cid, "name": name, "game": game, "url": "u", "title": "",
            "live_now": views[-1], "last_seen": now,
            "samples": [[now - (n - 1 - i) * 7200, v] for i, v in enumerate(views)],
        }
    return db


def test_hours_watched_beats_short_peak():
    now = 1_800_000_000
    db = db_with(now, [
        ("twitch", "marathon", "Marathon", "Just Chatting", [20000] * 4),   # 8h at 20k
        ("twitch", "spike", "Spike", "Just Chatting", [60000]),            # 2h at 60k
    ])
    with mock.patch.object(discover, "_now", return_value=now):
        board = discover.leaderboard(db)
    assert [r["id"] for r in board] == ["marathon", "spike"]
    assert board[0]["hours_watched_24h"] == 160000


def test_brand_channels_detected():
    assert discover.is_brand({"id": "rocketleague", "name": "RocketLeague", "game": "Rocket League"})
    assert discover.is_brand({"id": "pokemongo", "name": "PokemonGO", "game": "Pokémon GO"})
    assert discover.is_brand({"id": "warthunder_esports", "name": "W", "game": "War Thunder"})
    assert not discover.is_brand({"id": "kaicenat", "name": "KaiCenat", "game": "Grand Theft Auto VI"})


def test_promote_adds_top_skips_listed_brand_blocked_and_caps(tmp_state, monkeypatch):
    now = 1_800_000_000
    db = db_with(now, [
        ("twitch", "manualone", "Manual One", "x", [90000]),     # already listed
        ("twitch", "rocketleague", "RocketLeague", "Rocket League", [80000]),
        ("twitch", "blocked1", "Blocked1", "x", [70000]),
        ("twitch", "novods", "NoVods", "x", [60000]),
        ("twitch", "alpha", "Alpha", "x", [50000]),
        ("twitch", "beta", "Beta", "x", [40000]),
        ("twitch", "gamma", "Gamma", "x", [30000]),
        ("twitch", "tiny", "Tiny", "x", [10]),
    ])
    discover.save_db(db)
    (tmp_state / "exclude.txt").write_text("blocked1\n", encoding="utf-8")
    monkeypatch.setenv("DISCOVER_ADD_PER_DAY", "2")
    with mock.patch.object(discover, "_now", return_value=now), \
         mock.patch.object(discover, "_twitch_has_vods", side_effect=lambda login: login != "novods"):
        added = discover.promote()
        assert added == ["Alpha", "Beta"]
        assert discover.promote() == []                     # once per day
    names = [c.handle for c in config.load_creators()]
    assert names == ["manual1", "alpha", "beta"]
    auto = [c for c in config.load_creators() if c.source == "auto"]
    assert all(c.permission == "none" and c.vods_per_check == 1 for c in auto)


def test_issue_form_parsing():
    body = ("### Action\n\nAdd\n\n### Platform\n\ntwitch\n\n### Channel\n\nhttps://twitch.tv/xQc\n\n"
            "### Permission\n\nnone\n\n### Permission note\n\n_No response_\n\n### Posts per week\n\n5\n\n"
            "### Post to\n\nyoutube, instagram")
    f = creators.parse_issue(body)
    assert f["channel"] == "https://twitch.tv/xQc" and f["permission note"] == "" and f["posts per week"] == "5"


def test_add_and_remove_keep_file_valid(tmp_state):
    original = config.CREATORS_FILE.read_text(encoding="utf-8")
    with mock.patch.object(creators, "resolve_twitch", return_value=("xqc", "xQc")):
        msg = creators.add("twitch", "xqc", post_per_week=5, platforms=["youtube"])
        assert "xQc" in msg
        with pytest.raises(creators.CreatorError):
            creators.add("twitch", "xqc")                  # duplicate
    c = [c for c in config.load_creators() if c.handle == "xqc"][0]
    assert c.post_per_week == 5 and c.platforms == ["youtube"] and c.permission == "none"
    creators.remove("xqc")
    assert config.CREATORS_FILE.read_text(encoding="utf-8") == original
    with pytest.raises(creators.CreatorError):
        creators.add("kick", "someone")


def test_remove_auto_blocks_it(tmp_state):
    discover.save_auto([{"key": "twitch:alpha", "handle": "alpha", "display_name": "Alpha",
                         "twitch": "alpha", "youtube_channel_id": None}])
    creators.remove("alpha")
    assert discover.load_auto() == []
    assert "alpha" in discover.excluded()
