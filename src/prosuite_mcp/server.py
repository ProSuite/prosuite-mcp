from __future__ import annotations

from typing import Any

import grpc
import prosuite.generated.quality_verification_service_pb2_grpc as _qa_grpc
from mcp.server.fastmcp import FastMCP
from prosuite import EnvelopePerimeter, Service
from prosuite.data_model import Dataset, Model
from prosuite.factories.quality_conditions import Conditions
from prosuite.quality import Specification
from prosuite.verification import VerifiedSpecification
from prosuite.verification.advanced_parameters import AdvancedParameters
from pydantic import BaseModel

from .catalog import CATALOG, ParamInfo
from .config import load_config

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


def _run_stream(
    service: Service,
    spec: Specification,
    output_dir: str | None,
    perimeter,
) -> tuple[dict[int, int], VerifiedSpecification | None]:
    """Iterate the raw gRPC stream to capture per-condition issue counts.

    The prosuite Issue wrapper strips condition_id, so we must read it here
    from the raw protobuf before it is lost.
    """
    params = AdvancedParameters(spec, output_dir or "", perimeter)
    channel = service._create_channel()
    client = _qa_grpc.QualityVerificationGrpcStub(channel)
    request = service._compile_request(params)

    issues_by_condition: dict[int, int] = {}
    verified_spec = None

    for response_msg in client.VerifyQuality(request):
        for issue_msg in response_msg.issues:
            cid = issue_msg.condition_id
            issues_by_condition[cid] = issues_by_condition.get(cid, 0) + 1
        if response_msg.service_call_status == 3:
            verified_spec = service._parse_verified_specification(response_msg, {})

    return issues_by_condition, verified_spec


def _summarize(
    spec: VerifiedSpecification, issues_by_condition: dict[int, int]
) -> dict[str, Any]:
    return {
        "specification_name": spec.specification_name,
        "user_name": spec.user_name,
        "total_conditions": spec.verified_conditions_count,
        "total_errors": sum(issues_by_condition.values()),
        "conditions": [
            {
                "name": c.name or f"condition_{c.condition_id}",
                "errors": issues_by_condition.get(c.condition_id, 0)
                if c.condition_id is not None
                else 0,
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
        return {"status": "error", "error": str(exc)}

    perimeter = None
    if envelope:
        perimeter = EnvelopePerimeter(
            x_min=envelope["x_min"],
            y_min=envelope["y_min"],
            x_max=envelope["x_max"],
            y_max=envelope["y_max"],
        )

    service = _make_service()

    try:
        issues_by_condition, verified_spec = _run_stream(
            service, spec, output_dir, perimeter
        )
    except grpc.RpcError as exc:
        return {
            "status": "error",
            "error": f"gRPC {exc.code()}: {exc.details()}",
        }

    if verified_spec is None:
        return {
            "status": "error",
            "error": "Verification stream ended without a final summary.",
            "total_errors": sum(issues_by_condition.values()),
        }

    summary = _summarize(verified_spec, issues_by_condition)
    summary["status"] = "success"
    if output_dir:
        summary["output_dir"] = output_dir
    return summary
