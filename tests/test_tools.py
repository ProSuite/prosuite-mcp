"""Unit tests for the MCP tool wrappers themselves: input parsing, config
resolution, and delegation to authoring.py / verification.py. Deep logic
(condition building, XML authoring, gRPC streaming, summarization) is tested
in test_authoring.py / test_verification.py.
"""

import textwrap
from unittest.mock import patch

import pytest

from prosuite_mcp.catalog import CATALOG
from prosuite_mcp.schemas import ConditionRequest, DatasetRef
from prosuite_mcp.tools import (
    add_condition_to_spec,
    condition_to_xml,
    describe_condition,
    describe_spec,
    list_conditions,
    load_spec,
    preview_condition_run,
    run_verification,
    run_xml_verification,
)

_MINIMAL_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <DataQuality xmlns="urn:ProSuite.QA.QualitySpecifications-3.0">
      <Categories>
        <Category name="Roads">
          <QualityConditions>
            <QualityCondition name="Roads: minimum length" testDescriptor="MinLength(0)" allowErrors="False">
              <Parameters>
                <Dataset parameter="featureClass" value="MY_ROADS" workspace="mydb" />
                <Scalar parameter="limit" value="1.5" />
              </Parameters>
            </QualityCondition>
          </QualityConditions>
        </Category>
      </Categories>
    </DataQuality>
""")


def _cfg(spec_path: str | None = None):
    from prosuite_mcp.config import Config

    return Config(host="localhost", port=5151, ssl_cert_path=None, spec_path=spec_path)


# ---------------------------------------------------------------------------
# list_conditions
# ---------------------------------------------------------------------------


def test_list_conditions_returns_all_when_no_search():
    result = list_conditions()
    lines = result.splitlines()
    assert len(lines) == len(CATALOG)


def test_list_conditions_filters_by_keyword():
    result = list_conditions(search="min_length")
    assert "min_length" in result
    # Should not include unrelated conditions
    for line in result.splitlines():
        assert "min_length" in line.lower()


def test_list_conditions_no_match():
    result = list_conditions(search="zzz_does_not_exist_xyz")
    assert "No conditions match" in result


# ---------------------------------------------------------------------------
# describe_condition
# ---------------------------------------------------------------------------


def test_describe_condition_known():
    result = describe_condition("qa3d_constant_z_0")
    assert "qa3d_constant_z_0" in result
    assert "feature_class" in result
    assert "tolerance" in result
    assert "dataset name" in result  # feature_class is a dataset param


def test_describe_condition_unknown():
    result = describe_condition("no_such_condition_xyz")
    assert "Unknown condition" in result


def test_describe_condition_unknown_suggests_similar():
    result = describe_condition("qa3d_constant")
    # Should suggest similar names since "qa3d_constant" matches condition names
    assert "Similar names" in result or "Unknown condition" in result


# ---------------------------------------------------------------------------
# load_spec / search_spec
# ---------------------------------------------------------------------------


def test_load_spec_file_not_found():
    result = load_spec("/does/not/exist.qa.xml")
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_load_spec_success(tmp_path):
    import prosuite_mcp.spec as spec_module

    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text(_MINIMAL_XML, encoding="utf-8")

    original = spec_module._loaded_conditions
    try:
        result = load_spec(str(spec_file))
        assert result["status"] == "ok"
        assert result["conditions_loaded"] == 1
        assert spec_module._loaded_conditions is not None
        assert len(spec_module._loaded_conditions) == 1
    finally:
        spec_module._loaded_conditions = original


def test_load_spec_makes_search_work(tmp_path):
    import prosuite_mcp.spec as spec_module
    from prosuite_mcp.tools import search_spec as tool_search_spec

    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text(_MINIMAL_XML, encoding="utf-8")

    original = spec_module._loaded_conditions
    try:
        load_spec(str(spec_file))
        result = tool_search_spec("minimum length")
        assert result["total_matches"] == 1
    finally:
        spec_module._loaded_conditions = original


# ---------------------------------------------------------------------------
# describe_spec
# ---------------------------------------------------------------------------


def test_describe_spec_no_spec_configured():
    with patch("prosuite_mcp.tools.load_config", return_value=_cfg(spec_path=None)):
        result = describe_spec()
    assert "error" in result
    assert "PROSUITE_SPEC_PATH" in result["error"]


def test_describe_spec_returns_metadata():
    fake_meta = {"specifications": [], "workspaces": []}
    with (
        patch(
            "prosuite_mcp.tools.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.tools.get_spec_metadata", return_value=fake_meta),
    ):
        result = describe_spec()
    assert result == fake_meta


# ---------------------------------------------------------------------------
# condition_to_xml / add_condition_to_spec -- delegation to authoring.py
# ---------------------------------------------------------------------------


def test_condition_to_xml_delegates_to_authoring():
    cond_req = ConditionRequest(condition="qa_min_length_1", params={"limit": 1.5})
    datasets = [DatasetRef(name="lines")]

    with patch(
        "prosuite_mcp.authoring.build_condition_xml", return_value="<xml/>"
    ) as mock_build:
        result = condition_to_xml(
            name="lines: minimum length",
            condition_request=cond_req,
            datasets=datasets,
            workspace_id="DATA_OSM",
            test_descriptor="MinLength(1)",
            allow_errors=True,
            description="desc",
        )

    mock_build.assert_called_once_with(
        "lines: minimum length",
        cond_req,
        datasets,
        "DATA_OSM",
        "MinLength(1)",
        True,
        "desc",
    )
    assert result == "<xml/>"


def test_add_condition_to_spec_delegates_to_authoring_when_spec_xml_given():
    cond_req = ConditionRequest(condition="qa_min_length_1", params={"limit": 2.0})
    datasets = [DatasetRef(name="lines")]

    with patch(
        "prosuite_mcp.authoring.add_condition", return_value="<xml/>"
    ) as mock_add:
        result = add_condition_to_spec(
            target_specification_name="MySpec",
            name="lines minlen",
            condition_request=cond_req,
            datasets=datasets,
            workspace_id="DATA_OSM",
            spec_xml="<spec/>",
        )

    mock_add.assert_called_once_with(
        "MySpec",
        "lines minlen",
        cond_req,
        datasets,
        "DATA_OSM",
        "<spec/>",
        False,
        "",
    )
    assert result == "<xml/>"


def test_add_condition_to_spec_reads_configured_spec_path_when_omitted(tmp_path):
    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text("<spec/>", encoding="utf-8")

    with (
        patch(
            "prosuite_mcp.tools.load_config",
            return_value=_cfg(spec_path=str(spec_file)),
        ),
        patch(
            "prosuite_mcp.authoring.add_condition", return_value="<updated/>"
        ) as mock_add,
    ):
        result = add_condition_to_spec(
            target_specification_name="MySpec",
            name="lines minlen",
            condition_request=ConditionRequest(
                condition="qa_min_length_1",
                params={"feature_class": "lines", "limit": 2.0},
            ),
            datasets=[DatasetRef(name="lines")],
            workspace_id="DATA_OSM",
        )

    assert result == "<updated/>"
    assert mock_add.call_args[0][5] == "<spec/>"


def test_add_condition_to_spec_raises_without_spec_xml_or_configured_path():
    with patch("prosuite_mcp.tools.load_config", return_value=_cfg(spec_path=None)):
        with pytest.raises(ValueError, match="No spec loaded"):
            add_condition_to_spec(
                target_specification_name="MySpec",
                name="lines minlen",
                condition_request=ConditionRequest(
                    condition="qa_min_length_1",
                    params={"feature_class": "lines", "limit": 2.0},
                ),
                datasets=[DatasetRef(name="lines")],
                workspace_id="DATA_OSM",
            )


def test_add_condition_to_spec_raises_value_error_when_configured_path_missing():
    with patch(
        "prosuite_mcp.tools.load_config",
        return_value=_cfg(spec_path="/does/not/exist.qa.xml"),
    ):
        with pytest.raises(ValueError, match="Could not read spec file"):
            add_condition_to_spec(
                target_specification_name="MySpec",
                name="lines minlen",
                condition_request=ConditionRequest(
                    condition="qa_min_length_1",
                    params={"feature_class": "lines", "limit": 2.0},
                ),
                datasets=[DatasetRef(name="lines")],
                workspace_id="DATA_OSM",
            )


# ---------------------------------------------------------------------------
# run_verification / preview_condition_run -- delegation to verification.py
# ---------------------------------------------------------------------------


def test_run_verification_delegates_to_verification_impl():
    cond_req = ConditionRequest(
        condition="qa3d_constant_z_0",
        params={"feature_class": "Roads", "tolerance": 0.01},
    )
    datasets = [DatasetRef(name="Roads")]

    with patch(
        "prosuite_mcp.verification._run_verification_impl",
        return_value={"status": "success"},
    ) as mock_impl:
        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=datasets,
            conditions=[cond_req],
            output_dir="C:/output",
            envelope={"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
        )

    mock_impl.assert_called_once_with(
        "C:/test.gdb",
        "TestModel",
        datasets,
        [cond_req],
        "C:/output",
        {"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
        "adhoc",
    )
    assert result == {"status": "success"}


def test_preview_condition_run_forwards_params_to_shared_impl():
    cond_req = ConditionRequest(
        condition="qa3d_constant_z_0",
        params={"feature_class": "Roads", "tolerance": 0.01},
    )
    datasets = [DatasetRef(name="Roads")]

    with patch(
        "prosuite_mcp.verification._run_verification_impl",
        return_value={"status": "success"},
    ) as mock_impl:
        result = preview_condition_run(
            model_catalog_path="C:/test.gdb",
            condition_request=cond_req,
            datasets=datasets,
            workspace_id="TestModel",
            output_dir="C:/output",
            envelope={"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
        )

    mock_impl.assert_called_once_with(
        "C:/test.gdb",
        "TestModel",
        datasets,
        [cond_req],
        "C:/output",
        {"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
        "preview",
    )
    assert result == {"status": "success"}


# ---------------------------------------------------------------------------
# run_xml_verification
# ---------------------------------------------------------------------------


def test_run_xml_verification_no_spec_configured():
    with patch("prosuite_mcp.tools.load_config", return_value=_cfg(spec_path=None)):
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
        )
    assert result["status"] == "error"
    assert "PROSUITE_SPEC_PATH" in result["error"]
    assert result["engine_confirmed"] is False


def test_run_xml_verification_delegates_to_verification_impl():
    from prosuite_mcp.schemas import WorkspaceReplacement

    with (
        patch(
            "prosuite_mcp.tools.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch(
            "prosuite_mcp.verification.run_xml_verification_impl",
            return_value={"status": "success"},
        ) as mock_impl,
    ):
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[
                WorkspaceReplacement(
                    workspace_id="DATA_OSM", workspace_path="C:/data/osm.sde"
                )
            ],
            output_dir="C:/output",
            envelope={"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
        )

    mock_impl.assert_called_once_with(
        "/tmp/x.qa.xml",
        "Spec_A",
        [["DATA_OSM", "C:/data/osm.sde"]],
        "C:/output",
        {"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1},
    )
    assert result == {"status": "success"}
