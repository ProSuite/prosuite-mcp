"""Unit tests for the condition catalog built from Conditions introspection."""

import pytest

from prosuite_mcp.catalog import CATALOG, ConditionInfo, ParamInfo


def test_catalog_is_non_empty():
    assert len(CATALOG) > 0


def test_catalog_contains_known_condition():
    assert "qa3d_constant_z_0" in CATALOG


def test_condition_info_fields():
    info = CATALOG["qa3d_constant_z_0"]
    assert isinstance(info, ConditionInfo)
    assert info.method_name == "qa3d_constant_z_0"
    assert "Z" in info.docstring or "z" in info.docstring  # docstring mentions Z
    assert len(info.params) == 2  # feature_class, tolerance


def test_dataset_param_classified():
    info = CATALOG["qa3d_constant_z_0"]
    fc_param = info.params[0]
    assert fc_param.name == "feature_class"
    assert fc_param.is_dataset is True
    assert fc_param.is_dataset_list is False


def test_primitive_param_classified():
    info = CATALOG["qa3d_constant_z_0"]
    tol_param = info.params[1]
    assert tol_param.name == "tolerance"
    assert tol_param.is_dataset is False
    assert tol_param.is_dataset_list is False
    assert tol_param.type_hint == "float"


def test_list_dataset_param_classified():
    # qa_border_sense_1 takes List[BaseDataset]
    info = CATALOG["qa_border_sense_1"]
    list_param = info.params[0]
    assert list_param.name == "polyline_classes"
    assert list_param.is_dataset is True
    assert list_param.is_dataset_list is True


def test_all_catalog_entries_have_method_name():
    for name, info in CATALOG.items():
        assert info.method_name == name


def test_all_catalog_entries_have_params_list():
    for info in CATALOG.values():
        assert isinstance(info.params, list)
        for p in info.params:
            assert isinstance(p, ParamInfo)
