import pytest

from prosuite_mcp import quickref


@pytest.fixture(autouse=True)
def _offline_quickref(monkeypatch):
    """No test reaches for the real Quick Reference. Left alone, the catalog
    tools would download it, which makes them slow, networked and dependent on
    a document nobody here controls."""
    monkeypatch.setattr(quickref, "_fetch", lambda: b"")
    monkeypatch.setattr(quickref, "_entries", None)
    monkeypatch.setattr(quickref, "_started", False)
    yield
