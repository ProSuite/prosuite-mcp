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
    schema = collection.schema or {}
    fields = []
    for name, spec in (schema.get("properties") or {}).items():
        kind, _, width = spec.partition(":")
        field: dict[str, Any] = {"name": name, "type": kind}
        if width:
            field["width"] = int(width)
        fields.append(field)
    return fields


def _extent(
    collection: fiona.Collection, geometry_type: str
) -> dict[str, float] | None:
    # A table has no extent, and asking for one raises rather than saying so.
    if geometry_type == "None":
        return None
    bounds = collection.bounds
    if not bounds:
        return None
    x_min, y_min, x_max, y_max = bounds
    return {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max}


def _feature_count(collection: fiona.Collection) -> int | None:
    # fiona asks OGR not to scan, and raises TypeError where that means the
    # driver cannot answer. Not knowing the count is not a failed read.
    try:
        return len(collection)
    except TypeError:
        return None


def _shape(
    collection: fiona.Collection, geometry_type: str, detail: bool
) -> dict[str, Any]:
    shaped = {
        "name": collection.name,
        "geometry_type": geometry_type,
        "feature_count": _feature_count(collection),
        "spatial_reference": _crs(collection),
    }
    if detail:
        shaped["extent"] = _extent(collection, geometry_type)
        shaped["fields"] = _fields(collection)
    return shaped


def _read(path: str, layer: str, detail: bool) -> dict[str, Any]:
    # Nested, so a FionaError from the retry is converted too. A sibling
    # handler would not see it: it is raised while the first one is running.
    try:
        try:
            with fiona.open(path, layer=layer) as c:
                schema = c.schema or {}
                return _shape(c, schema.get("geometry") or "Unknown", detail)
        except UnsupportedGeometryTypeError:
            # A type fiona has no name for. "None" is taken: it means no geometry.
            with fiona.open(path, layer=layer, ignore_geometry=True) as c:
                return _shape(c, "Unknown", detail)
    except FionaError as exc:
        raise WorkspaceError(f"Cannot read {layer!r} in {path}: {exc}") from exc


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
