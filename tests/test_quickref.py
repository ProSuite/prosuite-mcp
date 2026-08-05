"""Parsing the QA Quick Reference. The PDF is not ours to commit, so these
cover the line handling; PROSUITE_QUICKREF points the loader at a real one."""

from __future__ import annotations

import pytest

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


def test_an_unreachable_quick_reference_costs_only_the_descriptions(monkeypatch):
    """It enriches the catalog rather than gating it, so an air-gapped host
    still gets working tools."""

    def unreachable(source):
        raise OSError("Network is unreachable")

    monkeypatch.setattr("prosuite_mcp.quickref._fetch", unreachable)

    assert load() == {}


def test_a_source_that_is_not_a_pdf_is_survived(monkeypatch, tmp_path):
    junk = tmp_path / "not.pdf"
    junk.write_text("this is not a pdf")
    monkeypatch.setenv("PROSUITE_QUICKREF", str(junk))

    assert load() == {}
