"""Introspects prosuite.factories.quality_conditions.Conditions at import time."""

import inspect
from dataclasses import dataclass, field

from prosuite.data_model.base_dataset import BaseDataset
from prosuite.factories.quality_conditions import Conditions


@dataclass
class ParamInfo:
    name: str
    type_hint: str
    is_dataset: bool
    is_dataset_list: bool


@dataclass
class ConditionInfo:
    method_name: str
    docstring: str
    params: list[ParamInfo] = field(default_factory=list)


def _classify_param(annotation: object) -> tuple[str, bool, bool]:
    """Return (type_hint_str, is_dataset, is_dataset_list)."""
    if annotation is inspect.Parameter.empty:
        return "any", False, False

    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ()) or ()

    if (
        origin is list
        and args
        and (issubclass(args[0], BaseDataset) if isinstance(args[0], type) else False)
    ):
        return f"list[{args[0].__name__}]", True, True

    if isinstance(annotation, type) and issubclass(annotation, BaseDataset):
        return annotation.__name__, True, False

    name = getattr(annotation, "__name__", None)
    if name is not None:
        return name, False, False

    return str(annotation), False, False


def _build_catalog() -> dict[str, ConditionInfo]:
    catalog: dict[str, ConditionInfo] = {}
    for name, method in inspect.getmembers(Conditions, predicate=inspect.ismethod):
        if name.startswith("_"):
            continue
        sig = inspect.signature(method)
        params: list[ParamInfo] = []
        for pname, p in sig.parameters.items():
            type_hint, is_ds, is_ds_list = _classify_param(p.annotation)
            params.append(
                ParamInfo(
                    name=pname,
                    type_hint=type_hint,
                    is_dataset=is_ds,
                    is_dataset_list=is_ds_list,
                )
            )
        catalog[name] = ConditionInfo(
            method_name=name,
            docstring=(inspect.getdoc(method) or "").strip(),
            params=params,
        )
    return catalog


CATALOG: dict[str, ConditionInfo] = _build_catalog()
