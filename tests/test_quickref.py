"""Parsing the QA Quick Reference. The PDF is not ours to commit, so these
cover the line handling rather than the document."""

from __future__ import annotations

import threading
import time

import pytest

from prosuite_mcp import quickref
from prosuite_mcp.quickref import (
    QuickRefEntry,
    _join,
    descriptor_to_method,
    entries_from_lines,
    for_condition,
    load,
)


@pytest.mark.parametrize(
    ("descriptor", "method"),
    [
        ("QaMinLength", "qa_min_length"),
        ("QaGdbConstraintFactory", "qa_gdb_constraint_factory"),
        ("TrDissolve", "tr_dissolve"),
        ("IfAll", "if_all"),
    ],
)
def test_descriptor_maps_to_the_factory_method_name(descriptor, method):
    assert descriptor_to_method(descriptor) == method


def test_join_undoes_a_hyphenated_line_break():
    """The PDF hyphenates mid-word at the column edge, so naive joining gives
    'Op- tionally'."""
    assert (
        _join(["Op-", "tionally, parts are tested"]) == "Optionally, parts are tested"
    )


def test_join_keeps_a_hyphen_before_a_capital():
    assert _join(["see QA-", "Tools"]) == "see QA- Tools"


def test_entries_end_at_their_test_line():
    lines = [
        "No sliver polygons",
        "Finds elongated polygons.",
        "Test: QaSliverPolygon",
        "Minimum and maximum dimensions",
        "Finds short lines.",
        "Tests: QaMinLength, QaMaxLength",
    ]

    entries = entries_from_lines(lines, "Geometry")

    assert entries == [
        QuickRefEntry(
            title="No sliver polygons",
            description="Finds elongated polygons.",
            category="Geometry",
            descriptors=["QaSliverPolygon"],
        ),
        QuickRefEntry(
            title="Minimum and maximum dimensions",
            description="Finds short lines.",
            category="Geometry",
            descriptors=["QaMinLength", "QaMaxLength"],
        ),
    ]


@pytest.mark.parametrize("anchor", ["Test", "Tests", "Transformer", "Issue Filter"])
def test_every_anchor_the_document_uses_is_recognized(anchor):
    """Conditions, transformers and issue filters announce themselves
    differently; missing one drops that whole section."""
    lines = ["A title", "A description.", f"{anchor}: TrDissolve"]

    (entry,) = entries_from_lines(lines, "Geometry")

    assert entry.descriptors == ["TrDissolve"]


def test_a_test_line_with_nothing_above_it_is_not_an_entry():
    """A section can continue across a page break, leaving a stray anchor."""
    assert entries_from_lines(["Test: QaMinLength"], "Geometry") == []


def test_prose_colons_are_not_mistaken_for_anchors():
    lines = ["A title", "Supported geometry types: polylines", "Test: TrDissolve"]

    (entry,) = entries_from_lines(lines, "Geometry")

    assert entry.description == "Supported geometry types: polylines"


def test_for_condition_ignores_the_arity_suffix(monkeypatch):
    """The Quick Reference names the test, not prosuite's overloads, so every
    arity of one test shares an entry."""
    entry = QuickRefEntry("Minimum dimensions", "Finds short lines.", "Geometry")
    monkeypatch.setattr("prosuite_mcp.quickref.load", lambda: {"qa_min_length": entry})

    assert for_condition("qa_min_length_0") is entry
    assert for_condition("qa_min_length_1") is entry


def test_a_lookup_does_not_wait_for_the_download(monkeypatch):
    """The window this closes is a real one: a client asking for a condition
    while the startup fetch is still running would otherwise wait out the
    network timeout for what is a local catalog operation."""
    still_downloading = threading.Event()
    monkeypatch.setattr(
        "prosuite_mcp.quickref._fetch",
        lambda: (still_downloading.wait(timeout=10), b"")[1],
    )

    started = time.monotonic()
    assert for_condition("qa_min_length_1") is None
    elapsed = time.monotonic() - started
    still_downloading.set()

    assert elapsed < 1, f"lookup blocked for {elapsed:.1f}s"


def _settle() -> None:
    """Wait out the background load the lookups started."""
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=5)


def test_the_document_is_fetched_once_however_many_lookups(monkeypatch):
    """Every condition in a listing calls for_condition, so a fetch per miss
    would mean hundreds of them."""
    calls = []
    monkeypatch.setattr(
        "prosuite_mcp.quickref._fetch", lambda: (calls.append(1), b"")[1]
    )

    for _ in range(5):
        for_condition("qa_min_length_1")
    _settle()

    assert calls == [1]


def test_an_unreachable_quick_reference_costs_only_the_descriptions(monkeypatch):
    """It enriches the catalog rather than gating it, so an air-gapped host
    still gets working tools."""

    def unreachable():
        raise OSError("Network is unreachable")

    monkeypatch.setattr("prosuite_mcp.quickref._fetch", unreachable)

    assert load() == {}


def test_a_response_that_is_not_a_pdf_is_survived(monkeypatch):
    monkeypatch.setattr("prosuite_mcp.quickref._fetch", lambda: b"404 Not Found")

    assert load() == {}
