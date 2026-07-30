"""Unit tests for condition building and spec XML authoring."""

import textwrap
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest
from prosuite.data_model import Dataset, Model
from prosuite.quality import Condition

from prosuite_mcp import authoring
from prosuite_mcp.authoring import _build_condition, _resolve_param, add_condition
from prosuite_mcp.schemas import ConditionRequest, DatasetRef
from prosuite_mcp.spec import _NS, _parse_condition

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


def test_build_condition_omits_defaulted_params():
    """qa_curve_0 defaults allowed_non_linear_segment_types and
    group_issues_by_segment_type, so a spec that omits them still builds."""
    condition = _build_condition(
        ConditionRequest(condition="qa_curve_0", params={"feature_class": "Roads"}),
        _make_dataset_map(),
    )
    assert condition.test_descriptor.startswith("QaCurve")


def test_build_condition_still_requires_params_without_defaults():
    """Only the factory's own defaults are filled in; nothing is invented."""
    with pytest.raises(ValueError, match="Missing parameter 'limit'"):
        _build_condition(
            ConditionRequest(
                condition="qa_min_length_1", params={"feature_class": "Roads"}
            ),
            _make_dataset_map(),
        )


def test_missing_param_error_lists_only_required_params():
    with pytest.raises(ValueError, match=r"Required: \['feature_class', 'limit'\]"):
        _build_condition(
            ConditionRequest(condition="qa_min_length_1", params={}),
            _make_dataset_map(),
        )


def test_build_condition_success():
    dm = _make_dataset_map()

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
# build_condition_xml
# ---------------------------------------------------------------------------


def test_build_condition_xml_round_trips_dataset_and_scalar_params():
    xml = authoring.build_condition_xml(
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


def test_build_condition_xml_round_trips_per_condition_where_filter():
    xml = authoring.build_condition_xml(
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


def test_build_condition_xml_round_trips_list_dataset_params():
    xml = authoring.build_condition_xml(
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


def test_build_condition_xml_emits_pascal_case_allow_errors():
    xml_false = authoring.build_condition_xml(
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
    xml_true = authoring.build_condition_xml(
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


# ---------------------------------------------------------------------------
# add_condition
# ---------------------------------------------------------------------------

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


def test_add_condition_reuses_descriptor_and_wires_element(tmp_path):
    from prosuite_mcp.spec import get_spec_metadata
    from prosuite_mcp.spec import load_spec as parse_spec

    updated = add_condition(
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


def test_add_condition_rejects_missing_descriptor():
    with pytest.raises(ValueError, match="descriptor"):
        add_condition(
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


def test_add_condition_preserves_xml_declaration():
    updated = add_condition(
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


def test_add_condition_rejects_duplicate_name():
    with pytest.raises(ValueError, match="Existing_Cond"):
        add_condition(
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


def test_add_condition_builds_condition_once():
    with patch.object(
        authoring, "_build_condition", wraps=authoring._build_condition
    ) as mock_build:
        add_condition(
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
