import pytest


@pytest.fixture(autouse=True)
def no_network_autocomplete(monkeypatch):
    """Unit tests must not depend on YouTube autocomplete being reachable."""
    from cliper import seo
    seo.suggest.cache_clear()
    monkeypatch.setattr(seo, "suggest", lambda q: ())
