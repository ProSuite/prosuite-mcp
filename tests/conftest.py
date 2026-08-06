import pytest

from prosuite_mcp import quickref


def await_quickref_loader() -> None:
    """Wait out the background load, if one is running."""
    worker = quickref._worker
    if worker is not None:
        worker.join(timeout=5)


@pytest.fixture(autouse=True)
def _offline_quickref(monkeypatch):
    """No test reaches for the real Quick Reference. Left alone, the catalog
    tools would download it, which makes them slow, networked and dependent on
    a document nobody here controls."""
    monkeypatch.setattr(quickref, "_fetch", lambda: b"")
    monkeypatch.setattr(quickref, "_entries", None)
    monkeypatch.setattr(quickref, "_started", False)
    monkeypatch.setattr(quickref, "_worker", None)
    yield
    # Before monkeypatch restores the module: a loader still running would
    # otherwise write this test's result into the next one's state.
    await_quickref_loader()
