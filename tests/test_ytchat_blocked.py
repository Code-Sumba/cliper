import subprocess
from unittest import mock

import pytest

from cliper import ytchat
from cliper.live import is_done


def _fail(stderr):
    return mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "yt-dlp", stderr=stderr))


def test_bot_check_raises_blocked(tmp_path):
    with _fail(b"ERROR: [youtube] x: Sign in to confirm you're not a bot."):
        with pytest.raises(ytchat.Blocked):
            ytchat.download_chat("u", tmp_path, "k")


def test_other_errors_mean_no_replay(tmp_path):
    with _fail(b"ERROR: Video unavailable"):
        assert ytchat.download_chat("u", tmp_path, "k") is None


def test_failed_streams_retry_until_limit():
    assert not is_done(None)
    assert is_done({"status": "harvested"})
    assert not is_done({"status": "failed", "attempts": 1})
    assert is_done({"status": "failed", "attempts": 3})
