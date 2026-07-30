"""Load and search a ProSuite .qa.xml spec file."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .catalog import CATALOG
from .config import load_config

_NS = {"qa": "urn:ProSuite.QA.QualitySpecifications-3.0"}


@dataclass
class DatasetParam:
    xml_name: str
    py_name: str
    dataset_name: str
    filter_expression: str
    is_list: bool


@dataclass
class ScalarParam:
    xml_name: str
    py_name: str
    value: str


@dataclass
class SpecCondition:
    name: str
    category: str
    allow_errors: bool
    description: str
    method: str
    dataset_params: list[DatasetParam] = field(default_factory=list)
    scalar_params: list[ScalarParam] = field(default_factory=list)
    unsupported: bool = False
    unsupported_reason: str = ""


def _to_snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


# prosuite's method names follow no rule a descriptor can be rewritten into
# (QaGdbConstraintFactory -> qa_gdb_constraint_factory, Qa3dConstantZ(0) ->
# qa3d_constant_z_0), so match against the factory itself, ignoring underscores.
_CATALOG_BY_NORMALIZED = {name.replace("_", ""): name for name in CATALOG}


def _descriptor_to_method(descriptor: str) -> str | None:
    """Factory method for a testDescriptor, or None if prosuite has no such test."""
    m = re.match(r"^(\w+?)(?:\((\d+)\))?$", descriptor)
    if not m:
        return None
    stem, version = m.group(1), m.group(2)
    if not stem.lower().startswith("qa"):
        stem = "qa" + stem
    return _CATALOG_BY_NORMALIZED.get(stem.lower() + (version or ""))


# Same problem one level down: minimumZValue snake-cases to minimum_zvalue but
# the factory calls it minimum_z_value, so resolve against the signature too.
_PARAMS_BY_NORMALIZED = {
    method: {p.name.replace("_", ""): p.name for p in info.params}
    for method, info in CATALOG.items()
}


def _resolve_param_name(method: str | None, xml_name: str) -> str:
    """Factory parameter name for an XML parameter, snake_case if unresolvable."""
    names = _PARAMS_BY_NORMALIZED.get(method or "")
    if names:
        resolved = names.get(xml_name.lower())
        if resolved:
            return resolved
    return _to_snake(xml_name)


def _is_list_dataset_param(method: str, py_name: str) -> bool:
    """Whether py_name is a List[BaseDataset] parameter of method, per CATALOG.

    CATALOG is built once at import time from the prosuite factory's own type
    annotations (see catalog.py), so this reuses that classification instead
    of re-deriving it by re-parsing the factory's source file.
    """
    info = CATALOG.get(method)
    if info is None:
        return False
    return any(p.name == py_name and p.is_dataset_list for p in info.params)


def _walk_conditions(
    el: ET.Element,
    category_stack: list[str],
    out: list[tuple[ET.Element, str]],
) -> None:
    tag = el.tag.split("}")[-1]
    if tag == "Category":
        category_stack = category_stack + [el.get("name", "")]
    if tag == "QualityCondition":
        out.append((el, category_stack[-1] if category_stack else ""))
        return
    for child in el:
        _walk_conditions(child, category_stack, out)


def _parse_condition(
    cond_el: ET.Element,
    category: str,
) -> SpecCondition:
    name = cond_el.get("name", "")
    descriptor = cond_el.get("testDescriptor", "")
    # Absent means False in ProSuite: a violation is a hard error unless the
    # spec says otherwise. Defaulting to True reported 42% of real conditions
    # as tolerated when they are not.
    allow_errors = cond_el.get("allowErrors", "False").lower() == "true"

    desc_el = cond_el.find("qa:Description", _NS)
    description = (desc_el.text or "").strip() if desc_el is not None else ""

    method = _descriptor_to_method(descriptor)

    dataset_params: list[DatasetParam] = []
    scalar_params: list[ScalarParam] = []
    has_transformer = False

    params_el = cond_el.find("qa:Parameters", _NS)
    if params_el is not None:
        for p in params_el:
            tag = p.tag.split("}")[-1]
            xml_pname = p.get("parameter", "")
            py_pname = _resolve_param_name(method, xml_pname)

            if tag == "Dataset":
                if p.get("transformerName"):
                    has_transformer = True
                    break
                value = p.get("value", "")
                if value:
                    is_list = method is not None and _is_list_dataset_param(
                        method, py_pname
                    )
                    dataset_params.append(
                        DatasetParam(
                            xml_name=xml_pname,
                            py_name=py_pname,
                            dataset_name=value,
                            filter_expression=p.get("where", ""),
                            is_list=is_list,
                        )
                    )
            elif tag == "Scalar":
                scalar_params.append(
                    ScalarParam(
                        xml_name=xml_pname,
                        py_name=py_pname,
                        value=p.get("value", ""),
                    )
                )

    unsupported = False
    unsupported_reason = ""
    if has_transformer:
        unsupported = True
        unsupported_reason = "uses transformer preprocessing"
    elif method is None:
        unsupported = True
        unsupported_reason = (
            f"no prosuite factory method for testDescriptor {descriptor!r}"
        )

    return SpecCondition(
        name=name,
        category=category,
        allow_errors=allow_errors,
        description=description,
        method=method or descriptor,
        dataset_params=dataset_params,
        scalar_params=scalar_params,
        unsupported=unsupported,
        unsupported_reason=unsupported_reason,
    )


def load_spec(path: str) -> list[SpecCondition]:
    tree = ET.parse(path)
    root = tree.getroot()
    pairs: list[tuple[ET.Element, str]] = []
    _walk_conditions(root, [], pairs)
    return [_parse_condition(el, cat) for el, cat in pairs]


def get_spec_metadata(path: str) -> dict:
    """Return spec names, workspace definitions, and per-spec dataset/workspace summary."""
    tree = ET.parse(path)
    root = tree.getroot()

    workspaces: dict[str, dict] = {}
    ws_el = root.find("qa:Workspaces", _NS)
    if ws_el is not None:
        for ws in ws_el:
            wid = ws.get("id", "")
            if wid:
                workspaces[wid] = {
                    "workspace_id": wid,
                    "model_name": ws.get("modelName", ""),
                }

    # Conditions and specifications may sit at the document root or nested inside
    # <Categories>, so both are collected from anywhere in the tree. Conditions
    # come from _walk_conditions specifically, so this stays in step with
    # load_spec: the two must agree on which conditions a spec file contains.
    pairs: list[tuple[ET.Element, str]] = []
    _walk_conditions(root, [], pairs)

    condition_refs: dict[str, tuple[set[str], set[str]]] = {}
    for cond, _category in pairs:
        ws_ids: set[str] = set()
        ds_names: set[str] = set()
        params_el = cond.find("qa:Parameters", _NS)
        if params_el is not None:
            for p in params_el:
                if p.tag.split("}")[-1] == "Dataset":
                    wid = p.get("workspace", "")
                    value = p.get("value", "")
                    if wid:
                        ws_ids.add(wid)
                    if value:
                        ds_names.add(value)
        condition_refs[cond.get("name", "")] = (ws_ids, ds_names)

    specs = []
    for spec_el in root.iter(f"{{{_NS['qa']}}}QualitySpecification"):
        sname = spec_el.get("name", "")
        cond_names: list[str] = []
        elements_el = spec_el.find("qa:Elements", _NS)
        if elements_el is not None:
            for el in elements_el:
                cref = el.get("qualityCondition", "")
                if cref:
                    cond_names.append(cref)
        all_ws: set[str] = set()
        all_ds: set[str] = set()
        for cname in cond_names:
            if cname in condition_refs:
                ws, ds = condition_refs[cname]
                all_ws.update(ws)
                all_ds.update(ds)
        specs.append(
            {
                "specification_name": sname,
                "condition_count": len(cond_names),
                "workspace_ids": sorted(all_ws),
                "datasets": sorted(all_ds),
            }
        )

    return {"specifications": specs, "workspaces": list(workspaces.values())}


def search_spec(
    conditions: list[SpecCondition],
    query: str,
    max_results: int = 20,
) -> dict:
    q = query.lower()
    matched = [
        c
        for c in conditions
        if not c.unsupported
        and (
            q in c.name.lower() or q in c.description.lower() or q in c.category.lower()
        )
    ]

    results = []
    for c in matched[:max_results]:
        params: dict = {}
        seen: set[str] = set()
        required_datasets: list[dict] = []

        for dp in c.dataset_params:
            if dp.is_list:
                params.setdefault(dp.py_name, []).append(dp.dataset_name)
            else:
                params[dp.py_name] = dp.dataset_name
            if dp.dataset_name not in seen:
                seen.add(dp.dataset_name)
                required_datasets.append(
                    {"name": dp.dataset_name, "filter_expression": dp.filter_expression}
                )

        for sp in c.scalar_params:
            params[sp.py_name] = sp.value

        # A spec omits a list element entirely when the list is empty, e.g.
        # RegularExpression with fieldListType=IgnoredFields and no fieldNames
        # means "ignore none". Dataset lists are left out: empty there makes the
        # condition vacuous rather than valid, so it should fail loudly.
        info = CATALOG.get(c.method)
        if info is not None:
            for p in info.params:
                if p.is_list and not p.is_dataset and not p.has_default:
                    params.setdefault(p.name, [])

        results.append(
            {
                "name": c.name,
                "category": c.category,
                "allow_errors": c.allow_errors,
                "description": c.description,
                "condition_request": {"condition": c.method, "params": params},
                "required_datasets": required_datasets,
            }
        )

    return {
        "total_matches": len(matched),
        "returned": len(results),
        "results": results,
    }


# The active spec is identified by its path, not by its parsed conditions.
# Each consumer needs something different from it (parsed conditions to search,
# a path to hand XmlSpecification, the raw text to author against), so the path
# is what they have to agree on. Resolving it in one place is what stops
# load_spec from switching the spec for some tools but not others.
_active_spec_path: str | None = None
_loaded_conditions: list[SpecCondition] | None = None


def set_spec(path: str, conditions: list[SpecCondition]) -> None:
    """Make path the active spec, replacing any previously loaded one."""
    global _active_spec_path, _loaded_conditions
    _active_spec_path = path
    _loaded_conditions = conditions


def get_spec_path() -> str | None:
    """Path of the active spec: whatever load_spec set, else PROSUITE_SPEC_PATH."""
    if _active_spec_path is not None:
        return _active_spec_path
    return load_config().spec_path


def get_loaded_conditions() -> list[SpecCondition] | None:
    """Conditions of the active spec, parsed lazily on first use."""
    global _loaded_conditions
    if _loaded_conditions is None:
        path = get_spec_path()
        if path:
            _loaded_conditions = load_spec(path)
    return _loaded_conditions
