"""Unit tests for spec.py — XML parsing, condition extraction, and search."""

from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from prosuite_mcp.spec import (
    SpecCondition,
    _descriptor_to_method,
    _to_snake,
    load_spec,
    search_spec,
)

# ---------------------------------------------------------------------------
# Minimal XML fixture
# ---------------------------------------------------------------------------

_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <DataQuality xmlns="urn:ProSuite.QA.QualitySpecifications-3.0">
      <Categories>
        <Category name="Roads">
          <QualityConditions>
            <QualityCondition name="Roads: minimum length check" testDescriptor="MinLength(0)" allowErrors="False">
              <Description>Each road must be at least 1.5 m long</Description>
              <Parameters>
                <Dataset parameter="featureClass" value="MY_ROADS" where="STATUS = 1" workspace="mydb" />
                <Scalar parameter="limit" value="1.5" />
              </Parameters>
            </QualityCondition>
            <QualityCondition name="Roads: must intersect intersections" testDescriptor="MustIntersectOther(1)" allowErrors="True">
              <Parameters>
                <Dataset parameter="featureClasses" value="MY_ROADS" workspace="mydb" />
                <Dataset parameter="otherFeatureClasses" value="MY_INTERSECTIONS" workspace="mydb" />
                <Scalar parameter="relevantRelationCondition" value="" />
              </Parameters>
            </QualityCondition>
            <QualityCondition name="Roads: transformer condition" testDescriptor="Constraint(0)" allowErrors="True">
              <Parameters>
                <Dataset parameter="table" transformerName="SomeTransformer" />
                <Scalar parameter="constraint" value="X &gt; 0" />
              </Parameters>
            </QualityCondition>
          </QualityConditions>
        </Category>
        <Category name="Buildings">
          <QualityConditions>
            <QualityCondition name="Buildings: attribute check" testDescriptor="ObjectAttributeConstraint" allowErrors="False">
              <Parameters>
                <Dataset parameter="table" value="MY_BUILDINGS" workspace="mydb" />
                <Scalar parameter="constraint" value="HEIGHT > 0" />
              </Parameters>
            </QualityCondition>
          </QualityConditions>
        </Category>
      </Categories>
    </DataQuality>
""")


@pytest.fixture()
def spec_file(tmp_path):
    p = tmp_path / "test.qa.xml"
    p.write_text(_XML, encoding="utf-8")
    return str(p)


@pytest.fixture()
def conditions(spec_file):
    return load_spec(spec_file)


# ---------------------------------------------------------------------------
# _to_snake
# ---------------------------------------------------------------------------


def test_to_snake_camel():
    assert _to_snake("featureClass") == "feature_class"


def test_to_snake_camel_plural():
    assert _to_snake("featureClasses") == "feature_classes"


def test_to_snake_long():
    assert _to_snake("relevantRelationCondition") == "relevant_relation_condition"


def test_to_snake_already_lower():
    assert _to_snake("limit") == "limit"


# ---------------------------------------------------------------------------
# _descriptor_to_method
# ---------------------------------------------------------------------------


def test_descriptor_with_version():
    assert _descriptor_to_method("MustIntersectOther(1)") == "qa_must_intersect_other_1"


def test_descriptor_version_zero():
    assert _descriptor_to_method("MinLength(0)") == "qa_min_length_0"


def test_descriptor_no_version():
    assert _descriptor_to_method("RelConstraint") == "qa_rel_constraint"


def test_descriptor_no_version_multi_word():
    assert (
        _descriptor_to_method("ObjectAttributeConstraint")
        == "qa_object_attribute_constraint"
    )


def test_descriptor_none_on_garbage():
    assert _descriptor_to_method("") is None


# ---------------------------------------------------------------------------
# load_spec — counts
# ---------------------------------------------------------------------------


def test_load_spec_total(conditions):
    assert len(conditions) == 4


def test_load_spec_supported_count(conditions):
    supported = [c for c in conditions if not c.unsupported]
    assert len(supported) == 3  # transformer one is unsupported


def test_load_spec_transformer_unsupported(conditions):
    transformer_cond = next(c for c in conditions if "transformer" in c.name)
    assert transformer_cond.unsupported is True
    assert "transformer" in transformer_cond.unsupported_reason


# ---------------------------------------------------------------------------
# load_spec — single-dataset condition (MinLength)
# ---------------------------------------------------------------------------


@pytest.fixture()
def min_length(conditions):
    return next(c for c in conditions if "minimum length" in c.name)


def test_min_length_category(min_length):
    assert min_length.category == "Roads"


def test_min_length_allow_errors(min_length):
    assert min_length.allow_errors is False


def test_min_length_description(min_length):
    assert "1.5 m" in min_length.description


def test_min_length_method(min_length):
    assert min_length.method == "qa_min_length_0"


def test_min_length_dataset_param(min_length):
    assert len(min_length.dataset_params) == 1
    dp = min_length.dataset_params[0]
    assert dp.dataset_name == "MY_ROADS"
    assert dp.filter_expression == "STATUS = 1"
    assert dp.is_list is False


def test_min_length_scalar_param(min_length):
    assert len(min_length.scalar_params) == 1
    sp = min_length.scalar_params[0]
    assert sp.py_name == "limit"
    assert sp.value == "1.5"


# ---------------------------------------------------------------------------
# load_spec — list-dataset condition (MustIntersectOther(1))
# ---------------------------------------------------------------------------


@pytest.fixture()
def must_intersect(conditions):
    return next(c for c in conditions if "must intersect" in c.name)


def test_must_intersect_allow_errors(must_intersect):
    assert must_intersect.allow_errors is True


def test_must_intersect_method(must_intersect):
    assert must_intersect.method == "qa_must_intersect_other_1"


def test_must_intersect_list_params(must_intersect):
    fc_param = next(
        p for p in must_intersect.dataset_params if p.py_name == "feature_classes"
    )
    assert fc_param.is_list is True
    assert fc_param.dataset_name == "MY_ROADS"


def test_must_intersect_other_list_params(must_intersect):
    other = next(
        p for p in must_intersect.dataset_params if p.py_name == "other_feature_classes"
    )
    assert other.is_list is True
    assert other.dataset_name == "MY_INTERSECTIONS"


# ---------------------------------------------------------------------------
# search_spec
# ---------------------------------------------------------------------------


def test_search_returns_total_matches(conditions):
    result = search_spec(conditions, "roads")
    assert result["total_matches"] == 2  # transformer excluded


def test_search_respects_max_results(conditions):
    result = search_spec(conditions, "roads", max_results=1)
    assert result["returned"] == 1
    assert result["total_matches"] == 2


def test_search_no_match(conditions):
    result = search_spec(conditions, "zzz_no_match_xyz")
    assert result["total_matches"] == 0
    assert result["results"] == []


def test_search_by_category(conditions):
    result = search_spec(conditions, "buildings")
    assert result["total_matches"] == 1
    assert result["results"][0]["category"] == "Buildings"


def test_search_result_has_condition_request(conditions):
    result = search_spec(conditions, "minimum length")
    r = result["results"][0]
    assert r["condition_request"]["condition"] == "qa_min_length_0"
    assert r["condition_request"]["params"]["limit"] == "1.5"


def test_search_result_single_dataset_is_string(conditions):
    result = search_spec(conditions, "minimum length")
    params = result["results"][0]["condition_request"]["params"]
    assert params["feature_class"] == "MY_ROADS"


def test_search_result_list_dataset_is_list(conditions):
    result = search_spec(conditions, "must intersect")
    params = result["results"][0]["condition_request"]["params"]
    assert params["feature_classes"] == ["MY_ROADS"]
    assert params["other_feature_classes"] == ["MY_INTERSECTIONS"]


def test_search_result_required_datasets(conditions):
    result = search_spec(conditions, "minimum length")
    req = result["results"][0]["required_datasets"]
    assert len(req) == 1
    assert req[0]["name"] == "MY_ROADS"
    assert req[0]["filter_expression"] == "STATUS = 1"


def test_search_result_allow_errors_surfaced(conditions):
    result = search_spec(conditions, "minimum length")
    assert result["results"][0]["allow_errors"] is False


def test_search_result_description_surfaced(conditions):
    result = search_spec(conditions, "minimum length")
    assert "1.5 m" in result["results"][0]["description"]


# ---------------------------------------------------------------------------
# search_spec MCP tool — no spec loaded
# ---------------------------------------------------------------------------


def test_search_spec_tool_no_spec_configured():
    from prosuite_mcp.server import search_spec as tool_search_spec

    with patch("prosuite_mcp.server._get_spec", return_value=None):
        result = tool_search_spec("roads")
    assert "error" in result
    assert "PROSUITE_SPEC_PATH" in result["error"]


def test_search_spec_tool_with_spec(conditions):
    from prosuite_mcp.server import search_spec as tool_search_spec

    with patch("prosuite_mcp.server._get_spec", return_value=conditions):
        result = tool_search_spec("minimum length")
    assert result["total_matches"] == 1
