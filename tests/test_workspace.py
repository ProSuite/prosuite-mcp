"""Reading a geodatabase's schema, against real geodatabases: fiona ships
GDAL, so there is nothing to mock."""

from __future__ import annotations

import json

import fiona
import pytest

from prosuite_mcp.workspace import WorkspaceError, describe_dataset, list_datasets


@pytest.fixture(scope="module")
def gdb(tmp_path_factory):
    """A real file geodatabase, built rather than committed."""
    path = tmp_path_factory.mktemp("ws") / "test.gdb"
    schema = {
        "geometry": "Point",
        "properties": {"kind": "str:25", "height": "int32"},
    }
    with fiona.open(
        path,
        "w",
        driver="OpenFileGDB",
        layer="points",
        schema=schema,
        crs="EPSG:2056",
    ) as dst:
        for i, kind in enumerate(("a", "b")):
            dst.write(
                {
                    "geometry": {
                        "type": "Point",
                        "coordinates": (2600000 + i * 100, 1200000 + i * 100),
                    },
                    "properties": {"kind": kind, "height": 12 + i},
                }
            )
    return str(path)


def test_list_datasets_reads_a_real_geodatabase(gdb):
    result = list_datasets(gdb)

    assert result["datasets"] == [
        {
            "name": "points",
            "geometry_type": "Point",
            "feature_count": 2,
            "spatial_reference": {"name": "CH1903+ / LV95", "epsg": 2056},
        }
    ]


def test_describe_dataset_reads_fields_and_extent(gdb):
    result = describe_dataset(gdb, "points")

    assert result["feature_count"] == 2
    assert result["extent"] == {
        "x_min": 2600000.0,
        "y_min": 1200000.0,
        "x_max": 2600100.0,
        "y_max": 1200100.0,
    }
    assert result["fields"] == [
        {"name": "kind", "type": "str", "width": 25},
        {"name": "height", "type": "int32"},
    ]


def test_describe_dataset_names_the_datasets_there_are(gdb):
    with pytest.raises(WorkspaceError, match="Available: points"):
        describe_dataset(gdb, "no_such_layer")


def test_list_datasets_reports_a_path_it_cannot_open(tmp_path):
    with pytest.raises(WorkspaceError, match="Cannot open"):
        list_datasets(str(tmp_path / "missing.gdb"))


def test_a_dataset_fiona_cannot_type_still_reports_the_rest(gdb, monkeypatch):
    """Losing the type label is acceptable, losing the fields is not."""
    real_open = fiona.open
    calls = []

    def fake_open(*args, **kwargs):
        calls.append(kwargs.get("ignore_geometry", False))
        if not kwargs.get("ignore_geometry"):
            raise fiona.errors.UnsupportedGeometryTypeError(2147483648)
        return real_open(*args, **kwargs)

    monkeypatch.setattr("prosuite_mcp.workspace.fiona.open", fake_open)
    result = describe_dataset(gdb, "points")

    assert calls == [False, True]
    assert result["geometry_type"] == "Unknown"
    assert result["feature_count"] == 2
    assert [f["name"] for f in result["fields"]] == ["kind", "height"]


def test_a_dataset_that_cannot_be_counted_still_reports_the_rest(gdb, monkeypatch):
    """fiona raises TypeError, not a FionaError, so this used to leave the tool
    rather than come back as a result."""
    monkeypatch.setattr(
        fiona.Collection,
        "__len__",
        lambda self: (_ for _ in ()).throw(
            TypeError("Layer does not support counting")
        ),
    )

    result = describe_dataset(gdb, "points")

    assert result["feature_count"] is None
    assert [f["name"] for f in result["fields"]] == ["kind", "height"]


def test_a_failing_retry_is_still_a_workspace_error(gdb, monkeypatch):
    """The retry runs inside the first handler, so its errors need converting
    too or the tool raises instead of returning a status."""

    def fake_open(*args, **kwargs):
        if kwargs.get("ignore_geometry"):
            raise fiona.errors.DriverError("boom")
        raise fiona.errors.UnsupportedGeometryTypeError(2147483648)

    monkeypatch.setattr("prosuite_mcp.workspace.fiona.open", fake_open)

    with pytest.raises(WorkspaceError, match="boom"):
        describe_dataset(gdb, "points")


def test_a_table_without_geometry_can_be_described(tmp_path):
    """No geometry means no extent, which must not read as a failure."""
    path = tmp_path / "notrs.gdb"
    with fiona.open(
        path,
        "w",
        driver="OpenFileGDB",
        layer="lookup",
        schema={"geometry": "None", "properties": {"code": "int32"}},
    ) as dst:
        dst.write({"geometry": None, "properties": {"code": 100}})

    result = describe_dataset(str(path), "lookup")

    assert result["geometry_type"] == "None"
    assert result["extent"] is None
    assert result["spatial_reference"] is None
    assert [f["name"] for f in result["fields"]] == ["code"]


def test_a_table_without_geometry_is_still_listed(tmp_path):
    """ProSuite tests tables too, so they must not drop out of the listing."""
    path = tmp_path / "tables.gdb"
    with fiona.open(
        path,
        "w",
        driver="OpenFileGDB",
        layer="lookup",
        schema={"geometry": "None", "properties": {"code": "int32"}},
    ) as dst:
        dst.write({"geometry": None, "properties": {"code": 100}})

    (dataset,) = list_datasets(str(path))["datasets"]

    assert dataset["name"] == "lookup"
    assert dataset["geometry_type"] == "None"
    assert dataset["feature_count"] == 1


def test_field_widths_survive_the_round_trip(gdb):
    result = describe_dataset(gdb, "points")
    kind = next(f for f in result["fields"] if f["name"] == "kind")

    assert kind["width"] == 25
    assert json.dumps(result)  # the tool returns this straight to the client
