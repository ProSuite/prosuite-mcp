"""Live smoke test against a real ProSuite gRPC service.

Skipped unless PROSUITE_LIVE_TESTS=1 is set, and requires PROSUITE_HOST /
PROSUITE_PORT to point at a real, reachable ProSuite service -- this repo
never hardcodes an address for one. Not run in CI (no such service is
reachable from GitHub Actions runners) and not part of a plain `pytest
tests/` run by default -- this exists to catch drift between the real
service's response shape and what prosuite_mcp.verification assumes, which
the mocked suite in test_verification.py cannot catch by construction.

    PROSUITE_LIVE_TESTS=1 PROSUITE_HOST=<host> PROSUITE_PORT=<port> \\
        uv run pytest tests/test_live_verification.py -v

Targets a plain file geodatabase named gdb1.gdb (feature classes
lines/points/polygons) on that server -- adjust _GDB1_PATH below if your
server keeps it somewhere else.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from prosuite_mcp.schemas import ConditionRequest, DatasetRef, WorkspaceReplacement
from prosuite_mcp.tools import (
    describe_spec,
    load_spec,
    run_verification,
    run_xml_verification,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("PROSUITE_LIVE_TESTS") != "1",
    reason="set PROSUITE_LIVE_TESTS=1 to run against a real ProSuite service",
)

_HOST = os.environ.get("PROSUITE_HOST", "localhost")
_PORT = (
    int(os.environ.get("PROSUITE_PORT", "5151"))
    if os.environ.get("PROSUITE_LIVE_TESTS") == "1"
    else 5151
)

_GDB1_PATH = "C:/ProSuite/TestData/gdb1.gdb"

# Committed alongside the tests: ours, tiny, and no customer data. Its one
# condition is nested under <Categories>, the shape describe_spec used to miss.
_SPEC_PATH = Path(__file__).parent / "data" / "gdb1.qa.xml"
_SPEC_NAME = "gdb1 smoke test"


@pytest.fixture()
def restore_active_spec():
    import prosuite_mcp.spec as spec_module

    saved = (spec_module._active_spec_path, spec_module._loaded_conditions)
    yield
    spec_module._active_spec_path, spec_module._loaded_conditions = saved


def _server_reachable() -> bool:
    try:
        with socket.create_connection((_HOST, _PORT), timeout=3):
            return True
    except OSError:
        return False


def test_qa_min_length_flags_real_feature_against_live_service(monkeypatch):
    if not _server_reachable():
        pytest.skip(f"ProSuite service not reachable at {_HOST}:{_PORT}")

    # run_verification resolves host/port via config.load_config() reading
    # os.environ directly, independent of this module's _HOST/_PORT defaults.
    monkeypatch.setenv("PROSUITE_HOST", _HOST)
    monkeypatch.setenv("PROSUITE_PORT", str(_PORT))

    result = run_verification(
        model_catalog_path=_GDB1_PATH,
        model_name="gdb1",
        datasets=[DatasetRef(name="lines")],
        conditions=[
            ConditionRequest(
                condition="qa_min_length_1",
                params={"feature_class": "lines", "limit": 1_000_000},
            )
        ],
        # output_dir is a server-side write path; "" (not None) skips the
        # default local runs/ dir, which the remote VM can't write to.
        output_dir="",
    )

    assert result["status"] == "success", result
    assert result["engine_confirmed"] is True
    assert result["total_errors"] >= 1
    assert result["sample_features"], "expected at least one real flagged feature"


def test_xml_specification_runs_against_live_service(monkeypatch, restore_active_spec):
    """The documented XML workflow end to end: load a spec, ask it which
    workspaces to replace, run it. Everything the mocked suite covers stops at
    a serialized request; only this proves ProSuite accepts what we send."""
    if not _server_reachable():
        pytest.skip(f"ProSuite service not reachable at {_HOST}:{_PORT}")

    monkeypatch.setenv("PROSUITE_HOST", _HOST)
    monkeypatch.setenv("PROSUITE_PORT", str(_PORT))

    assert load_spec(str(_SPEC_PATH))["status"] == "ok"

    spec = next(
        s
        for s in describe_spec()["specifications"]
        if s["specification_name"] == _SPEC_NAME
    )
    assert spec["workspace_ids"] == ["GDB1"]

    result = run_xml_verification(
        specification_name=_SPEC_NAME,
        # Fed straight from describe_spec, which is how the docs tell an agent
        # to build this and what silently returned nothing before.
        data_source_replacements=[
            WorkspaceReplacement(workspace_id=w, workspace_path=_GDB1_PATH)
            for w in spec["workspace_ids"]
        ],
        output_dir="",
    )

    assert result["status"] == "success", result
    assert result["engine_confirmed"] is True
    assert result["total_errors"] >= 1
    assert result["sample_features"], "expected at least one real flagged feature"
