"""Unit tests for MCP tools — all gRPC I/O is mocked."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from prosuite.data_model import Dataset, Model
from prosuite.verification import VerifiedCondition, VerifiedSpecification

from prosuite_mcp.catalog import CATALOG
from prosuite_mcp.server import (
    ConditionRequest,
    DatasetRef,
    _build_condition,
    _resolve_param,
    _summarize,
    describe_condition,
    list_conditions,
    run_verification,
)

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
# _summarize
# ---------------------------------------------------------------------------


def test_summarize():
    spec = VerifiedSpecification(
        specification_name="Test Spec",
        user_name="alice",
        verified_conditions=[
            VerifiedCondition(condition_id=1, name="cond_a", error_count=0),
            VerifiedCondition(condition_id=2, name="cond_b", error_count=0),
        ],
    )
    result = _summarize(spec, issues_by_condition={1: 3})
    assert result["total_errors"] == 3
    assert result["total_conditions"] == 2
    assert result["conditions"][0]["name"] == "cond_a"
    assert result["conditions"][0]["errors"] == 3
    assert result["conditions"][1]["errors"] == 0


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


def test_run_verification_success():
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_stream") as mock_stream,
    ):
        mock_stream.return_value = ({1: 2}, final_spec)

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


def test_run_verification_grpc_error():
    import grpc

    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "service unavailable"

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_stream") as mock_stream,
    ):
        mock_stream.side_effect = _FakeRpcError()

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


def test_run_verification_with_output_dir():
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.server._make_service"),
        patch("prosuite_mcp.server._run_stream") as mock_stream,
    ):
        mock_stream.return_value = ({1: 0}, final_spec)

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
