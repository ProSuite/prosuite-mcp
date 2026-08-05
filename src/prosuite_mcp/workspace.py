"""Read a geodatabase's schema, so conditions can be chosen from the data
rather than from its name.

Reads the path on this machine, not the one the ProSuite service resolves.
fiona ships GDAL, and OpenFileGDB is read-only and license-free.
"""

from __future__ import annotations

import re
from typing import Any

import fiona
from fiona.errors import FionaError, UnsupportedGeometryTypeError

_WKT_NAME = re.compile(r'^\s*\w+\["([^"]+)"')


class WorkspaceError(Exception):
    """Raised when the geodatabase cannot be read here."""


def _crs(collection: fiona.Collection) -> dict[str, Any] | None:
    """Name and EPSG only; the WKT is kilobytes of no use to a caller."""
    crs = collection.crs
    if not crs:
        return None
    match = _WKT_NAME.match(crs.to_wkt() or "")
    return {"name": match.group(1) if match else None, "epsg": crs.to_epsg()}


def _fields(collection: fiona.Collection) -> list[dict[str, Any]]:
    fields = []
    for name, spec in collection.schema["properties"].items():
        kind, _, width = spec.partition(":")
        field = {"name": name, "type": kind}
        if width:
            field["width"] = int(width)
        fields.append(field)
    return fields


def _summarize(collection: fiona.Collection, geometry_type: str) -> dict[str, Any]:
    return {
        "name": collection.name,
        "geometry_type": geometry_type,
        "feature_count": len(collection),
        "spatial_reference": _crs(collection),
    }


def _read(path: str, layer: str, detail: bool) -> dict[str, Any]:
    try:
        with fiona.open(path, layer=layer) as c:
            summary = _summarize(c, c.schema["geometry"])
            extra = _detail(c) if detail else {}
    except UnsupportedGeometryTypeError:
        # A type fiona has no name for. "None" is taken: it means no geometry.
        with fiona.open(path, layer=layer, ignore_geometry=True) as c:
            summary = _summarize(c, "Unknown")
            extra = _detail(c) if detail else {}
    except FionaError as exc:
        raise WorkspaceError(f"Cannot read {layer!r} in {path}: {exc}") from exc
    return {**summary, **extra}


def _detail(collection: fiona.Collection) -> dict[str, Any]:
    x_min, y_min, x_max, y_max = collection.bounds
    return {
        "extent": {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max},
        "fields": _fields(collection),
    }


def list_datasets(workspace_path: str) -> dict[str, Any]:
    """Every feature class and table, with just enough to pick one."""
    try:
        layers = fiona.listlayers(workspace_path)
    except FionaError as exc:
        raise WorkspaceError(f"Cannot open {workspace_path}: {exc}") from exc

    return {
        "workspace_path": workspace_path,
        "datasets": [_read(workspace_path, layer, detail=False) for layer in layers],
    }


def describe_dataset(workspace_path: str, name: str) -> dict[str, Any]:
    """One dataset in full: geometry, extent, spatial reference, fields."""
    try:
        layers = fiona.listlayers(workspace_path)
    except FionaError as exc:
        raise WorkspaceError(f"Cannot open {workspace_path}: {exc}") from exc
    if name not in layers:
        raise WorkspaceError(
            f"No dataset named {name!r} in {workspace_path}. Available: "
            f"{', '.join(layers) or 'none'}"
        )

    return {
        "workspace_path": workspace_path,
        **_read(workspace_path, name, detail=True),
    }
