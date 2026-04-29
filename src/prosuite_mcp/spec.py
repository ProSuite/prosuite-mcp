"""Load and search a ProSuite .qa.xml spec file."""

from __future__ import annotations

import importlib.util
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

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


def _descriptor_to_method(descriptor: str) -> str | None:
    m = re.match(r"^(\w+?)(?:\((\d+)\))?$", descriptor)
    if not m:
        return None
    class_name, version = m.group(1), m.group(2)
    snake = _to_snake(class_name)
    return f"qa_{snake}_{version}" if version is not None else f"qa_{snake}"


def _load_list_params() -> dict[str, set[str]]:
    """Return {method_name: {snake_case param names that are List[BaseDataset]}}."""
    spec = importlib.util.find_spec("prosuite")
    if not spec or not spec.submodule_search_locations:
        return {}
    qc_path = (
        Path(list(spec.submodule_search_locations)[0])
        / "factories"
        / "quality_conditions.py"
    )
    if not qc_path.exists():
        return {}

    src = qc_path.read_text(encoding="utf-8")
    result: dict[str, set[str]] = {}
    sig_re = re.compile(r"def (qa_\w+)\(cls,([^)]+)\)")
    param_re = re.compile(r"(\w+)\s*:\s*([\w\[\], ]+?)(?:,|\Z)")

    for m in sig_re.finditer(src):
        method_name = m.group(1)
        list_params: set[str] = set()
        for pm in param_re.finditer(m.group(2)):
            pname, ptype = pm.group(1).strip(), pm.group(2)
            if "List" in ptype and "BaseDataset" in ptype:
                list_params.add(_to_snake(pname))
        result[method_name] = list_params

    return result


_LIST_PARAMS: dict[str, set[str]] | None = None


def _get_list_params() -> dict[str, set[str]]:
    global _LIST_PARAMS
    if _LIST_PARAMS is None:
        _LIST_PARAMS = _load_list_params()
    return _LIST_PARAMS


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
    list_params: dict[str, set[str]],
) -> SpecCondition:
    name = cond_el.get("name", "")
    descriptor = cond_el.get("testDescriptor", "")
    allow_errors = cond_el.get("allowErrors", "True").lower() == "true"

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
            py_pname = _to_snake(xml_pname)

            if tag == "Dataset":
                if p.get("transformerName"):
                    has_transformer = True
                    break
                value = p.get("value", "")
                if value:
                    is_list = py_pname in (
                        list_params.get(method, set()) if method else set()
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
        unsupported_reason = f"unrecognised testDescriptor: {descriptor!r}"

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
    list_params = _get_list_params()
    return [_parse_condition(el, cat, list_params) for el, cat in pairs]


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
