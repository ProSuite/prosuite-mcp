"""Unit tests for the MCP tool wrappers themselves: input parsing, config
resolution, and delegation to authoring.py / verification.py. Deep logic
(condition building, XML authoring, gRPC streaming, summarization) is tested
in test_authoring.py / test_verification.py.
"""

import textwrap
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anyio.to_thread
import pytest

from prosuite_mcp.catalog import CATALOG
from prosuite_mcp.schemas import ConditionRequest, DatasetRef
from prosuite_mcp.tools import (
    _progress_relay,
    add_condition_to_spec,
    describe_condition,
    describe_dataset,
    describe_spec,
    get_verification_result,
    get_verification_status,
    list_conditions,
    list_datasets,
    list_verification_runs,
    load_spec,
    preview_condition_run,
    run_verification,
    run_xml_verification,
    start_verification,
    start_xml_verification,
)
from prosuite_mcp.verification import ProgressEvent
from prosuite_mcp.workspace import WorkspaceError

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


@pytest.fixture(autouse=True)
def _isolate_spec_state(monkeypatch):
    """Start every test with no spec loaded and none configured.

    The active spec is module-level state in spec.py, so without this a
    load_spec call in one test would leak into the next.
    """
    import prosuite_mcp.spec as spec_module

    saved = (spec_module._active_spec_path, spec_module._loaded_conditions)
    spec_module._active_spec_path = None
    spec_module._loaded_conditions = None
    monkeypatch.delenv("PROSUITE_SPEC_PATH", raising=False)
    yield
    spec_module._active_spec_path, spec_module._loaded_conditions = saved


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


def test_describe_condition_marks_optional_params():
    """qa_curve_0 defaults everything but feature_class, and without this the
    caller has to invent values for parameters ProSuite would fill in."""
    result = describe_condition("qa_curve_0")

    lines = {
        line.split()[0]: line for line in result.splitlines() if line.startswith("  ")
    }
    assert lines["feature_class"].endswith("required")
    assert lines["allowed_non_linear_segment_types"].endswith("optional")
    assert lines["group_issues_by_segment_type"].endswith("optional")


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

    result = load_spec(str(spec_file))

    assert result["status"] == "ok"
    assert result["conditions_loaded"] == 1
    assert spec_module._loaded_conditions is not None
    assert len(spec_module._loaded_conditions) == 1


def test_load_spec_makes_search_work(tmp_path):
    from prosuite_mcp.tools import search_spec as tool_search_spec

    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text(_MINIMAL_XML, encoding="utf-8")

    load_spec(str(spec_file))

    assert tool_search_spec("minimum length")["total_matches"] == 1


def test_load_spec_is_seen_by_every_spec_tool(tmp_path):
    """load_spec must switch the spec for all spec tools, not just search_spec."""
    spec_file = tmp_path / "runtime.qa.xml"
    spec_file.write_text(_MINIMAL_XML, encoding="utf-8")

    load_spec(str(spec_file))

    assert "error" not in describe_spec()

    with patch(
        "prosuite_mcp.verification.run_xml_verification_impl",
        return_value={"status": "success"},
    ) as mock_impl:
        run_xml_verification(specification_name="Spec_A", data_source_replacements=[])
    assert mock_impl.call_args[0][0] == str(spec_file)


def test_load_spec_takes_precedence_over_configured_spec_path(tmp_path, monkeypatch):
    """A runtime load_spec must win over PROSUITE_SPEC_PATH, not be ignored by it."""
    env_spec = tmp_path / "env.qa.xml"
    env_spec.write_text(_MINIMAL_XML, encoding="utf-8")
    runtime_spec = tmp_path / "runtime.qa.xml"
    runtime_spec.write_text(_MINIMAL_XML, encoding="utf-8")
    monkeypatch.setenv("PROSUITE_SPEC_PATH", str(env_spec))

    load_spec(str(runtime_spec))

    with patch(
        "prosuite_mcp.verification.run_xml_verification_impl",
        return_value={"status": "success"},
    ) as mock_impl:
        run_xml_verification(specification_name="Spec_A", data_source_replacements=[])
    assert mock_impl.call_args[0][0] == str(runtime_spec)


# ---------------------------------------------------------------------------
# describe_spec
# ---------------------------------------------------------------------------


def test_describe_spec_no_spec_configured():
    result = describe_spec()
    assert "error" in result
    assert "PROSUITE_SPEC_PATH" in result["error"]


def test_describe_spec_returns_metadata(monkeypatch):
    fake_meta = {"specifications": [], "workspaces": []}
    monkeypatch.setenv("PROSUITE_SPEC_PATH", "/tmp/x.qa.xml")
    with patch("prosuite_mcp.tools.get_spec_metadata", return_value=fake_meta):
        result = describe_spec()
    assert result == {"status": "ok", **fake_meta}


# ---------------------------------------------------------------------------
# add_condition_to_spec -- delegation to authoring.py
# ---------------------------------------------------------------------------


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
        "",  # category
    )
    assert result == {"status": "ok", "spec_xml": "<xml/>"}


def test_add_condition_to_spec_reads_configured_spec_path_when_omitted(
    tmp_path, monkeypatch
):
    spec_file = tmp_path / "test.qa.xml"
    spec_file.write_text("<spec/>", encoding="utf-8")
    monkeypatch.setenv("PROSUITE_SPEC_PATH", str(spec_file))

    with patch(
        "prosuite_mcp.authoring.add_condition", return_value="<updated/>"
    ) as mock_add:
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

    assert result == {"status": "ok", "spec_xml": "<updated/>"}
    assert mock_add.call_args[0][5] == "<spec/>"


def test_add_condition_to_spec_errors_without_spec_xml_or_configured_path():
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

    assert result["status"] == "error"
    assert "No spec loaded" in result["error"]


def test_add_condition_to_spec_errors_when_configured_path_missing(monkeypatch):
    monkeypatch.setenv("PROSUITE_SPEC_PATH", "/does/not/exist.qa.xml")

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

    assert result["status"] == "error"
    assert "Could not read spec file" in result["error"]


def test_add_condition_to_spec_errors_instead_of_raising_from_authoring():
    """authoring.add_condition raises for a duplicate name or a missing
    descriptor; the tool must turn that into the same error shape."""
    with patch(
        "prosuite_mcp.authoring.add_condition",
        side_effect=ValueError("Spec already has a QualityCondition named 'x'."),
    ):
        result = add_condition_to_spec(
            target_specification_name="MySpec",
            name="x",
            condition_request=ConditionRequest(
                condition="qa_min_length_1",
                params={"feature_class": "lines", "limit": 2.0},
            ),
            datasets=[DatasetRef(name="lines")],
            workspace_id="DATA_OSM",
            spec_xml="<spec/>",
        )

    assert result["status"] == "error"
    assert "already has a QualityCondition" in result["error"]


def test_add_condition_to_spec_errors_on_malformed_spec_xml():
    """ET.ParseError subclasses SyntaxError, so catching ValueError alone let
    malformed XML escape the documented error shape."""
    result = add_condition_to_spec(
        target_specification_name="MySpec",
        name="x",
        condition_request=ConditionRequest(
            condition="qa_min_length_1",
            params={"feature_class": "lines", "limit": 2.0},
        ),
        datasets=[DatasetRef(name="lines")],
        workspace_id="DATA_OSM",
        spec_xml="<not xml",
    )

    assert result["status"] == "error"
    assert "unclosed token" in result["error"]


def test_every_dict_tool_reports_failure_the_same_way():
    """A caller should check status, not remember which tool it called."""
    from prosuite_mcp.tools import search_spec

    failures = [
        describe_spec(),
        search_spec("anything"),
        load_spec("/does/not/exist.qa.xml"),
        add_condition_to_spec(
            target_specification_name="MySpec",
            name="x",
            condition_request=ConditionRequest(
                condition="qa_min_length_1",
                params={"feature_class": "lines", "limit": 2.0},
            ),
            datasets=[DatasetRef(name="lines")],
            workspace_id="DATA_OSM",
        ),
        run_xml_verification(specification_name="X", data_source_replacements=[]),
    ]

    for result in failures:
        assert result["status"] == "error", result
        assert result["error"], result


# ---------------------------------------------------------------------------
# run_verification / preview_condition_run -- delegation to verification.py
# ---------------------------------------------------------------------------


def test_start_verification_submits_to_async_manager():
    manager = SimpleNamespace(start_adhoc=Mock(return_value={"run_id": "run-1"}))
    condition = ConditionRequest(condition="qa_min_length_0", params={})
    datasets = [DatasetRef(name="Roads")]

    with patch("prosuite_mcp.tools.get_run_manager", return_value=manager):
        result = start_verification("C:/test.gdb", "model", datasets, [condition])

    assert result == {"run_id": "run-1"}
    manager.start_adhoc.assert_called_once_with(
        "C:/test.gdb", "model", datasets, [condition], None, None
    )


def test_async_status_result_and_list_delegate_to_manager():
    manager = SimpleNamespace(
        status=Mock(return_value={"status": "running"}),
        result=Mock(return_value={"status": "success"}),
        list=Mock(return_value={"status": "ok", "runs": []}),
    )
    with patch("prosuite_mcp.tools.get_run_manager", return_value=manager):
        assert get_verification_status("run-1")["status"] == "running"
        assert get_verification_result("run-1")["status"] == "success"
        assert list_verification_runs("failed", 5)["status"] == "ok"

    manager.status.assert_called_once_with("run-1")
    manager.result.assert_called_once_with("run-1")
    manager.list.assert_called_once_with(status="failed", limit=5)


def test_run_verification_delegates_to_verification_impl():
    cond_req = ConditionRequest(
        condition="qa3d_constant_z_0",
        params={"feature_class": "Roads", "tolerance": 0.01},
    )
    datasets = [DatasetRef(name="Roads")]

    with patch(
        "prosuite_mcp.verification.run_verification_impl",
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
        None,  # no Context outside a live MCP session
    )
    assert result == {"status": "success"}


def test_preview_condition_run_forwards_params_to_shared_impl():
    cond_req = ConditionRequest(
        condition="qa3d_constant_z_0",
        params={"feature_class": "Roads", "tolerance": 0.01},
    )
    datasets = [DatasetRef(name="Roads")]

    with patch(
        "prosuite_mcp.verification.run_verification_impl",
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
        None,  # no Context outside a live MCP session
    )
    assert result == {"status": "success"}


# ---------------------------------------------------------------------------
# run_xml_verification
# ---------------------------------------------------------------------------


def test_run_xml_verification_no_spec_configured():
    result = run_xml_verification(
        specification_name="Spec_A",
        data_source_replacements=[],
    )
    assert result["status"] == "error"
    assert "PROSUITE_SPEC_PATH" in result["error"]
    assert result["engine_confirmed"] is False


def test_run_xml_verification_delegates_to_verification_impl(monkeypatch):
    from prosuite_mcp.schemas import WorkspaceReplacement

    monkeypatch.setenv("PROSUITE_SPEC_PATH", "/tmp/x.qa.xml")
    with patch(
        "prosuite_mcp.verification.run_xml_verification_impl",
        return_value={"status": "success"},
    ) as mock_impl:
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
        None,  # no Context outside a live MCP session
    )
    assert result == {"status": "success"}


def test_start_xml_verification_captures_loaded_spec(monkeypatch):
    from prosuite_mcp.schemas import WorkspaceReplacement

    monkeypatch.setenv("PROSUITE_SPEC_PATH", "/tmp/x.qa.xml")
    manager = SimpleNamespace(start_xml=Mock(return_value={"run_id": "run-1"}))
    replacements = [WorkspaceReplacement(workspace_id="DB", workspace_path="C:/db.sde")]

    with patch("prosuite_mcp.tools.get_run_manager", return_value=manager):
        result = start_xml_verification("Spec", replacements)

    assert result == {"run_id": "run-1"}
    manager.start_xml.assert_called_once_with(
        "/tmp/x.qa.xml", "Spec", [["DB", "C:/db.sde"]], None, None
    )


# ---------------------------------------------------------------------------
# _progress_relay
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self):
        self.calls = []

    async def report_progress(self, progress, total=None, message=None):
        self.calls.append((progress, total, message))


@pytest.mark.asyncio
async def test_progress_relay_reaches_the_event_loop_from_a_worker_thread():
    """A sync tool body runs in a worker thread, so progress has to cross back
    into the loop. Get it wrong and long runs stay silent."""
    ctx = _FakeContext()
    relay = _progress_relay(ctx)

    def tool_body():
        relay(ProgressEvent(message="Processing tile 1 of 2"))
        relay(ProgressEvent(message="Processing tile 2 of 2"))

    await anyio.to_thread.run_sync(tool_body)

    assert ctx.calls == [
        (1, None, "1/2 (50%): Processing tile 1 of 2"),
        (2, None, "2/2 (100%): Processing tile 2 of 2"),
    ]


def test_progress_relay_survives_no_event_loop():
    """Losing progress must never fail a verification."""
    relay = _progress_relay(_FakeContext())
    relay(ProgressEvent(message="Processing tile 1 of 2"))


def test_progress_relay_is_absent_without_a_context():
    assert _progress_relay(None) is None


# ---------------------------------------------------------------------------
# list_datasets / describe_dataset
# ---------------------------------------------------------------------------


def test_list_datasets_returns_ok_with_the_datasets():
    with patch(
        "prosuite_mcp.workspace.list_datasets",
        return_value={
            "workspace_path": "C:/d.gdb",
            "driver": "OpenFileGDB",
            "datasets": [],
        },
    ):
        result = list_datasets("C:/d.gdb")

    assert result["status"] == "ok"
    assert result["driver"] == "OpenFileGDB"


def test_list_datasets_turns_a_workspace_error_into_an_error_result():
    """A missing GDAL or an unreadable path must not surface as a tool crash."""
    with patch(
        "prosuite_mcp.workspace.list_datasets",
        side_effect=WorkspaceError("ogrinfo is not on PATH"),
    ):
        result = list_datasets("C:/d.gdb")

    assert result == {"status": "error", "error": "ogrinfo is not on PATH"}


def test_describe_dataset_turns_a_workspace_error_into_an_error_result():
    with patch(
        "prosuite_mcp.workspace.describe_dataset",
        side_effect=WorkspaceError("No dataset named 'nope'"),
    ):
        result = describe_dataset("C:/d.gdb", "nope")

    assert result["status"] == "error"
