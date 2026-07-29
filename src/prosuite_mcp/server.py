from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import authoring, verification
from .catalog import CATALOG
from .config import load_config
from .schemas import ConditionRequest, DatasetRef, WorkspaceReplacement
from .spec import get_loaded_conditions, get_spec_metadata, set_spec
from .spec import load_spec as _load_spec
from .spec import search_spec as _search_spec

mcp = MCPServer(
    "ProSuite MCP",
    instructions="MCP server for Dira ProSuite quality verification",
)


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


@mcp.tool()
def search_spec(query: str, max_results: int = 20) -> dict:
    """
    Search the loaded QA spec for conditions matching a natural-language query.

    Returns up to max_results conditions whose name, description, or category
    contains the query string (case-insensitive, literal substring match, not
    semantic). Spec content may be in English, German, French, or Italian --
    query in the same language as the spec to find matches.

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
    conditions = get_loaded_conditions()
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
    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}
    try:
        loaded = _load_spec(path)
    except Exception as exc:
        return {"error": f"Failed to parse spec: {exc}"}
    set_spec(loaded)
    return {"status": "ok", "conditions_loaded": len(loaded), "path": path}


@mcp.tool()
def condition_to_xml(
    name: str,
    condition_request: ConditionRequest,
    datasets: list[DatasetRef],
    workspace_id: str,
    test_descriptor: str,
    allow_errors: bool = False,
    description: str = "",
) -> str:
    """Preview a single ProSuite <QualityCondition> XML element, without touching a spec.

    Builds the condition through the same prosuite factory as run_verification,
    so parameter names and value formatting are engine-derived, not guessed.
    Returns a string; never writes anywhere. test_descriptor must already
    exist in the target spec (e.g. "MinLength(1)") — use add_condition_to_spec
    if you want it resolved automatically and wired into a real specification.

    Args:
        name: Human-readable condition name.
        condition_request: {condition: method name from list_conditions, params: dict}.
        datasets: Feature classes/tables used by condition_request, each with
            'name' and an optional 'filter_expression'.
        workspace_id: Logical workspace id to bind datasets to (e.g. "DATA_OSM").
        test_descriptor: Existing <TestDescriptor> name to reference.
        allow_errors: Whether issues from this condition are tolerated.
        description: Optional description element.

    Returns the <QualityCondition> XML fragment.
    """
    return authoring.build_condition_xml(
        name,
        condition_request,
        datasets,
        workspace_id,
        test_descriptor,
        allow_errors,
        description,
    )


@mcp.tool()
def add_condition_to_spec(
    target_specification_name: str,
    name: str,
    condition_request: ConditionRequest,
    datasets: list[DatasetRef],
    workspace_id: str,
    allow_errors: bool = False,
    description: str = "",
    spec_xml: str | None = None,
) -> str:
    """Preview adding a new QualityCondition to a spec, reusing an existing descriptor.

    Builds the condition through the same prosuite factory as run_verification,
    resolves a matching <TestDescriptor> (never synthesizes one), and returns
    the full updated spec XML with the condition appended and wired into
    target_specification_name. Preview only — never writes to a file. Call
    describe_spec first for valid specification/workspace/dataset names.

    Args:
        target_specification_name: QualitySpecification to wire the condition into.
        name: Human-readable condition name; must not already exist in the spec.
        condition_request: {condition: method name from list_conditions, params: dict}.
        datasets: Feature classes/tables used by condition_request, each with
            'name' and an optional 'filter_expression'.
        workspace_id: Logical workspace id to bind datasets to (e.g. "DATA_OSM").
        allow_errors: Whether issues from this condition are tolerated.
        description: Optional description element.
        spec_xml: Spec XML text; defaults to reading the configured/loaded
            spec path if omitted.

    Returns the updated spec XML, ready to review and persist yourself.
    """
    if spec_xml is None:
        cfg = load_config()
        if not cfg.spec_path:
            raise ValueError(
                "No spec loaded. Set PROSUITE_SPEC_PATH to a .qa.xml file path, "
                "call load_spec first, or pass spec_xml explicitly."
            )
        try:
            spec_xml = Path(cfg.spec_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"Could not read spec file {cfg.spec_path!r}: {exc}"
            ) from exc

    return authoring.add_condition(
        target_specification_name,
        name,
        condition_request,
        datasets,
        workspace_id,
        spec_xml,
        allow_errors,
        description,
    )


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
    return verification._run_verification_impl(
        model_catalog_path,
        model_name,
        datasets,
        conditions,
        output_dir,
        envelope,
        "adhoc",
    )


@mcp.tool()
def preview_condition_run(
    model_catalog_path: str,
    condition_request: ConditionRequest,
    datasets: list[DatasetRef],
    workspace_id: str,
    output_dir: str | None = None,
    envelope: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Run a single proposed condition ad-hoc and show what it actually flags.

    Tier-2 confirmation for spec authoring: add_condition_to_spec only confirms
    a condition builds and references an existing descriptor. This runs it for
    real against model_catalog_path and returns the same engine-confirmed
    summary as run_verification (engine_confirmed, total_errors,
    sample_features with the actual flagged issues), so a proposed condition
    can be judged by what it flags before it's merged into a spec. Scope
    envelope to a small extent to keep this a preview, not a full run.

    Args:
        model_catalog_path: Workspace path on the server, e.g.
            'C:/data/mydb.gdb' or a .sde connection file.
        condition_request: {condition: method name from list_conditions, params: dict}.
        datasets: Feature classes/tables used by condition_request, each with
            'name' and an optional 'filter_expression'.
        workspace_id: Logical name for the data model (arbitrary, used in
            generated condition names).
        output_dir: Optional server-side directory for Issues.gdb and HTML report.
        envelope: Optional spatial filter {x_min, y_min, x_max, y_max}.
    """
    return verification._run_verification_impl(
        model_catalog_path,
        workspace_id,
        datasets,
        [condition_request],
        output_dir,
        envelope,
        "preview",
    )


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

    return verification.run_xml_verification_impl(
        cfg.spec_path,
        specification_name,
        replacements,
        output_dir,
        envelope,
    )
