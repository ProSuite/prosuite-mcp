from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import grpc
from mcp.server.fastmcp import FastMCP
from prosuite import EnvelopePerimeter, Service
from prosuite.data_model import Dataset, Model
from prosuite.factories.quality_conditions import Conditions
from prosuite.quality import Specification, XmlSpecification
from prosuite.verification import VerifiedSpecification
from pydantic import BaseModel

from .catalog import CATALOG, ParamInfo
from .config import load_config
from .spec import _NS, SpecCondition, get_spec_metadata
from .spec import load_spec as _load_spec
from .spec import search_spec as _search_spec

mcp = FastMCP(
    "ProSuite MCP",
    instructions="MCP server for Dira ProSuite quality verification",
)


class DatasetRef(BaseModel):
    name: str
    filter_expression: str = ""


class ConditionRequest(BaseModel):
    condition: str
    params: dict[str, Any] = {}


class WorkspaceReplacement(BaseModel):
    workspace_id: str
    workspace_path: str


def _make_run_dir(name: str, base: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    safe = re.sub(r"[^\w-]", "_", name)
    path = base / f"{ts}_{safe}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@mcp.tool()
def list_conditions(search: str = "") -> str:
    """
    List available ProSuite quality conditions.

    Returns condition names and their one-line description. Use search to
    filter by keyword (matched against name and description). Pass a result
    name to describe_condition to get full parameter details before building
    a run_verification call.
    """
    query = search.lower()
    results = []
    for name, info in sorted(CATALOG.items()):
        if query and query not in name and query not in info.docstring.lower():
            continue
        first_line = info.docstring.split("\n")[0] if info.docstring else ""
        results.append(f"{name}: {first_line}")

    if not results:
        return f"No conditions match {search!r}."
    return "\n".join(results)


@mcp.tool()
def describe_condition(name: str) -> str:
    """
    Describe the parameters of a ProSuite quality condition.

    Returns the full docstring and parameter list with types. Dataset
    parameters expect a dataset name string (must match a name in the
    datasets list you will pass to run_verification). Primitive parameters
    take their direct value (number, bool, string).
    """
    info = CATALOG.get(name)
    if info is None:
        close = [n for n in CATALOG if name.lower() in n.lower()][:5]
        hint = f" Similar names: {', '.join(close)}" if close else ""
        return f"Unknown condition: {name!r}.{hint} Use list_conditions to browse."

    lines = [f"condition: {info.method_name}", ""]
    if info.docstring:
        lines += [info.docstring, ""]

    lines.append("parameters:")
    for p in info.params:
        if p.is_dataset_list:
            kind = "list of dataset names"
        elif p.is_dataset:
            kind = "dataset name"
        else:
            kind = "value"
        lines.append(f"  {p.name} ({p.type_hint}) — {kind}")

    return "\n".join(lines)


@mcp.tool()
def describe_spec() -> dict:
    """
    Describe the loaded QA spec file: available specifications, workspace definitions,
    and per-specification summary of conditions, workspace IDs, and dataset names.

    Call this before run_xml_verification to learn:
    - Which specification_name values exist in the spec (pass one to run_xml_verification)
    - Which workspace_id values need to be replaced with real paths
    - Which datasets each specification expects (useful for sanity-checking the workspace)

    Requires PROSUITE_SPEC_PATH to be configured.
    """
    cfg = load_config()
    if not cfg.spec_path:
        return {
            "error": "No spec loaded. Set PROSUITE_SPEC_PATH to a .qa.xml file path."
        }
    try:
        return get_spec_metadata(cfg.spec_path)
    except Exception as exc:
        return {"error": f"Failed to read spec: {exc}"}


_spec_conditions: list[SpecCondition] | None = None


def _get_spec() -> list[SpecCondition] | None:
    global _spec_conditions
    if _spec_conditions is None:
        cfg = load_config()
        if cfg.spec_path:
            _spec_conditions = _load_spec(cfg.spec_path)
    return _spec_conditions


@mcp.tool()
def search_spec(query: str, max_results: int = 20) -> dict:
    """
    Search the loaded QA spec for conditions matching a natural-language query.

    Returns up to max_results conditions whose name, description, or category
    contains the query string (case-insensitive). Claude bridges any language
    gap — queries in English, German, French, or Italian all work.

    Each result includes:
    - name: the full condition name (human-readable rule statement)
    - category: domain grouping from the spec
    - allow_errors: False means a hard failure, True means tolerated
    - condition_request: ready to pass directly into run_verification's
      conditions list (includes condition method name and pre-filled params)
    - required_datasets: dataset names and filter expressions to include in
      run_verification's datasets list

    Requires PROSUITE_SPEC_PATH to be configured. Returns an error dict if
    no spec is loaded.
    """
    conditions = _get_spec()
    if conditions is None:
        return {
            "error": "No spec loaded. Set PROSUITE_SPEC_PATH or call load_spec first."
        }
    return _search_spec(conditions, query, max_results=max_results)


@mcp.tool()
def load_spec(path: str) -> dict:
    """
    Load a .qa.xml spec file at runtime.

    Replaces any previously loaded spec so that subsequent search_spec calls
    use the new file. Use this when the spec path is only known at conversation
    time (e.g. a file on OneDrive or a network share) instead of pre-configuring
    PROSUITE_SPEC_PATH.

    Args:
        path: Absolute path to the .qa.xml spec file on the local machine.

    Returns a dict with 'conditions_loaded' on success, or 'error' on failure.
    """
    global _spec_conditions
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}
    try:
        loaded = _load_spec(path)
    except Exception as exc:
        return {"error": f"Failed to parse spec: {exc}"}
    _spec_conditions = loaded
    return {"status": "ok", "conditions_loaded": len(loaded), "path": path}


def _make_service() -> Service:
    cfg = load_config()
    if cfg.ssl_cert_path:
        with open(cfg.ssl_cert_path, "rb") as f:
            creds = grpc.ssl_channel_credentials(f.read())
        return Service(cfg.host, cfg.port, creds)
    return Service(cfg.host, cfg.port)


def _resolve_param(raw: Any, p: ParamInfo, dataset_map: dict[str, Dataset]) -> Any:
    if not p.is_dataset:
        return raw
    if p.is_dataset_list:
        names = raw if isinstance(raw, list) else [raw]
        resolved = []
        for ds_name in names:
            if ds_name not in dataset_map:
                raise ValueError(
                    f"Dataset {ds_name!r} not found. "
                    f"Provided datasets: {list(dataset_map)}"
                )
            resolved.append(dataset_map[ds_name])
        return resolved
    if raw not in dataset_map:
        raise ValueError(
            f"Dataset {raw!r} not found. Provided datasets: {list(dataset_map)}"
        )
    return dataset_map[raw]


def _build_condition(req: ConditionRequest, dataset_map: dict[str, Dataset]):
    info = CATALOG.get(req.condition)
    if info is None:
        raise ValueError(
            f"Unknown condition: {req.condition!r}. "
            f"Use list_conditions to browse available conditions."
        )

    method = getattr(Conditions, req.condition)
    kwargs: dict[str, Any] = {}
    for p in info.params:
        if p.name not in req.params:
            required = [pp.name for pp in info.params]
            raise ValueError(
                f"Missing parameter {p.name!r} for condition {req.condition!r}. "
                f"Required: {required}"
            )
        kwargs[p.name] = _resolve_param(req.params[p.name], p, dataset_map)

    return method(**kwargs)


def _build_condition_element(
    name: str,
    condition: Any,
    workspace_id: str,
    test_descriptor: str,
    allow_errors: bool = False,
    description: str = "",
) -> ET.Element:
    """Build a <QualityCondition> element from an already-built condition object.

    Shared by condition_to_xml and add_condition_to_spec so the latter only
    calls _build_condition once per invocation.
    """
    ns = _NS["qa"]

    def q(tag: str) -> str:
        return f"{{{ns}}}{tag}"

    cond_el = ET.Element(
        q("QualityCondition"),
        {
            "name": name,
            "testDescriptor": test_descriptor,
            # XML Schema / .NET XmlConvert booleans are lowercase; str(bool)
            # would emit "True"/"False" and fail to parse on the engine side.
            "allowErrors": "true" if allow_errors else "false",
        },
    )
    if description:
        ET.SubElement(cond_el, q("Description")).text = description

    params_el = ET.SubElement(cond_el, q("Parameters"))
    for p in condition.parameters:
        if p.dataset is not None:
            attrs = {
                "parameter": p.name,
                "value": p.dataset.name,
                "workspace": workspace_id,
            }
            if p.dataset.filter_expression:
                attrs["where"] = p.dataset.filter_expression
            ET.SubElement(params_el, q("Dataset"), attrs)
        else:
            ET.SubElement(
                params_el,
                q("Scalar"),
                {"parameter": p.name, "value": str(p.value)},
            )

    return cond_el


def condition_to_xml(
    name: str,
    condition_request: ConditionRequest,
    datasets: list[DatasetRef],
    workspace_id: str,
    test_descriptor: str,
    allow_errors: bool = False,
    description: str = "",
) -> str:
    """Serialize a condition request into a ProSuite <QualityCondition> element.

    The condition is built through the authoritative prosuite factory (via
    _build_condition), so parameter XML names, dataset/scalar classification,
    and value formatting come from the engine's own model rather than string
    reversal. test_descriptor is the descriptor alias to reference; per the
    reuse-existing strategy the caller looks it up in the target spec.
    """
    dataset_map = {
        ds.name: Dataset(
            ds.name, Model(workspace_id, workspace_id), ds.filter_expression
        )
        for ds in datasets
    }
    condition = _build_condition(condition_request, dataset_map)
    cond_el = _build_condition_element(
        name, condition, workspace_id, test_descriptor, allow_errors, description
    )

    ET.register_namespace("", _NS["qa"])
    return ET.tostring(cond_el, encoding="unicode")


def _find_descriptor_alias(root: ET.Element, test_descriptor: str) -> str | None:
    """Return the name of an existing <TestDescriptor> matching the test's class
    and constructor index, or None. Reuse-existing only: we never synthesize."""
    m = re.match(r"^(\w+?)(?:\((\d+)\))?$", test_descriptor)
    if not m:
        return None
    class_stem, ctor = m.group(1), m.group(2)

    td_root = root.find(f"{{{_NS['qa']}}}TestDescriptors")
    if td_root is None:
        return None
    for td in td_root.findall(f"{{{_NS['qa']}}}TestDescriptor"):
        tc = td.find(f"{{{_NS['qa']}}}TestClass")
        if tc is None:
            continue
        type_base = tc.get("type", "").rsplit(".", 1)[-1]
        if type_base == class_stem and (
            ctor is None or tc.get("constructorIndex") == ctor
        ):
            return td.get("name")
    return None


def add_condition_to_spec(
    spec_xml: str,
    target_specification_name: str,
    name: str,
    condition_request: ConditionRequest,
    datasets: list[DatasetRef],
    workspace_id: str,
    allow_errors: bool = False,
    description: str = "",
) -> str:
    """Return spec_xml with a new <QualityCondition> added and wired into the
    named specification, reusing an existing <TestDescriptor>.

    Pure string-in / string-out: it does not touch the filesystem, so the caller
    can preview the proposal and write only on confirmation. Raises ValueError
    if name already names a <QualityCondition> in the spec, so calling this
    twice with the same name fails loudly instead of producing a spec with
    duplicate condition entries.
    """
    ns = _NS["qa"]

    def q(tag: str) -> str:
        return f"{{{ns}}}{tag}"

    dataset_map = {
        ds.name: Dataset(
            ds.name, Model(workspace_id, workspace_id), ds.filter_expression
        )
        for ds in datasets
    }
    condition = _build_condition(condition_request, dataset_map)

    ET.register_namespace("", ns)
    root = ET.fromstring(spec_xml)

    qcs = root.find(q("QualityConditions"))
    if qcs is None:
        raise ValueError("Spec has no <QualityConditions> section.")
    if any(c.get("name") == name for c in qcs.findall(q("QualityCondition"))):
        raise ValueError(f"Spec already has a QualityCondition named {name!r}.")

    alias = _find_descriptor_alias(root, condition.test_descriptor)
    if alias is None:
        raise ValueError(
            f"No existing test descriptor matches {condition.test_descriptor!r} "
            f"in the target spec; reuse-existing only (cannot synthesize a descriptor)."
        )

    cond_el = _build_condition_element(
        name, condition, workspace_id, alias, allow_errors, description
    )
    qcs.append(cond_el)

    specs = root.find(q("QualitySpecifications"))
    target = None
    if specs is not None:
        for s in specs.findall(q("QualitySpecification")):
            if s.get("name") == target_specification_name:
                target = s
                break
    if target is None:
        raise ValueError(
            f"Specification {target_specification_name!r} not found in spec."
        )
    elements = target.find(q("Elements"))
    if elements is None:
        elements = ET.SubElement(target, q("Elements"))
    ET.SubElement(elements, q("Element"), {"qualityCondition": name})

    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


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


@mcp.tool()
def run_verification(
    model_catalog_path: str,
    model_name: str,
    datasets: list[DatasetRef],
    conditions: list[ConditionRequest],
    output_dir: str | None = None,
    envelope: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Run a ProSuite quality verification.

    Build an ad-hoc condition-list specification and run it against the
    given workspace. The ProSuite service (prosuite-qa-microservice) must
    be reachable at the host/port configured via PROSUITE_HOST /
    PROSUITE_PORT environment variables (default: localhost:5151).

    Args:
        model_catalog_path: Workspace path on the server, e.g.
            'C:/data/mydb.gdb' or a .sde connection file.
        model_name: Logical name for the data model (arbitrary, used in
            generated condition names).
        datasets: Feature classes or tables to make available for
            conditions. Each entry has a 'name' (feature class name) and
            an optional 'filter_expression' (SQL WHERE clause).
        conditions: Conditions to run. Each entry has:
            - condition: method name from list_conditions (e.g.
              'qa_min_length_0')
            - params: dict mapping parameter names to values. Dataset
              parameters take a string matching a name in 'datasets';
              primitive parameters take their direct value.
        output_dir: Optional server-side directory for Issues.gdb and
            HTML report. The service process must have write access.
        envelope: Optional spatial filter {x_min, y_min, x_max, y_max}.
            Omit for full-extent verification.

    Returns a summary with status, total_errors, and per-condition
    breakdown. Check 'status': 'error' for connection or parameter
    failures.
    """
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
        output_dir = str(_make_run_dir("adhoc", Path.cwd() / "runs"))

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
            "error": "Verification stream ended without a final summary.",
            "issues_seen_in_stream": outcome.total,
        }

    summary = _summarize(verified_spec, outcome)
    summary["status"] = "success"
    summary["output_dir"] = output_dir
    return summary


@mcp.tool()
def run_xml_verification(
    specification_name: str,
    data_source_replacements: list[WorkspaceReplacement],
    output_dir: str | None = None,
    envelope: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Run a ProSuite quality verification directly from the loaded XML spec file.

    Unlike run_verification, this tool sends the XML spec to the ProSuite service
    as-is, without decomposing it into individual conditions and datasets. This
    preserves per-condition dataset filters, default scalar values, and all other
    spec details exactly as configured.

    Use search_spec (with empty query) to discover available specification_name
    values and workspace_id keys that need to be replaced.

    Args:
        specification_name: Name of the QualitySpecification element inside the
            XML file to run (e.g. 'Copy of DATA_OSM_10_Demo').
        data_source_replacements: Maps each workspace_id in the XML to the actual
            workspace path on the ProSuite server. Example:
            [{"workspace_id": "DATA_OSM", "workspace_path": "C:/data/osm.sde"}]
        output_dir: Optional server-side directory for Issues.gdb and HTML report.
        envelope: Optional spatial filter {x_min, y_min, x_max, y_max}.

    Returns a summary with status, total_errors, and per-condition breakdown.
    Requires PROSUITE_SPEC_PATH to be configured.
    """
    cfg = load_config()
    if not cfg.spec_path:
        return {
            "status": "error",
            "engine_confirmed": False,
            "error": "No spec loaded. Set PROSUITE_SPEC_PATH to a .qa.xml file path.",
        }

    replacements = [
        [r.workspace_id, r.workspace_path] for r in data_source_replacements
    ]

    try:
        xml_spec = XmlSpecification(cfg.spec_path, specification_name, replacements)
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
            "error": "Verification stream ended without a final summary.",
            "issues_seen_in_stream": outcome.total,
        }

    summary = _summarize(verified_spec, outcome)
    summary["status"] = "success"
    summary["output_dir"] = output_dir
    return summary
