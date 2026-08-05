import pytest

from prosuite_mcp import quickref


@pytest.fixture(autouse=True)
def _offline_quickref(monkeypatch):
    """No test reaches for the real Quick Reference. Left alone, the catalog
    tools would download it, which makes them slow, networked and dependent on
    a document nobody here controls. Tests that want it point PROSUITE_QUICKREF
    at a PDF themselves."""
    # Held, because a test may replace quickref.load with something that has
    # no cache, and monkeypatch only undoes that after this fixture tears down.
    loader = quickref.load
    loader.cache_clear()
    monkeypatch.setattr(quickref, "_fetch", lambda source: b"")
    yield
    loader.cache_clear()
