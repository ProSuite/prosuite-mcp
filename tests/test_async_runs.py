import threading
import time
from pathlib import Path
from unittest.mock import patch

from prosuite_mcp.async_runs import AsyncVerificationManager, RunStore
from prosuite_mcp.schemas import ConditionRequest, DatasetRef
from prosuite_mcp.verification import ProgressEvent


def _wait_for_terminal(manager: AsyncVerificationManager, run_id: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = manager.status(run_id)
        if status["status"] in {"succeeded", "failed"}:
            return status
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not finish")


def test_start_returns_before_verification_finishes(tmp_path):
    manager = AsyncVerificationManager(tmp_path)
    release = threading.Event()

    def verify(*args, **kwargs):
        assert release.wait(5)
        return {"status": "success", "output_dir": "C:/qa/run"}

    with (
        patch(
            "prosuite_mcp.async_runs.verification._service_is_local", return_value=False
        ),
        patch(
            "prosuite_mcp.async_runs.verification.run_verification_impl",
            side_effect=verify,
        ),
    ):
        started = manager.start_adhoc(
            "C:/data.gdb",
            "model",
            [DatasetRef(name="Roads")],
            [ConditionRequest(condition="qa_min_length_0", params={})],
            None,
            None,
        )
        assert started["status"] == "queued"
        assert started["run_id"]
        assert started["automatic_notification"] is False
        assert started["status_tool"] == {
            "name": "get_verification_status",
            "arguments": {"run_id": started["run_id"]},
        }
        assert started["result_tool"] == {
            "name": "get_verification_result",
            "arguments": {"run_id": started["run_id"]},
        }
        assert "No automatic completion notification" in started["message"]
        assert started["output_dir"] is None
        assert manager.status(started["run_id"])["status"] in {"queued", "running"}
        release.set()
        finished = _wait_for_terminal(manager, started["run_id"])

    assert finished["status"] == "succeeded"
    assert finished["output_dir"] == "C:/qa/run"
    manager.close()


def test_local_default_output_base_is_visible_immediately(tmp_path):
    manager = AsyncVerificationManager(tmp_path)
    release = threading.Event()

    def verify(*args, **kwargs):
        assert release.wait(5)
        return {"status": "success", "output_dir": str(tmp_path / "outputs" / "run")}

    with (
        patch(
            "prosuite_mcp.async_runs.verification._service_is_local", return_value=True
        ),
        patch(
            "prosuite_mcp.async_runs.verification.run_verification_impl",
            side_effect=verify,
        ),
    ):
        started = manager.start_adhoc("C:/data.gdb", "model", [], [], None, None)
        status = manager.status(started["run_id"])

        assert started["output_dir"] == str((tmp_path / "outputs").resolve())
        assert started["output_directory_is_final"] is False
        assert status["output_dir"] == started["output_dir"]
        assert status["output_directory_is_final"] is False

        release.set()
        finished = _wait_for_terminal(manager, started["run_id"])

    assert finished["output_directory_is_final"] is True
    manager.close()


def test_progress_and_result_are_persisted(tmp_path):
    manager = AsyncVerificationManager(tmp_path)

    def verify(*args, **kwargs):
        on_progress = args[-1]
        on_progress(
            ProgressEvent(
                overall_current=4,
                overall_total=10,
                processing_step_message="Checking roads",
            )
        )
        return {
            "status": "success",
            "engine_confirmed": True,
            "total_errors": 3,
            "output_dir": "C:/qa/run-1",
        }

    with (
        patch(
            "prosuite_mcp.async_runs.verification._service_is_local", return_value=False
        ),
        patch(
            "prosuite_mcp.async_runs.verification.run_verification_impl",
            side_effect=verify,
        ),
    ):
        started = manager.start_adhoc("C:/data.gdb", "model", [], [], None, None)
        status = _wait_for_terminal(manager, started["run_id"])

    assert status["progress_percent"] == 40.0
    assert status["message"] == "Verification completed"
    assert status["result_available"] is True
    assert Path(status["result_path"]).is_file()
    result = manager.result(started["run_id"])
    assert result["run_status"] == "succeeded"
    assert result["total_errors"] == 3
    manager.close()


def test_worker_exceptions_become_retrievable_failed_results(tmp_path):
    manager = AsyncVerificationManager(tmp_path)
    with (
        patch(
            "prosuite_mcp.async_runs.verification._service_is_local", return_value=False
        ),
        patch(
            "prosuite_mcp.async_runs.verification.run_verification_impl",
            side_effect=RuntimeError("boom"),
        ),
    ):
        started = manager.start_adhoc("C:/data.gdb", "model", [], [], None, None)
        status = _wait_for_terminal(manager, started["run_id"])

    assert status["status"] == "failed"
    result = manager.result(started["run_id"])
    assert result["status"] == "error"
    assert "RuntimeError: boom" in result["error"]
    manager.close()


def test_store_can_be_read_by_another_manager_instance(tmp_path):
    store = RunStore(tmp_path)
    row = store.create("adhoc", {"output_dir": None})
    store.finish(row["run_id"], {"status": "success", "total_errors": 0})

    other = AsyncVerificationManager(tmp_path)
    assert other.status(row["run_id"])["status"] == "succeeded"
    assert other.result(row["run_id"])["total_errors"] == 0
    other.close()


def test_unknown_run_id_has_normal_error_contract(tmp_path):
    manager = AsyncVerificationManager(tmp_path)
    assert manager.status("missing") == {
        "status": "error",
        "error": "Unknown run_id: missing",
    }
    assert manager.result("missing")["status"] == "error"
    manager.close()
