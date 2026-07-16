"""Unit tests for MCP tools — all gRPC I/O is mocked."""

import textwrap
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from prosuite.data_model import Dataset, Model
from prosuite.verification import VerifiedCondition, VerifiedSpecification

from prosuite_mcp.catalog import CATALOG
from prosuite_mcp.server import (
    ConditionRequest,
    DatasetRef,
    StreamOutcome,
    _build_condition,
    _make_run_dir,
    _resolve_param,
    _summarize,
    describe_condition,
    describe_spec,
    list_conditions,
    load_spec,
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
# load_spec
# ---------------------------------------------------------------------------


def test_load_spec_file_not_found():
    result = load_spec("/does/not/exist.qa.xml")
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_load_spec_success(tmp_path):
    import prosuite_mcp.server as srv

    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text(_MINIMAL_XML, encoding="utf-8")

    original = srv._spec_conditions
    try:
        result = load_spec(str(spec_file))
        assert result["status"] == "ok"
        assert result["conditions_loaded"] == 1
        assert srv._spec_conditions is not None
        assert len(srv._spec_conditions) == 1
    finally:
        srv._spec_conditions = original


def test_load_spec_makes_search_work(tmp_path):
    import prosuite_mcp.server as srv
    from prosuite_mcp.server import search_spec as tool_search_spec

    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text(_MINIMAL_XML, encoding="utf-8")

    original = srv._spec_conditions
    try:
        load_spec(str(spec_file))
        result = tool_search_spec("minimum length")
        assert result["total_matches"] == 1
    finally:
        srv._spec_conditions = original


# ---------------------------------------------------------------------------
# _resolve_param
# ---------------------------------------------------------------------------


def _make_dataset_map() -> dict[str, Dataset]:
    m = Model("TestModel", "C:/test.gdb")
    return {
        "Roads": Dataset("Roads", m),
        "Buildings": Dataset("Buildings", m),
    }


def _param_info(is_dataset: bool, is_dataset_list: bool):
    from prosuite_mcp.catalog import ParamInfo

    return ParamInfo(
        name="fc",
        type_hint="BaseDataset",
        is_dataset=is_dataset,
        is_dataset_list=is_dataset_list,
    )


def test_resolve_param_primitive():
    p = _param_info(is_dataset=False, is_dataset_list=False)
    assert _resolve_param(42.0, p, {}) == 42.0


def test_resolve_param_dataset():
    p = _param_info(is_dataset=True, is_dataset_list=False)
    dm = _make_dataset_map()
    result = _resolve_param("Roads", p, dm)
    assert isinstance(result, Dataset)
    assert result.name == "Roads"


def test_resolve_param_dataset_not_found():
    p = _param_info(is_dataset=True, is_dataset_list=False)
    with pytest.raises(ValueError, match="not found"):
        _resolve_param("Missing", p, _make_dataset_map())


def test_resolve_param_dataset_list():
    p = _param_info(is_dataset=True, is_dataset_list=True)
    dm = _make_dataset_map()
    result = _resolve_param(["Roads", "Buildings"], p, dm)
    assert len(result) == 2
    assert all(isinstance(d, Dataset) for d in result)


def test_resolve_param_dataset_list_single_string_coerced():
    p = _param_info(is_dataset=True, is_dataset_list=True)
    dm = _make_dataset_map()
    result = _resolve_param("Roads", p, dm)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_condition
# ---------------------------------------------------------------------------


def test_build_condition_unknown():
    with pytest.raises(ValueError, match="Unknown condition"):
        _build_condition(
            ConditionRequest(condition="no_such_condition_xyz", params={}),
            {},
        )


def test_build_condition_missing_param():
    dm = _make_dataset_map()
    with pytest.raises(ValueError, match="Missing parameter"):
        _build_condition(
            ConditionRequest(condition="qa3d_constant_z_0", params={}),
            dm,
        )


def test_build_condition_success():
    dm = _make_dataset_map()
    from prosuite.quality import Condition

    condition = _build_condition(
        ConditionRequest(
            condition="qa3d_constant_z_0",
            params={"feature_class": "Roads", "tolerance": 0.01},
        ),
        dm,
    )
    assert isinstance(condition, Condition)
    assert condition.test_descriptor == "Qa3dConstantZ(0)"


# ---------------------------------------------------------------------------
# _decode_issue
# ---------------------------------------------------------------------------


def test_decode_issue_maps_core_fields():
    from types import SimpleNamespace

    from prosuite_mcp.server import _decode_issue

    issue = SimpleNamespace(
        issue_code="MinimumLength.LengthTooSmall",
        description="Length 341.96 < 1,000,000.00",
        allowable=False,
        involved_objects=[
            SimpleNamespace(table_name="lines", object_ids=[1]),
        ],
    )

    assert _decode_issue(issue) == {
        "issue_code": "MinimumLength.LengthTooSmall",
        "description": "Length 341.96 < 1,000,000.00",
        "allowable": False,
        "involved": [{"table_name": "lines", "object_ids": [1]}],
    }


# ---------------------------------------------------------------------------
# _run_verify (stream aggregation)
# ---------------------------------------------------------------------------


def _fake_issue(code: str, table: str, allowable: bool, condition_id: int = 0):
    from types import SimpleNamespace

    return SimpleNamespace(
        issue_code=code,
        description="d",
        allowable=allowable,
        condition_id=condition_id,
        involved_objects=[SimpleNamespace(table_name=table, object_ids=[1])],
    )


def test_run_verify_aggregates_stream_without_retaining_all_issues():
    from types import SimpleNamespace

    from prosuite_mcp.server import _run_verify

    responses = [
        SimpleNamespace(
            issues=[
                _fake_issue("A", "lines", allowable=False, condition_id=1),
                _fake_issue("A", "lines", allowable=False, condition_id=1),
            ],
            verified_specification=None,
        ),
        SimpleNamespace(
            issues=[_fake_issue("B", "points", allowable=True, condition_id=2)],
            verified_specification="SPEC",
        ),
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))

    outcome, verified = _run_verify(service, spec=None, output_dir="", perimeter=None)

    assert verified == "SPEC"
    assert outcome.total == 3
    assert outcome.errors == 2  # allowable is False
    assert outcome.warnings == 1  # allowable is True
    assert outcome.counts_by_code == {"A": 2, "B": 1}
    assert outcome.counts_by_table == {"lines": 2, "points": 1}
    # counts_by_condition only tallies allowable=False issues, same filter as
    # total_errors, so the two stay consistent by construction.
    assert outcome.counts_by_condition == {1: 2}
    assert len(outcome.sample) == 3
    assert outcome.sample[0]["issue_code"] == "A"


# ---------------------------------------------------------------------------
# condition_to_xml (spec authoring / emission)
# ---------------------------------------------------------------------------


def test_condition_to_xml_round_trips_dataset_and_scalar_params():
    import xml.etree.ElementTree as ET

    from prosuite_mcp.server import condition_to_xml
    from prosuite_mcp.spec import _NS, _parse_condition

    xml = condition_to_xml(
        name="lines: minimum length",
        condition_request=ConditionRequest(
            condition="qa_min_length_1",
            params={"feature_class": "lines", "limit": 1.5},
        ),
        datasets=[DatasetRef(name="lines")],
        workspace_id="DATA_OSM",
        test_descriptor="MinLength(1)",
        allow_errors=False,
    )

    el = ET.fromstring(xml)
    parsed = _parse_condition(el, "")

    assert parsed.name == "lines: minimum length"
    assert parsed.method == "qa_min_length_1"
    assert parsed.allow_errors is False
    assert parsed.dataset_params[0].py_name == "feature_class"
    assert parsed.dataset_params[0].dataset_name == "lines"
    assert {s.py_name: s.value for s in parsed.scalar_params}["limit"] == "1.5"
    # workspace binding is emitted on the Dataset element
    ds_el = el.find(".//qa:Dataset", _NS)
    assert ds_el is not None and ds_el.get("workspace") == "DATA_OSM"


def test_condition_to_xml_round_trips_per_condition_where_filter():
    import xml.etree.ElementTree as ET

    from prosuite_mcp.server import condition_to_xml
    from prosuite_mcp.spec import _parse_condition

    xml = condition_to_xml(
        name="natur subtype 0: minimum length",
        condition_request=ConditionRequest(
            condition="qa_min_length_1",
            params={"feature_class": "Natur", "limit": 2.0},
        ),
        datasets=[DatasetRef(name="Natur", filter_expression="subtype=0")],
        workspace_id="DATA_OSM",
        test_descriptor="MinLength(1)",
    )

    parsed = _parse_condition(ET.fromstring(xml), "")
    assert parsed.dataset_params[0].filter_expression == "subtype=0"


def test_condition_to_xml_round_trips_list_dataset_params():
    import xml.etree.ElementTree as ET

    from prosuite_mcp.server import condition_to_xml
    from prosuite_mcp.spec import _parse_condition

    xml = condition_to_xml(
        name="border sense over two classes",
        condition_request=ConditionRequest(
            condition="qa_border_sense_1",
            params={"polyline_classes": ["Eisenbahn", "Strassen"], "clockwise": True},
        ),
        datasets=[DatasetRef(name="Eisenbahn"), DatasetRef(name="Strassen")],
        workspace_id="DATA_OSM",
        test_descriptor="BorderSense(1)",
    )

    parsed = _parse_condition(ET.fromstring(xml), "")
    list_params = [
        dp for dp in parsed.dataset_params if dp.py_name == "polyline_classes"
    ]
    assert len(list_params) == 2
    assert {dp.dataset_name for dp in list_params} == {"Eisenbahn", "Strassen"}
    assert all(dp.is_list for dp in list_params)
    assert {s.py_name: s.value for s in parsed.scalar_params}["clockwise"] == "True"


_SPEC_FOR_AUTHORING = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <DataQuality xmlns="urn:ProSuite.QA.QualitySpecifications-3.0">
      <QualitySpecifications>
        <QualitySpecification name="MySpec">
          <Elements>
            <Element qualityCondition="Existing_Cond" />
          </Elements>
        </QualitySpecification>
      </QualitySpecifications>
      <QualityConditions>
        <QualityCondition name="Existing_Cond" testDescriptor="MinLength(1)">
          <Parameters>
            <Dataset parameter="featureClass" value="Roads" workspace="DATA_OSM" />
            <Scalar parameter="limit" value="5" />
          </Parameters>
        </QualityCondition>
      </QualityConditions>
      <TestDescriptors>
        <TestDescriptor name="MinLength(1)">
          <TestClass type="EsriDE.ProSuite.QA.Tests.QaMinLength" assembly="EsriDE.ProSuite.QA.Tests" constructorIndex="1" />
        </TestDescriptor>
      </TestDescriptors>
      <Workspaces>
        <Workspace id="DATA_OSM" modelName="osm" />
      </Workspaces>
    </DataQuality>
""")


def test_add_condition_to_spec_reuses_descriptor_and_wires_element(tmp_path):
    from prosuite_mcp.server import add_condition_to_spec
    from prosuite_mcp.spec import get_spec_metadata
    from prosuite_mcp.spec import load_spec as parse_spec

    updated = add_condition_to_spec(
        spec_xml=_SPEC_FOR_AUTHORING,
        target_specification_name="MySpec",
        name="lines minlen",
        condition_request=ConditionRequest(
            condition="qa_min_length_1",
            params={"feature_class": "lines", "limit": 2.0},
        ),
        datasets=[DatasetRef(name="lines")],
        workspace_id="DATA_OSM",
    )

    out = tmp_path / "updated.qa.xml"
    out.write_text(updated, encoding="utf-8")

    # New condition parses, reusing the spec's existing descriptor alias
    conditions = {c.name: c for c in parse_spec(str(out))}
    assert "lines minlen" in conditions
    assert conditions["lines minlen"].method == "qa_min_length_1"

    # And it is wired into the target specification
    meta = get_spec_metadata(str(out))
    myspec = next(
        s for s in meta["specifications"] if s["specification_name"] == "MySpec"
    )
    assert myspec["condition_count"] == 2
    assert "lines" in myspec["datasets"]


def test_add_condition_to_spec_rejects_missing_descriptor():
    from prosuite_mcp.server import add_condition_to_spec

    with pytest.raises(ValueError, match="descriptor"):
        add_condition_to_spec(
            spec_xml=_SPEC_FOR_AUTHORING,
            target_specification_name="MySpec",
            name="simple geom",
            condition_request=ConditionRequest(
                condition="qa_simple_geometry_0",
                params={"feature_class": "lines"},
            ),
            datasets=[DatasetRef(name="lines")],
            workspace_id="DATA_OSM",
        )


def test_condition_to_xml_emits_pascal_case_allow_errors():
    from prosuite_mcp.server import condition_to_xml

    xml_false = condition_to_xml(
        name="lines: minimum length",
        condition_request=ConditionRequest(
            condition="qa_min_length_1",
            params={"feature_class": "lines", "limit": 1.5},
        ),
        datasets=[DatasetRef(name="lines")],
        workspace_id="DATA_OSM",
        test_descriptor="MinLength(1)",
        allow_errors=False,
    )
    xml_true = condition_to_xml(
        name="lines: minimum length",
        condition_request=ConditionRequest(
            condition="qa_min_length_1",
            params={"feature_class": "lines", "limit": 1.5},
        ),
        datasets=[DatasetRef(name="lines")],
        workspace_id="DATA_OSM",
        test_descriptor="MinLength(1)",
        allow_errors=True,
    )

    # allowErrors maps to ProSuite's Override enum (Null/True/False), not
    # xs:boolean; the real engine rejects lowercase "false"/"true" here.
    assert 'allowErrors="False"' in xml_false
    assert 'allowErrors="True"' in xml_true


def test_add_condition_to_spec_preserves_xml_declaration():
    from prosuite_mcp.server import add_condition_to_spec

    updated = add_condition_to_spec(
        spec_xml=_SPEC_FOR_AUTHORING,
        target_specification_name="MySpec",
        name="lines minlen",
        condition_request=ConditionRequest(
            condition="qa_min_length_1",
            params={"feature_class": "lines", "limit": 2.0},
        ),
        datasets=[DatasetRef(name="lines")],
        workspace_id="DATA_OSM",
    )

    assert updated.startswith('<?xml version="1.0" encoding="utf-8"?>')


def test_add_condition_to_spec_rejects_duplicate_name():
    from prosuite_mcp.server import add_condition_to_spec

    with pytest.raises(ValueError, match="Existing_Cond"):
        add_condition_to_spec(
            spec_xml=_SPEC_FOR_AUTHORING,
            target_specification_name="MySpec",
            name="Existing_Cond",
            condition_request=ConditionRequest(
                condition="qa_min_length_1",
                params={"feature_class": "lines", "limit": 2.0},
            ),
            datasets=[DatasetRef(name="lines")],
            workspace_id="DATA_OSM",
        )


def test_add_condition_to_spec_builds_condition_once():
    import prosuite_mcp.server as server_module

    with patch.object(
        server_module, "_build_condition", wraps=server_module._build_condition
    ) as mock_build:
        server_module.add_condition_to_spec(
            spec_xml=_SPEC_FOR_AUTHORING,
            target_specification_name="MySpec",
            name="lines minlen",
            condition_request=ConditionRequest(
                condition="qa_min_length_1",
                params={"feature_class": "lines", "limit": 2.0},
            ),
            datasets=[DatasetRef(name="lines")],
            workspace_id="DATA_OSM",
        )

    assert mock_build.call_count == 1


def test_add_condition_to_spec_reads_configured_spec_path_when_omitted(tmp_path):
    from prosuite_mcp.server import add_condition_to_spec

    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text(_SPEC_FOR_AUTHORING, encoding="utf-8")

    with patch(
        "prosuite_mcp.server.load_config",
        return_value=_cfg(spec_path=str(spec_file)),
    ):
        updated = add_condition_to_spec(
            target_specification_name="MySpec",
            name="lines minlen",
            condition_request=ConditionRequest(
                condition="qa_min_length_1",
                params={"feature_class": "lines", "limit": 2.0},
            ),
            datasets=[DatasetRef(name="lines")],
            workspace_id="DATA_OSM",
        )

    assert "lines minlen" in updated


def test_add_condition_to_spec_raises_without_spec_xml_or_configured_path():
    from prosuite_mcp.server import add_condition_to_spec

    with patch("prosuite_mcp.server.load_config", return_value=_cfg(spec_path=None)):
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
    from prosuite_mcp.server import add_condition_to_spec

    with patch(
        "prosuite_mcp.server.load_config",
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
# _summarize
# ---------------------------------------------------------------------------


def test_summarize():
    spec = VerifiedSpecification(
        specification_name="Test Spec",
        user_name="alice",
        verified_conditions=[
            VerifiedCondition(condition_id=1, name="cond_a", error_count=3),
            VerifiedCondition(condition_id=2, name="cond_b", error_count=0),
        ],
    )
    # total_errors and the per-condition breakdown both come from the stream
    # (counts_by_condition), so they are consistent by construction; the
    # engine's own error_count on VerifiedCondition is unused.
    result = _summarize(
        spec,
        StreamOutcome(total=3, errors=3, warnings=0, counts_by_condition={1: 3}),
    )
    assert result["total_errors"] == 3
    assert result["total_warnings"] == 0
    assert result["total_conditions"] == 2
    assert result["issues_seen_in_stream"] == 3
    assert result["conditions"][0]["name"] == "cond_a"
    assert result["conditions"][0]["errors"] == 3
    assert result["conditions"][1]["errors"] == 0
    assert result["unmatched_condition_errors"] == 0
    assert sum(c["errors"] for c in result["conditions"]) == result["total_errors"]


def test_summarize_reports_unmatched_condition_errors():
    # An issue's condition_id (99) that doesn't correspond to any verified
    # condition would otherwise be silently dropped from the per-condition
    # breakdown while still counting toward total_errors. unmatched_condition_errors
    # surfaces that gap instead of hiding it.
    spec = VerifiedSpecification(
        specification_name="Test Spec",
        user_name="alice",
        verified_conditions=[
            VerifiedCondition(condition_id=1, name="cond_a", error_count=0),
        ],
    )
    result = _summarize(
        spec,
        StreamOutcome(total=5, errors=5, warnings=0, counts_by_condition={1: 3, 99: 2}),
    )

    assert result["total_errors"] == 5
    assert result["conditions"][0]["errors"] == 3
    assert result["unmatched_condition_errors"] == 2
    assert (
        sum(c["errors"] for c in result["conditions"])
        + result["unmatched_condition_errors"]
        == result["total_errors"]
    )


# ---------------------------------------------------------------------------
# run_verification (mocked service)
# ---------------------------------------------------------------------------


def _mock_verified_spec() -> VerifiedSpecification:
    return VerifiedSpecification(
        specification_name="prosuite-mcp verification",
        user_name="",
        verified_conditions=[
            VerifiedCondition(
                condition_id=1, name="Roads_Qa3dConstantZ(0)", error_count=2
            ),
        ],
    )


def test_run_verification_success(tmp_path):
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_verify") as mock_stream,
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_stream.return_value = (
            StreamOutcome(total=2, errors=2, counts_by_condition={1: 2}),
            final_spec,
        )
        mock_path.cwd.return_value = tmp_path

        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
        )

    assert result["status"] == "success"
    assert result["total_errors"] == 2
    assert result["total_conditions"] == 1
    assert result["conditions"][0]["errors"] == 2


def test_run_verification_total_errors_from_stream_when_conditions_empty(tmp_path):
    # Ad-hoc runs come back with empty/nameless verified_conditions, so the old
    # sum-of-error_count reported 0 even when the stream carried real issues.
    empty_spec = VerifiedSpecification(
        specification_name="prosuite-mcp verification",
        user_name="",
        verified_conditions=[],
    )

    with (
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(total=5, errors=4, warnings=1), empty_spec),
        ),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
        )

    assert result["total_errors"] == 4
    assert result["total_warnings"] == 1
    assert result["issues_seen_in_stream"] == 5


def test_run_verification_exposes_sample_and_counts(tmp_path):
    spec = _mock_verified_spec()
    sample = [
        {"issue_code": "A", "description": "d", "allowable": False, "involved": []},
    ]
    outcome = StreamOutcome(
        total=3,
        errors=2,
        warnings=1,
        counts_by_code={"A": 2, "B": 1},
        counts_by_table={"lines": 3},
        sample=sample,
    )

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_verify", return_value=(outcome, spec)),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
        )

    assert result["issue_counts_by_code"] == {"A": 2, "B": 1}
    assert result["issue_counts_by_table"] == {"lines": 3}
    assert result["sample_features"] == sample


def test_run_verification_engine_confirmed_true_on_success(tmp_path):
    spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(total=1, errors=1), spec),
        ),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
        )

    assert result["engine_confirmed"] is True


def test_run_verification_engine_confirmed_false_without_final_summary(tmp_path):
    with (
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(total=3), None),
        ),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
        )

    assert result["status"] == "error"
    assert result["engine_confirmed"] is False


def test_run_verification_grpc_error(tmp_path):
    import grpc

    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "service unavailable"

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_verify") as mock_stream,
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_stream.side_effect = _FakeRpcError()
        mock_path.cwd.return_value = tmp_path

        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
        )

    assert result["status"] == "error"
    assert "unavailable" in result["error"].lower()
    assert result["engine_confirmed"] is False


def test_run_verification_unknown_condition():
    result = run_verification(
        model_catalog_path="C:/test.gdb",
        model_name="TestModel",
        datasets=[DatasetRef(name="Roads")],
        conditions=[
            ConditionRequest(
                condition="no_such_condition_xyz",
                params={},
            )
        ],
    )
    assert result["status"] == "error"
    assert "Unknown condition" in result["error"]
    assert result["engine_confirmed"] is False


def test_run_verification_with_output_dir():
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_verify") as mock_stream,
    ):
        mock_stream.return_value = (StreamOutcome(), final_spec)

        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
            output_dir="C:/output",
        )

    assert result["status"] == "success"
    assert result["output_dir"] == "C:/output"


# ---------------------------------------------------------------------------
# preview_condition_run
# ---------------------------------------------------------------------------


def test_preview_condition_run_surfaces_flagged_features(tmp_path):
    from prosuite_mcp.server import preview_condition_run

    spec = _mock_verified_spec()
    sample = [
        {"issue_code": "A", "description": "d", "allowable": False, "involved": []},
    ]
    outcome = StreamOutcome(
        total=1, errors=1, counts_by_condition={1: 1}, sample=sample
    )

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_verify", return_value=(outcome, spec)),
        patch(
            "prosuite_mcp.server._make_run_dir", return_value=tmp_path / "preview_run"
        ),
    ):
        result = preview_condition_run(
            model_catalog_path="C:/test.gdb",
            condition_request=ConditionRequest(
                condition="qa3d_constant_z_0",
                params={"feature_class": "Roads", "tolerance": 0.01},
            ),
            datasets=[DatasetRef(name="Roads")],
            workspace_id="TestModel",
        )

    assert result["status"] == "success"
    assert result["engine_confirmed"] is True
    assert result["total_errors"] == 1
    assert result["sample_features"] == sample


def test_preview_condition_run_does_not_create_dir_on_invalid_condition():
    from prosuite_mcp.server import preview_condition_run

    with patch("prosuite_mcp.server._make_run_dir") as mock_make_run_dir:
        result = preview_condition_run(
            model_catalog_path="C:/test.gdb",
            condition_request=ConditionRequest(
                condition="no_such_condition_xyz", params={}
            ),
            datasets=[DatasetRef(name="Roads")],
            workspace_id="TestModel",
        )

    assert result["status"] == "error"
    mock_make_run_dir.assert_not_called()


def test_preview_condition_run_uses_preview_prefixed_run_dir(tmp_path):
    from prosuite_mcp.server import preview_condition_run

    with (
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(), _mock_verified_spec()),
        ),
        patch(
            "prosuite_mcp.server._make_run_dir", return_value=tmp_path / "run"
        ) as mock_make_run_dir,
    ):
        preview_condition_run(
            model_catalog_path="C:/test.gdb",
            condition_request=ConditionRequest(
                condition="qa3d_constant_z_0",
                params={"feature_class": "Roads", "tolerance": 0.01},
            ),
            datasets=[DatasetRef(name="Roads")],
            workspace_id="TestModel",
        )

    assert mock_make_run_dir.call_args[0][0] == "preview"


def test_preview_condition_run_forwards_params_to_shared_impl():
    from prosuite_mcp.server import preview_condition_run

    cond_req = ConditionRequest(
        condition="qa3d_constant_z_0",
        params={"feature_class": "Roads", "tolerance": 0.01},
    )
    datasets = [DatasetRef(name="Roads")]

    with patch(
        "prosuite_mcp.server._run_verification_impl",
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
# describe_spec
# ---------------------------------------------------------------------------


def _cfg(spec_path: str | None = None):
    from prosuite_mcp.config import Config

    return Config(host="localhost", port=5151, ssl_cert_path=None, spec_path=spec_path)


def test_describe_spec_no_spec_configured():
    with patch("prosuite_mcp.server.load_config", return_value=_cfg(spec_path=None)):
        result = describe_spec()
    assert "error" in result
    assert "PROSUITE_SPEC_PATH" in result["error"]


def test_describe_spec_returns_metadata():
    fake_meta = {"specifications": [], "workspaces": []}
    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.server.get_spec_metadata", return_value=fake_meta),
    ):
        result = describe_spec()
    assert result == fake_meta


# ---------------------------------------------------------------------------
# run_xml_verification
# ---------------------------------------------------------------------------


def _mock_xml_verified_spec() -> VerifiedSpecification:
    return VerifiedSpecification(
        specification_name="Spec_A",
        user_name="",
        verified_conditions=[
            VerifiedCondition(condition_id=10, name="Cond_A", error_count=0),
        ],
    )


def test_run_xml_verification_no_spec_configured():
    with patch("prosuite_mcp.server.load_config", return_value=_cfg(spec_path=None)):
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
        )
    assert result["status"] == "error"
    assert "PROSUITE_SPEC_PATH" in result["error"]
    assert result["engine_confirmed"] is False


def test_run_xml_verification_spec_load_failure():
    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch(
            "prosuite_mcp.server.XmlSpecification",
            side_effect=ValueError("bad spec"),
        ),
    ):
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
        )
    assert result["status"] == "error"
    assert "Failed to load spec" in result["error"]
    assert result["engine_confirmed"] is False


def test_run_xml_verification_success(tmp_path):
    final_spec = _mock_xml_verified_spec()

    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.server.XmlSpecification"),
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
        )

    assert result["status"] == "success"
    assert result["total_errors"] == 0
    assert result["conditions"][0]["name"] == "Cond_A"


def test_run_xml_verification_grpc_error(tmp_path):
    import grpc

    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "service unavailable"

    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.server.XmlSpecification"),
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_verify", side_effect=_FakeRpcError()),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
        )

    assert result["status"] == "error"
    assert "unavailable" in result["error"].lower()
    assert result["engine_confirmed"] is False


def test_run_xml_verification_no_final_summary(tmp_path):
    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.server.XmlSpecification"),
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(total=3), None),
        ),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
        )

    assert result["status"] == "error"
    assert result["issues_seen_in_stream"] == 3


def test_run_xml_verification_output_dir_in_result():
    final_spec = _mock_xml_verified_spec()

    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.server.XmlSpecification"),
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
    ):
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
            output_dir="C:/output",
        )

    assert result["status"] == "success"
    assert result["output_dir"] == "C:/output"


# ---------------------------------------------------------------------------
# _make_run_dir
# ---------------------------------------------------------------------------


def test_make_run_dir_creates_directory(tmp_path):
    result = _make_run_dir("MySpec", tmp_path)
    assert result.exists()
    assert result.is_dir()


def test_make_run_dir_is_inside_base(tmp_path):
    result = _make_run_dir("MySpec", tmp_path)
    assert result.parent == tmp_path


def test_make_run_dir_name_contains_spec(tmp_path):
    result = _make_run_dir("MySpec", tmp_path)
    assert "MySpec" in result.name


def test_make_run_dir_sanitizes_spaces_and_special_chars(tmp_path):
    result = _make_run_dir("Copy of DATA OSM 10/Demo", tmp_path)
    assert " " not in result.name
    assert "/" not in result.name


# ---------------------------------------------------------------------------
# run_xml_verification -- auto output_dir
# ---------------------------------------------------------------------------


def test_run_xml_verification_auto_creates_output_dir(tmp_path):
    final_spec = _mock_xml_verified_spec()

    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.server.XmlSpecification"),
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
        )

    assert "output_dir" in result
    assert result["status"] == "success"


def test_run_xml_verification_explicit_output_dir_not_overridden(tmp_path):
    final_spec = _mock_xml_verified_spec()

    with (
        patch(
            "prosuite_mcp.server.load_config",
            return_value=_cfg(spec_path="/tmp/x.qa.xml"),
        ),
        patch("prosuite_mcp.server.XmlSpecification"),
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
    ):
        result = run_xml_verification(
            specification_name="Spec_A",
            data_source_replacements=[],
            output_dir="C:/my_dir",
        )

    assert result["output_dir"] == "C:/my_dir"


# ---------------------------------------------------------------------------
# run_verification -- auto output_dir
# ---------------------------------------------------------------------------


def test_run_verification_auto_creates_output_dir(tmp_path):
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(total=2, errors=2), final_spec),
        ),
        patch("prosuite_mcp.server.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
        )

    assert "output_dir" in result
    assert result["status"] == "success"


def test_run_verification_explicit_output_dir_not_overridden(tmp_path):
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.server._make_service"),
        patch(
            "prosuite_mcp.server._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
    ):
        result = run_verification(
            model_catalog_path="C:/test.gdb",
            model_name="TestModel",
            datasets=[DatasetRef(name="Roads")],
            conditions=[
                ConditionRequest(
                    condition="qa3d_constant_z_0",
                    params={"feature_class": "Roads", "tolerance": 0.01},
                )
            ],
            output_dir="C:/my_dir",
        )

    assert result["output_dir"] == "C:/my_dir"
