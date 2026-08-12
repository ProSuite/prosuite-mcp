"""Run ProSuite verifications over gRPC and shape the results.

Pure "call ProSuite, aggregate the stream, summarize", no MCP/FastMCP
concerns, so this is testable with a fake Service.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

import grpc
from prosuite import EnvelopePerimeter, Service
from prosuite.data_model import Dataset, Model
from prosuite.quality import Specification, XmlSpecification
from prosuite.verification import MessageLevel, ServiceStatus, VerifiedSpecification

from .authoring import build_condition
from .config import load_config
from .schemas import ConditionRequest, DatasetRef


def _run_dir_name(name: str) -> str:
    # Token as well as a timestamp: two runs can share a second.
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    safe = re.sub(r"[^\w-]", "_", name)
    return f"{ts}_{token_hex(4)}_{safe}"


_RUN_DIR_ATTEMPTS = 5


def _make_run_dir(name: str, base: Path) -> Path:
    """Reserve a run directory, rather than trust the name to be unique.

    mkdir is atomic, so creating exclusively is what makes the directory ours;
    exist_ok would hand the same one to two runs and put them back in the
    Issues.gdb conflict.
    """
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(_RUN_DIR_ATTEMPTS):
        path = base / _run_dir_name(name)
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path
    raise RuntimeError(f"No free run directory under {base} after {_RUN_DIR_ATTEMPTS}")


def _run_subdir(base: str, name: str) -> str:
    """A run of its own under a caller-supplied output_dir, so a reused one
    does not collide on an Issues.gdb the service will not overwrite.

    Only a local service shares our filesystem, so only there can the
    directory be reserved. Remote we name it and the service creates it, where
    a repeated name would fail the run loudly rather than merge two of them.
    """
    if _service_is_local():
        return str(_make_run_dir(name, Path(base)))
    sep = "\\" if "\\" in base and "/" not in base else "/"
    return base.rstrip("/\\") + sep + _run_dir_name(name)


def _service_is_local() -> bool:
    """Whether the service shares our filesystem, so a path we invent means
    something to it. Loopback in any spelling: all of 127.0.0.0/8, ::1 with or
    without gRPC's brackets, or a localhost name (RFC 6761). A hostname we
    cannot classify counts as remote rather than resolving it, since a DNS
    lookup here can hang and need not match how gRPC resolves the same name.
    """
    host = load_config().host.strip().lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


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
_MESSAGE_CAP = 50

_DIAGNOSTIC_LEVELS = frozenset(
    {MessageLevel.level_110000, MessageLevel.level_70000, MessageLevel.level_60000}
)
_TILE_PROGRESS_RE = re.compile(
    r"\bProcessing\s+tile\s+(\d+)\s+of\s+(\d+)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class ProgressEvent:
    """MCP-neutral snapshot of one response from the verification stream.

    The prosuite package owns its protobuf wrapper types. Keeping only scalar
    values here makes the MCP boundary stable and deliberately prevents the
    raw, potentially high-volume stream from becoming tool-result context.
    """

    overall_current: int | None = None
    overall_total: int | None = None
    detailed_current: int | None = None
    detailed_total: int | None = None
    progress_type: int | None = None
    progress_step: int | None = None
    processing_step_message: str = ""
    message: str = ""
    message_level: str | None = None
    current_box: dict[str, float] | None = None
    total_box: dict[str, float] | None = None

    def counter(self) -> tuple[int | None, int | None]:
        """Return the best domain counter carried by this event.

        Older ProSuite services expose progress only in messages such as
        ``Processing tile 516 of 1368`` while the protobuf counter fields stay
        at their default zero values. Keep that compatibility parsing here at
        the service boundary so MCP transport code does not need to understand
        ProSuite message formats.
        """
        if self.overall_current is not None and self.overall_total is not None:
            if self.overall_total > 0:
                return self.overall_current, self.overall_total
        if self.detailed_current is not None and self.detailed_total is not None:
            if self.detailed_total > 0:
                return self.detailed_current, self.detailed_total

        for text in (self.processing_step_message, self.message):
            match = _TILE_PROGRESS_RE.search(text)
            if match is not None:
                current, total = (int(value) for value in match.groups())
                return (current, total) if total > 0 else (None, None)
        return None, None

    @classmethod
    def from_response(cls, response: Any) -> "ProgressEvent | None":
        """Normalize SDK progress, while remaining compatible with SDK < 1.8."""
        progress = getattr(response, "progress", None)
        response_message = getattr(response, "message", "")
        response_level = getattr(response, "message_level", None)
        if progress is None:
            return (
                cls(message=response_message, message_level=response_level)
                if response_message
                else None
            )

        def box_as_dict(box: Any) -> dict[str, float] | None:
            if box is None:
                return None
            return {
                "x_min": box.x_min,
                "y_min": box.y_min,
                "x_max": box.x_max,
                "y_max": box.y_max,
            }

        return cls(
            overall_current=getattr(progress, "overall_progress_current_step", None),
            overall_total=getattr(progress, "overall_progress_total_steps", None),
            detailed_current=getattr(progress, "detailed_progress_current_step", None),
            detailed_total=getattr(progress, "detailed_progress_total_steps", None),
            progress_type=getattr(progress, "progress_type", None),
            progress_step=getattr(progress, "progress_step", None),
            processing_step_message=getattr(progress, "processing_step_message", ""),
            message=getattr(progress, "message", "") or response_message,
            message_level=getattr(progress, "message_level", None) or response_level,
            current_box=box_as_dict(getattr(progress, "current_box", None)),
            total_box=box_as_dict(getattr(progress, "total_box", None)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall_current": self.overall_current,
            "overall_total": self.overall_total,
            "detailed_current": self.detailed_current,
            "detailed_total": self.detailed_total,
            "progress_type": self.progress_type,
            "progress_step": self.progress_step,
            "processing_step_message": self.processing_step_message,
            "message": self.message,
            "message_level": self.message_level,
            "current_box": self.current_box,
            "total_box": self.total_box,
        }


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
    # Warnings and worse from anywhere in the run, not just its end.
    service_messages: list[dict[str, str]] = field(default_factory=list)
    # A rejection and a cancelled run both end without a summary. Only this
    # separates them.
    last_status: str | None = None
    # Kept for diagnosis and final tool output; raw events are never retained.
    last_progress: ProgressEvent | None = None
    progress_events_seen: int = 0


def _run_verify(
    service: Service,
    spec: Specification | XmlSpecification,
    output_dir: str,
    perimeter,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> tuple[StreamOutcome, VerifiedSpecification | None]:
    """Consume the verification stream once, aggregating into a StreamOutcome.

    Returns (outcome, final verified spec). Individual issues are tallied and
    dropped; only a bounded sample is retained.

    on_progress sees every normalized progress snapshot as it arrives. The
    caller decides what is worth forwarding to a human or MCP client.
    """
    outcome = StreamOutcome()
    verified_spec = None
    for response in service.verify(spec, perimeter=perimeter, output_dir=output_dir):
        outcome.last_status = response.service_call_status
        event = ProgressEvent.from_response(response)
        if event is not None:
            outcome.last_progress = event
            outcome.progress_events_seen += 1
            if on_progress:
                on_progress(event)

        message = event.message if event is not None else response.message
        level = event.message_level if event is not None else response.message_level
        if response.service_call_status == ServiceStatus.status_4 and message:
            outcome.failure_messages.append(message)
        if message and level in _DIAGNOSTIC_LEVELS:
            if len(outcome.service_messages) < _MESSAGE_CAP:
                outcome.service_messages.append({"level": level, "message": message})
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
    """Why the run produced no summary, in the service's own words if it said.

    It often does not. Named causes are leads we have observed, not diagnoses.
    """
    if outcome.failure_messages:
        return " ".join(outcome.failure_messages)
    if outcome.service_messages:
        # The failing response is often empty; the warning came earlier.
        return " ".join(m["message"] for m in outcome.service_messages)
    if outcome.last_status == ServiceStatus.status_4:
        return (
            "The service rejected the run and sent no reason. One known cause "
            "is a dataset name that is not in the workspace."
        )
    if outcome.last_status == ServiceStatus.status_2:
        return (
            f"The service cancelled the run after streaming {outcome.total} "
            f"issue(s) and sent no final summary, so those issues are not a "
            f"complete result. One known cause is a field name that does not "
            f"exist in a condition's constraint expression."
        )
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
        "service_messages": outcome.service_messages,
        "warnings": [
            message
            for message in outcome.service_messages
            if message["level"] == MessageLevel.level_60000
        ],
        "errors": [
            message
            for message in outcome.service_messages
            if message["level"] in {MessageLevel.level_70000, MessageLevel.level_110000}
        ],
        "last_progress": (
            outcome.last_progress.as_dict() if outcome.last_progress else None
        ),
        "progress_events_seen": outcome.progress_events_seen,
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


def _verify_and_summarize(
    spec: Specification | XmlSpecification,
    run_name: str,
    output_dir: str | None,
    envelope: dict[str, float] | None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> dict[str, Any]:
    """Run a built specification and shape the result.

    Ad-hoc and XML verification differ only in how they build the spec object;
    everything from here on is the same for both.
    """
    perimeter = None
    if envelope:
        perimeter = EnvelopePerimeter(
            x_min=envelope["x_min"],
            y_min=envelope["y_min"],
            x_max=envelope["x_max"],
            y_max=envelope["y_max"],
        )

    if output_dir is None:
        # ProSuite resolves this path on its own machine, so a local runs/ dir
        # is only useful when that machine is this one. Empty means the service
        # writes no Issues.gdb or report.
        output_dir = (
            str(_make_run_dir(run_name, Path.cwd() / "runs"))
            if _service_is_local()
            else ""
        )
    elif output_dir:
        # A base to run under, not the run directory. Empty asks for no output.
        output_dir = _run_subdir(output_dir, run_name)

    service = _make_service()

    try:
        outcome, verified_spec = _run_verify(
            service, spec, output_dir, perimeter, on_progress
        )
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
            # Lets a caller branch on the two without parsing the prose.
            "service_status": outcome.last_status,
            "service_messages": outcome.service_messages,
            "warnings": [
                message
                for message in outcome.service_messages
                if message["level"] == MessageLevel.level_60000
            ],
            "errors": [
                message
                for message in outcome.service_messages
                if message["level"]
                in {MessageLevel.level_70000, MessageLevel.level_110000}
            ],
            "last_progress": (
                outcome.last_progress.as_dict() if outcome.last_progress else None
            ),
            "progress_events_seen": outcome.progress_events_seen,
            # A run that failed mid-stream often explains itself here, in the
            # service's own words, where _failure_reason can only guess.
            "sample_features": outcome.sample,
        }

    summary = _summarize(verified_spec, outcome)
    summary["status"] = "success"
    summary["output_dir"] = output_dir
    return summary


def run_verification_impl(
    model_catalog_path: str,
    model_name: str,
    datasets: list[DatasetRef],
    conditions: list[ConditionRequest],
    output_dir: str | None,
    envelope: dict[str, float] | None,
    run_dir_prefix: str,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> dict[str, Any]:
    try:
        model = Model(model_name, model_catalog_path)
        dataset_map: dict[str, Dataset] = {
            ds.name: Dataset(ds.name, model, ds.filter_expression) for ds in datasets
        }

        spec = Specification(name="prosuite-mcp verification")
        for cond_req in conditions:
            spec.add_condition(build_condition(cond_req, dataset_map))
    except ValueError as exc:
        return {"status": "error", "engine_confirmed": False, "error": str(exc)}

    return _verify_and_summarize(
        spec, run_dir_prefix, output_dir, envelope, on_progress
    )


def run_xml_verification_impl(
    spec_path: str,
    specification_name: str,
    replacements: list[list[str]],
    output_dir: str | None,
    envelope: dict[str, float] | None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> dict[str, Any]:
    try:
        xml_spec = XmlSpecification(spec_path, specification_name, replacements)
    except Exception as exc:
        return {
            "status": "error",
            "engine_confirmed": False,
            "error": f"Failed to load spec: {exc}",
        }

    return _verify_and_summarize(
        xml_spec, specification_name, output_dir, envelope, on_progress
    )
