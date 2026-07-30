"""Run ProSuite verifications over gRPC and shape the results.

Pure "call ProSuite, aggregate the stream, summarize", no MCP/FastMCP
concerns, so this is testable with a fake Service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import grpc
from prosuite import EnvelopePerimeter, Service
from prosuite.data_model import Dataset, Model
from prosuite.quality import Specification, XmlSpecification
from prosuite.verification import ServiceStatus, VerifiedSpecification

from .authoring import _build_condition
from .config import load_config
from .schemas import ConditionRequest, DatasetRef


def _make_run_dir(name: str, base: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    safe = re.sub(r"[^\w-]", "_", name)
    path = base / f"{ts}_{safe}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_service() -> Service:
    cfg = load_config()
    if cfg.ssl_cert_path:
        with open(cfg.ssl_cert_path, "rb") as f:
            creds = grpc.ssl_channel_credentials(f.read())
        return Service(cfg.host, cfg.port, creds)
    return Service(cfg.host, cfg.port)


def _decode_issue(issue: Any) -> dict[str, Any]:
    """Decode a streamed issue object into a plain, JSON-serializable dict."""
    return {
        "issue_code": issue.issue_code,
        "description": issue.description,
        "allowable": issue.allowable,
        "involved": [
            {"table_name": t.table_name, "object_ids": list(t.object_ids)}
            for t in issue.involved_objects
        ],
    }


_SAMPLE_CAP = 10


@dataclass
class StreamOutcome:
    """Bounded aggregate of a verification issue stream.

    Memory is O(distinct codes + distinct tables + sample cap), independent of
    the number of issues, so large runs (100k+ issues) do not blow up memory.
    The authoritative full record stays in the server-side Issues.gdb.
    """

    total: int = 0
    errors: int = 0  # issue.allowable is False (hard errors)
    warnings: int = 0  # issue.allowable is True (allowed / soft)
    counts_by_code: dict[str, int] = field(default_factory=dict)
    counts_by_table: dict[str, int] = field(default_factory=dict)
    counts_by_condition: dict[int | None, int] = field(default_factory=dict)
    sample: list[dict[str, Any]] = field(default_factory=list)
    # What the service said when it gave up, e.g. why it rejected a spec.
    failure_messages: list[str] = field(default_factory=list)


def _run_verify(
    service: Service,
    spec: Specification | XmlSpecification,
    output_dir: str,
    perimeter,
) -> tuple[StreamOutcome, VerifiedSpecification | None]:
    """Consume the verification stream once, aggregating into a StreamOutcome.

    Returns (outcome, final verified spec). Individual issues are tallied and
    dropped; only a bounded sample is retained.
    """
    outcome = StreamOutcome()
    verified_spec = None
    for response in service.verify(spec, perimeter=perimeter, output_dir=output_dir):
        if response.service_call_status == ServiceStatus.status_4 and response.message:
            outcome.failure_messages.append(response.message)
        for issue in response.issues:
            outcome.total += 1
            if issue.allowable:
                outcome.warnings += 1
            else:
                outcome.errors += 1
            code = issue.issue_code
            outcome.counts_by_code[code] = outcome.counts_by_code.get(code, 0) + 1
            if not issue.allowable:
                cid = issue.condition_id
                outcome.counts_by_condition[cid] = (
                    outcome.counts_by_condition.get(cid, 0) + 1
                )
            for t in issue.involved_objects:
                name = t.table_name
                outcome.counts_by_table[name] = outcome.counts_by_table.get(name, 0) + 1
            if len(outcome.sample) < _SAMPLE_CAP:
                outcome.sample.append(_decode_issue(issue))
        if response.verified_specification is not None:
            verified_spec = response.verified_specification
    return outcome, verified_spec


def _failure_reason(outcome: StreamOutcome) -> str:
    """Why the run produced no summary, in the service's own words if it said."""
    if outcome.failure_messages:
        return " ".join(outcome.failure_messages)
    return "Verification stream ended without a final summary."


def _summarize(spec: VerifiedSpecification, outcome: StreamOutcome) -> dict[str, Any]:
    # Every issue's condition_id is expected to match one of spec's verified
    # conditions. unmatched_condition_errors is normally 0; a nonzero value
    # means some stream errors couldn't be attributed to a known condition
    # (e.g. verified_conditions is incomplete), so
    # sum(conditions[*].errors) + unmatched_condition_errors == total_errors
    # always, even when the breakdown itself is short some counts.
    known_ids = {c.condition_id for c in spec.verified_conditions}
    matched_errors = sum(
        n for cid, n in outcome.counts_by_condition.items() if cid in known_ids
    )
    unmatched_errors = outcome.errors - matched_errors

    return {
        "engine_confirmed": True,
        "specification_name": spec.specification_name,
        "user_name": spec.user_name,
        "total_conditions": spec.verified_conditions_count,
        "total_errors": outcome.errors,
        "total_warnings": outcome.warnings,
        "issues_seen_in_stream": outcome.total,
        "issue_counts_by_code": outcome.counts_by_code,
        "issue_counts_by_table": outcome.counts_by_table,
        "unmatched_condition_errors": unmatched_errors,
        "sample_features": outcome.sample,
        "conditions": [
            {
                "name": c.name or f"condition_{c.condition_id}",
                # Same allowable-is-False tally as total_errors, keyed by
                # condition_id, so the two are always consistent by
                # construction instead of relying on the engine's separate
                # error_count field (whose warning-inclusion semantics are
                # not guaranteed to match the stream's allowable distinction).
                "errors": outcome.counts_by_condition.get(c.condition_id, 0),
            }
            for c in spec.verified_conditions
        ],
    }


def _run_verification_impl(
    model_catalog_path: str,
    model_name: str,
    datasets: list[DatasetRef],
    conditions: list[ConditionRequest],
    output_dir: str | None,
    envelope: dict[str, float] | None,
    run_dir_prefix: str,
) -> dict[str, Any]:
    try:
        model = Model(model_name, model_catalog_path)
        dataset_map: dict[str, Dataset] = {
            ds.name: Dataset(ds.name, model, ds.filter_expression) for ds in datasets
        }

        spec = Specification(name="prosuite-mcp verification")
        for cond_req in conditions:
            spec.add_condition(_build_condition(cond_req, dataset_map))
    except ValueError as exc:
        return {"status": "error", "engine_confirmed": False, "error": str(exc)}

    perimeter = None
    if envelope:
        perimeter = EnvelopePerimeter(
            x_min=envelope["x_min"],
            y_min=envelope["y_min"],
            x_max=envelope["x_max"],
            y_max=envelope["y_max"],
        )

    if output_dir is None:
        output_dir = str(_make_run_dir(run_dir_prefix, Path.cwd() / "runs"))

    service = _make_service()

    try:
        outcome, verified_spec = _run_verify(service, spec, output_dir, perimeter)
    except grpc.RpcError as exc:
        return {
            "status": "error",
            "engine_confirmed": False,
            "error": f"gRPC {exc.code()}: {exc.details()}",
        }

    if verified_spec is None:
        return {
            "status": "error",
            "engine_confirmed": False,
            "error": _failure_reason(outcome),
            "issues_seen_in_stream": outcome.total,
        }

    summary = _summarize(verified_spec, outcome)
    summary["status"] = "success"
    summary["output_dir"] = output_dir
    return summary


def run_xml_verification_impl(
    spec_path: str,
    specification_name: str,
    replacements: list[list[str]],
    output_dir: str | None,
    envelope: dict[str, float] | None,
) -> dict[str, Any]:
    try:
        xml_spec = XmlSpecification(spec_path, specification_name, replacements)
    except Exception as exc:
        return {
            "status": "error",
            "engine_confirmed": False,
            "error": f"Failed to load spec: {exc}",
        }

    perimeter = None
    if envelope:
        perimeter = EnvelopePerimeter(
            x_min=envelope["x_min"],
            y_min=envelope["y_min"],
            x_max=envelope["x_max"],
            y_max=envelope["y_max"],
        )

    if output_dir is None:
        output_dir = str(_make_run_dir(specification_name, Path.cwd() / "runs"))

    service = _make_service()

    try:
        outcome, verified_spec = _run_verify(service, xml_spec, output_dir, perimeter)
    except grpc.RpcError as exc:
        return {
            "status": "error",
            "engine_confirmed": False,
            "error": f"gRPC {exc.code()}: {exc.details()}",
        }

    if verified_spec is None:
        return {
            "status": "error",
            "engine_confirmed": False,
            "error": _failure_reason(outcome),
            "issues_seen_in_stream": outcome.total,
        }

    summary = _summarize(verified_spec, outcome)
    summary["status"] = "success"
    summary["output_dir"] = output_dir
    return summary
