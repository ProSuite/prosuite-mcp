"""Build ProSuite condition objects and edit QA spec XML in memory.

The write-side counterpart to spec.py: spec.py reads/searches an existing
spec, this module builds new <QualityCondition> fragments and appends them to
a spec's XML. Nothing here writes to disk — callers decide separately whether
and how to persist the returned XML.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from prosuite.data_model import Dataset, Model
from prosuite.factories.quality_conditions import Conditions

from .catalog import CATALOG, ParamInfo
from .schemas import ConditionRequest, DatasetRef
from .spec import _NS


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

    Shared by build_condition_xml and add_condition so the latter only calls
    _build_condition once per invocation.
    """
    ns = _NS["qa"]

    def q(tag: str) -> str:
        return f"{{{ns}}}{tag}"

    cond_el = ET.Element(
        q("QualityCondition"),
        {
            "name": name,
            "testDescriptor": test_descriptor,
            # allowErrors maps to ProSuite's Override enum (Null/True/False),
            # not xs:boolean; XmlSerializer matches enum names case-sensitively,
            # so it must be "True"/"False", not lowercase.
            "allowErrors": "True" if allow_errors else "False",
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


def build_condition_xml(
    name: str,
    condition_request: ConditionRequest,
    datasets: list[DatasetRef],
    workspace_id: str,
    test_descriptor: str,
    allow_errors: bool = False,
    description: str = "",
) -> str:
    """Build a single <QualityCondition> XML fragment, without touching a spec."""
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


def add_condition(
    target_specification_name: str,
    name: str,
    condition_request: ConditionRequest,
    datasets: list[DatasetRef],
    workspace_id: str,
    spec_xml: str,
    allow_errors: bool = False,
    description: str = "",
) -> str:
    """Add a new QualityCondition to spec_xml, reusing an existing descriptor.

    Builds the condition through the same prosuite factory as run_verification,
    resolves a matching <TestDescriptor> (never synthesizes one), and returns
    the full updated spec XML with the condition appended and wired into
    target_specification_name.
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
