"""Runs real .qa.xml files through load_spec, describe_spec, and the
search_spec to run_verification handoff, up to the serialized gRPC request.

Skipped unless PROSUITE_SPEC_CORPUS points at a directory of real specs, which
are not committed: they belong to customers, not to this repo.

    PROSUITE_SPEC_CORPUS=<dir> uv run pytest tests/test_spec_corpus.py -v -s

Other test modules use fixtures we wrote, so they only prove the code does what
we assumed. This measures what real specs contain.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from prosuite import Service
from prosuite.data_model import Dataset, Model
from prosuite.quality import Specification
from prosuite.verification.advanced_parameters import AdvancedParameters

from prosuite_mcp.authoring import _build_condition
from prosuite_mcp.schemas import ConditionRequest
from prosuite_mcp.spec import get_spec_metadata, load_spec, search_spec

_CORPUS = os.environ.get("PROSUITE_SPEC_CORPUS")

pytestmark = pytest.mark.skipif(
    not _CORPUS,
    reason="set PROSUITE_SPEC_CORPUS to a directory of real .qa.xml files",
)

# A floor to raise as the handoff bugs get fixed, not a target. Now 0.94.
MIN_CLEAN_HANDOFF_RATE = 0.90

# Never resolves (RFC 2606). Nothing connects, but localhost would be a real
# ProSuite server on Windows.
_OFFLINE_HOST = "prosuite-mcp.invalid"


@dataclass
class CorpusStats:
    files: list[Path]
    conditions: int = 0
    built: int = 0
    clean: int = 0
    failures: Counter[str] = field(default_factory=Counter)
    load_errors: list[str] = field(default_factory=list)
    metadata_errors: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    specs_missing_workspaces: list[str] = field(default_factory=list)

    @property
    def clean_rate(self) -> float:
        return self.clean / self.conditions if self.conditions else 0.0


def _unique_spec_files(root: Path) -> list[Path]:
    """Deduplicate by content: demo sets tend to exist in several copies."""
    seen: set[str] = set()
    files: list[Path] = []
    for path in sorted(root.rglob("*.qa.xml")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            files.append(path)
    return files


def _classify(message: str) -> str:
    if message.startswith("Missing parameter"):
        return "missing param"
    if message.startswith("Unknown condition"):
        return "unknown condition"
    if "not found. Provided" in message:
        return "dataset not found"
    return "other"


def _report(stats: CorpusStats) -> str:
    lines = [
        "",
        f"spec corpus: {len(stats.files)} unique files",
        f"  conditions offered by search_spec : {stats.conditions}",
        f"  bind parameters                   : {stats.built}",
        f"  reach a serialized request        : {stats.clean} ({stats.clean_rate:.0%})",
    ]
    for reason, count in stats.failures.most_common():
        share = count / stats.conditions if stats.conditions else 0
        lines.append(f"    FAIL {reason:<20}: {count} ({share:.0%})")
    lines.append(
        f"  specs referencing conditions but reporting no workspace ids: "
        f"{len(stats.specs_missing_workspaces)}"
    )
    return "\n".join(lines)


@pytest.fixture(scope="module")
def corpus() -> CorpusStats:
    root = Path(os.path.expanduser(_CORPUS or ""))
    if not root.is_dir():
        pytest.skip(f"PROSUITE_SPEC_CORPUS is not a directory: {root}")

    stats = CorpusStats(files=_unique_spec_files(root))
    if not stats.files:
        pytest.skip(f"no .qa.xml files under {root}")

    service = Service(_OFFLINE_HOST, 5151)

    for path in stats.files:
        try:
            conditions = load_spec(str(path))
        except Exception as exc:
            stats.load_errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue

        try:
            for spec in get_spec_metadata(str(path))["specifications"]:
                if spec["condition_count"] and not spec["workspace_ids"]:
                    stats.specs_missing_workspaces.append(
                        f"{path.name}: {spec['specification_name']}"
                    )
        except Exception as exc:
            stats.metadata_errors.append(f"{path.name}: {type(exc).__name__}: {exc}")

        for result in search_spec(conditions, "", max_results=10_000)["results"]:
            stats.conditions += 1
            model = Model("corpus", "corpus")
            datasets = {
                d["name"]: Dataset(d["name"], model, d["filter_expression"])
                for d in result["required_datasets"]
            }
            try:
                condition = _build_condition(
                    ConditionRequest(**result["condition_request"]), datasets
                )
            except ValueError as exc:
                stats.failures[_classify(str(exc))] += 1
                continue
            except Exception as exc:
                stats.unexpected.append(f"{path.name}: {type(exc).__name__}: {exc}")
                continue
            stats.built += 1

            # Binding parameters is not the whole handoff: run_verification
            # also serializes the condition, so stopping short overstates it.
            specification = Specification(name="corpus")
            specification.add_condition(condition)
            try:
                service._compile_request(
                    AdvancedParameters(specification, "", None)
                ).SerializeToString()
                stats.clean += 1
            except Exception as exc:
                stats.failures[f"serialize: {type(exc).__name__}"] += 1
                stats.unexpected.append(
                    f"{path.name}: serializing {result['name']!r}: "
                    f"{type(exc).__name__}: {exc}"
                )

    print(_report(stats))
    return stats


def test_every_spec_file_loads(corpus: CorpusStats):
    assert corpus.load_errors == []


def test_metadata_never_raises(corpus: CorpusStats):
    assert corpus.metadata_errors == []


def test_rejected_conditions_fail_diagnosably(corpus: CorpusStats):
    """Anything but a ValueError means real input hit a path we missed."""
    assert corpus.unexpected == []


def test_specs_report_the_workspace_ids_they_need(corpus: CorpusStats):
    """Empty was the category-nesting bug, and it failed silently."""
    assert corpus.specs_missing_workspaces == []


def test_handoff_clean_rate_does_not_regress(corpus: CorpusStats):
    assert corpus.clean_rate >= MIN_CLEAN_HANDOFF_RATE, _report(corpus)
