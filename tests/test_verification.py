"""Unit tests for running verifications and shaping results. gRPC is mocked."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from prosuite.verification import (
    MessageLevel,
    ServiceStatus,
    VerifiedCondition,
    VerifiedSpecification,
)

from prosuite_mcp.schemas import ConditionRequest, DatasetRef
from prosuite_mcp.verification import (
    _MESSAGE_CAP,
    StreamOutcome,
    _decode_issue,
    _failure_reason,
    _make_run_dir,
    _run_verify,
    _service_is_local,
    _summarize,
    run_verification_impl,
    run_xml_verification_impl,
)


@pytest.fixture(autouse=True)
def _local_service(monkeypatch):
    """Pin the host: whether output_dir defaults to a local runs/ dir depends on
    it, and a developer shell may export a remote PROSUITE_HOST."""
    monkeypatch.setenv("PROSUITE_HOST", "localhost")


# ---------------------------------------------------------------------------
# _service_is_local
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "is_local"),
    [
        ("localhost", True),
        ("LOCALHOST", True),
        ("db.localhost", True),
        ("127.0.0.1", True),
        # The whole 127.0.0.0/8 block is loopback, not just .0.1
        ("127.0.0.2", True),
        ("127.255.255.254", True),
        ("::1", True),
        # gRPC targets bracket IPv6 literals
        ("[::1]", True),
        ("0:0:0:0:0:0:0:1", True),
        ("203.0.113.10", False),
        ("192.168.1.10", False),
        ("example.com", False),
        ("0.0.0.0", False),
        ("", False),
    ],
)
def test_service_is_local_classifies_host(monkeypatch, host, is_local):
    """Getting this wrong either invents a path a remote server cannot use or
    suppresses the local Issues.gdb a local one would have written."""
    monkeypatch.setenv("PROSUITE_HOST", host)
    assert _service_is_local() is is_local


# ---------------------------------------------------------------------------
# _decode_issue
# ---------------------------------------------------------------------------


def test_decode_issue_maps_core_fields():
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
    return SimpleNamespace(
        issue_code=code,
        description="d",
        allowable=allowable,
        condition_id=condition_id,
        involved_objects=[SimpleNamespace(table_name=table, object_ids=[1])],
    )


def _fake_response(
    issues,
    verified_specification=None,
    status=ServiceStatus.status_1,
    message="",
    message_level=MessageLevel.level_40000,
):
    """Mirrors prosuite's VerificationResponse, which always carries a status,
    a message and its level alongside the issues. Statuses come from
    ServiceStatus so these exercise the same comparison _run_verify makes:
    status_1 is Running, status_3 Finished, status_4 Failed."""
    return SimpleNamespace(
        issues=issues,
        verified_specification=verified_specification,
        service_call_status=status,
        message=message,
        message_level=message_level,
    )


def test_run_verify_aggregates_stream_without_retaining_all_issues():
    responses = [
        _fake_response(
            [
                _fake_issue("A", "lines", allowable=False, condition_id=1),
                _fake_issue("A", "lines", allowable=False, condition_id=1),
            ]
        ),
        _fake_response(
            [_fake_issue("B", "points", allowable=True, condition_id=2)],
            verified_specification="SPEC",
            status=ServiceStatus.status_3,
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
    assert outcome.failure_messages == []


def test_run_verify_keeps_the_message_from_a_failed_response():
    """The service explains a rejection here and nowhere else; dropping it
    leaves the caller with no way to tell a bad spec from a bad path."""
    responses = [
        _fake_response(
            [],
            status=ServiceStatus.status_4,
            message="Server error: Error deserializing file: invalid child element",
        )
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))

    outcome, verified = _run_verify(service, spec=None, output_dir="", perimeter=None)

    assert verified is None
    assert outcome.failure_messages == [
        "Server error: Error deserializing file: invalid child element"
    ]


def test_run_verify_ignores_messages_on_non_failed_responses():
    responses = [
        _fake_response([], status=ServiceStatus.status_1, message="progress noise")
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))

    outcome, _ = _run_verify(service, spec=None, output_dir="", perimeter=None)

    assert outcome.failure_messages == []


def test_run_verify_keeps_warnings_from_a_run_that_succeeds():
    """The service diagnoses a run as it goes, not only when it gives up."""
    responses = [
        _fake_response(
            [],
            status=ServiceStatus.status_1,
            message="The FileGdb workspace 'Issues.gdb' already exists",
            message_level=MessageLevel.level_60000,
        ),
        _fake_response(
            [],
            status=ServiceStatus.status_3,
            verified_specification="SPEC",
            message="Processing tile 2 of 2",
        ),
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))

    outcome, _ = _run_verify(service, spec=None, output_dir="", perimeter=None)

    assert outcome.service_messages == [
        {
            "level": MessageLevel.level_60000,
            "message": "The FileGdb workspace 'Issues.gdb' already exists",
        }
    ]
    assert outcome.failure_messages == []


def test_run_verify_leaves_progress_chatter_out_of_service_messages():
    responses = [
        _fake_response([], message="Processing tile 1 of 9"),
        _fake_response([], message="repeated", message_level=MessageLevel.level_30000),
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))

    outcome, _ = _run_verify(service, spec=None, output_dir="", perimeter=None)

    assert outcome.service_messages == []


def test_run_verify_caps_service_messages():
    responses = [
        _fake_response(
            [], message=f"warning {i}", message_level=MessageLevel.level_60000
        )
        for i in range(_MESSAGE_CAP + 20)
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))

    outcome, _ = _run_verify(service, spec=None, output_dir="", perimeter=None)

    assert len(outcome.service_messages) == _MESSAGE_CAP


def test_run_verify_relays_messages_while_the_run_is_going():
    """It has to fire during the stream, not once it has finished."""
    responses = [
        _fake_response([], message="Processing tile 1 of 2"),
        _fake_response([], message="skip me", message_level=MessageLevel.level_10000),
        _fake_response([], message="", message_level=MessageLevel.level_40000),
        _fake_response([], message="Processing tile 2 of 2"),
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))
    relayed: list[str] = []

    _run_verify(
        service,
        spec=None,
        output_dir="",
        perimeter=None,
        on_progress=relayed.append,
    )

    assert relayed == ["Processing tile 1 of 2", "Processing tile 2 of 2"]


def test_run_verify_records_the_terminal_status():
    """Both silent failure modes end without a summary, so the status is the
    only thing left that distinguishes them."""
    responses = [
        _fake_response([], status=ServiceStatus.status_1),
        _fake_response([], status=ServiceStatus.status_2),
    ]
    service = SimpleNamespace(verify=lambda *a, **k: iter(responses))

    outcome, _ = _run_verify(service, spec=None, output_dir="", perimeter=None)

    assert outcome.last_status == ServiceStatus.status_2


def test_failure_reason_uses_a_warning_when_the_failure_itself_is_silent():
    """Observed live: it warns on a Running response, then fails with an empty
    message, so the canned prose was all a caller got."""
    outcome = StreamOutcome(
        last_status=ServiceStatus.status_4,
        service_messages=[
            {
                "level": MessageLevel.level_60000,
                "message": ("The FileGdb workspace 'C:/out/Issues.gdb' already exists"),
            }
        ],
    )

    assert _failure_reason(outcome) == (
        "The FileGdb workspace 'C:/out/Issues.gdb' already exists"
    )


def test_failure_reason_prefers_what_the_service_said():
    outcome = StreamOutcome(
        failure_messages=["Cannot find column [ART]."],
        last_status=ServiceStatus.status_4,
    )

    assert _failure_reason(outcome) == "Cannot find column [ART]."


def test_failure_reason_distinguishes_a_silent_rejection_from_a_cancelled_run():
    """Measured against a live server: a dataset name that does not exist is
    rejected with an empty message, and a bad field inside a constraint
    expression streams issues and then cancels. Both used to report the same
    unhelpful string."""
    rejected = _failure_reason(StreamOutcome(last_status=ServiceStatus.status_4))
    cancelled = _failure_reason(
        StreamOutcome(total=1, last_status=ServiceStatus.status_2)
    )

    assert rejected != cancelled
    assert "rejected" in rejected and "dataset name" in rejected
    assert "cancelled" in cancelled and "field name" in cancelled
    # Named causes are leads, not diagnoses: the service sent none.
    assert "One known cause" in rejected and "One known cause" in cancelled
    # The issues streamed before a cancel look like findings but are partial.
    assert "not a complete result" in cancelled


def test_failure_reason_falls_back_when_the_stream_just_stops():
    assert _failure_reason(StreamOutcome()) == (
        "Verification stream ended without a final summary."
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


def test_make_run_dir_retries_a_name_already_taken(tmp_path):
    """Two runs must never share a directory, however the name was generated."""
    with patch(
        "prosuite_mcp.verification._run_dir_name",
        side_effect=["taken", "taken", "free"],
    ):
        first = _make_run_dir("MySpec", tmp_path)
        second = _make_run_dir("MySpec", tmp_path)

    assert (first.name, second.name) == ("taken", "free")


def test_make_run_dir_gives_up_rather_than_share_a_directory(tmp_path):
    with (
        patch("prosuite_mcp.verification._run_dir_name", return_value="taken"),
        pytest.raises(RuntimeError, match="No free run directory"),
    ):
        _make_run_dir("MySpec", tmp_path)
        _make_run_dir("MySpec", tmp_path)


# ---------------------------------------------------------------------------
# run_verification_impl (ad-hoc mode, shared by run_verification /
# preview_condition_run)
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


def _run_adhoc(**overrides):
    args = dict(
        model_catalog_path="C:/test.gdb",
        model_name="TestModel",
        datasets=[DatasetRef(name="Roads")],
        conditions=[
            ConditionRequest(
                condition="qa3d_constant_z_0",
                params={"feature_class": "Roads", "tolerance": 0.01},
            )
        ],
        output_dir=None,
        envelope=None,
        run_dir_prefix="adhoc",
    )
    args.update(overrides)
    return run_verification_impl(**args)


def test_run_verification_impl_success(tmp_path):
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify") as mock_stream,
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_stream.return_value = (
            StreamOutcome(total=2, errors=2, counts_by_condition={1: 2}),
            final_spec,
        )
        mock_path.cwd.return_value = tmp_path

        result = _run_adhoc()

    assert result["status"] == "success"
    assert result["total_errors"] == 2
    assert result["total_conditions"] == 1
    assert result["conditions"][0]["errors"] == 2


def test_run_verification_impl_total_errors_from_stream_when_conditions_empty(
    tmp_path,
):
    # Ad-hoc runs come back with empty/nameless verified_conditions, so the old
    # sum-of-error_count reported 0 even when the stream carried real issues.
    empty_spec = VerifiedSpecification(
        specification_name="prosuite-mcp verification",
        user_name="",
        verified_conditions=[],
    )

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(total=5, errors=4, warnings=1), empty_spec),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["total_errors"] == 4
    assert result["total_warnings"] == 1
    assert result["issues_seen_in_stream"] == 5


def test_run_verification_impl_exposes_sample_and_counts(tmp_path):
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
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify", return_value=(outcome, spec)),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["issue_counts_by_code"] == {"A": 2, "B": 1}
    assert result["issue_counts_by_table"] == {"lines": 3}
    assert result["sample_features"] == sample


def test_run_verification_impl_keeps_the_sample_when_there_is_no_summary(tmp_path):
    """A run that dies mid-stream says why in the issues it already sent, and
    _failure_reason can only guess. Dropping them threw the answer away."""
    sample = [
        {
            "issue_code": "",
            "description": (
                "Error testing QaConstraint0_lines: ... Cannot find column [ART]."
            ),
            "allowable": False,
            "involved": [],
        },
    ]
    outcome = StreamOutcome(
        total=1, errors=1, sample=sample, last_status=ServiceStatus.status_2
    )

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify", return_value=(outcome, None)),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["status"] == "error"
    assert result["sample_features"] == sample
    assert "Cannot find column [ART]" in result["sample_features"][0]["description"]


def test_run_verification_impl_engine_confirmed_true_on_success(tmp_path):
    spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(total=1, errors=1), spec),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["engine_confirmed"] is True


def test_run_verification_impl_engine_confirmed_false_without_final_summary(tmp_path):
    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(total=3), None),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["status"] == "error"
    assert result["engine_confirmed"] is False
    assert result["error"] == "Verification stream ended without a final summary."


def test_run_verification_impl_surfaces_the_service_failure_message(tmp_path):
    outcome = StreamOutcome(
        failure_messages=["Server error: dataset 'lines' not found"]
    )
    with (
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify", return_value=(outcome, None)),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["status"] == "error"
    assert result["error"] == "Server error: dataset 'lines' not found"


def test_run_verification_impl_grpc_error(tmp_path):
    import grpc

    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "service unavailable"

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify") as mock_stream,
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_stream.side_effect = _FakeRpcError()
        mock_path.cwd.return_value = tmp_path

        result = _run_adhoc()

    assert result["status"] == "error"
    assert "unavailable" in result["error"].lower()
    assert result["engine_confirmed"] is False


def test_run_verification_impl_unknown_condition():
    result = _run_adhoc(
        conditions=[ConditionRequest(condition="no_such_condition_xyz", params={})]
    )
    assert result["status"] == "error"
    assert "Unknown condition" in result["error"]
    assert result["engine_confirmed"] is False


def test_run_verification_impl_does_not_create_dir_on_invalid_condition():
    # Condition validation happens before _make_run_dir is reached, so a
    # rejected request must not leave a timestamped directory behind. Not
    # preview-specific -- both run_dir_prefix values share this ordering.
    with patch("prosuite_mcp.verification._make_run_dir") as mock_make_run_dir:
        result = _run_adhoc(
            conditions=[ConditionRequest(condition="no_such_condition_xyz", params={})]
        )

    assert result["status"] == "error"
    mock_make_run_dir.assert_not_called()


def test_run_verification_impl_with_output_dir(monkeypatch):
    monkeypatch.setenv("PROSUITE_HOST", "203.0.113.10")
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify") as mock_stream,
    ):
        mock_stream.return_value = (StreamOutcome(), final_spec)

        result = _run_adhoc(output_dir="C:/output")

    assert result["status"] == "success"
    assert result["output_dir"].startswith("C:/output/")


def test_run_verification_impl_auto_creates_output_dir(tmp_path):
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(total=2, errors=2), final_spec),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert "output_dir" in result
    assert result["status"] == "success"


def test_run_verification_impl_creates_no_local_dir_for_a_remote_service(
    tmp_path, monkeypatch
):
    """The service resolves output_dir on its own machine, so inventing a local
    path for a remote host produced an empty dir here and a bad path there."""
    monkeypatch.setenv("PROSUITE_HOST", "203.0.113.10")
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(total=2, errors=2), final_spec),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["status"] == "success"
    assert result["output_dir"] == ""
    assert list(tmp_path.iterdir()) == []


def test_run_verification_impl_creates_a_local_dir_for_a_local_service(tmp_path):
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(total=2, errors=2), final_spec),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = _run_adhoc()

    assert result["output_dir"].startswith(str(tmp_path))
    assert len(list((tmp_path / "runs").iterdir())) == 1


def test_run_verification_impl_runs_under_an_explicit_output_dir(monkeypatch):
    """A reused output_dir has to be a base: the service will not overwrite an
    Issues.gdb that is already there."""
    monkeypatch.setenv("PROSUITE_HOST", "203.0.113.10")
    final_spec = _mock_verified_spec()

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
    ):
        first = _run_adhoc(output_dir="C:/my_dir")
        second = _run_adhoc(output_dir="C:/my_dir")

    assert first["output_dir"].startswith("C:/my_dir/")
    assert first["output_dir"] != second["output_dir"]


def test_run_verification_impl_keeps_a_windows_separator(monkeypatch):
    """output_dir belongs to the service's machine, which may spell paths
    differently."""
    monkeypatch.setenv("PROSUITE_HOST", "203.0.113.10")

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), _mock_verified_spec()),
        ),
    ):
        result = _run_adhoc(output_dir="C:\\my_dir")

    assert result["output_dir"].startswith("C:\\my_dir\\")


def test_run_verification_impl_empty_output_dir_asks_for_no_output(monkeypatch):
    monkeypatch.setenv("PROSUITE_HOST", "203.0.113.10")

    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), _mock_verified_spec()),
        ),
    ):
        result = _run_adhoc(output_dir="")

    assert result["output_dir"] == ""


def test_run_verification_impl_creates_the_run_dir_for_a_local_service(tmp_path):
    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), _mock_verified_spec()),
        ),
    ):
        result = _run_adhoc(output_dir=str(tmp_path))

    assert Path(result["output_dir"]).is_dir()
    assert [p.name for p in tmp_path.iterdir()] == [Path(result["output_dir"]).name]


# ---------------------------------------------------------------------------
# run_verification_impl (preview mode, run_dir_prefix="preview")
# ---------------------------------------------------------------------------


def test_run_verification_impl_preview_uses_preview_prefixed_run_dir(tmp_path):
    with (
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), _mock_verified_spec()),
        ),
        patch(
            "prosuite_mcp.verification._make_run_dir", return_value=tmp_path / "run"
        ) as mock_make_run_dir,
    ):
        _run_adhoc(run_dir_prefix="preview")

    assert mock_make_run_dir.call_args[0][0] == "preview"


# ---------------------------------------------------------------------------
# run_xml_verification_impl
# ---------------------------------------------------------------------------


def _mock_xml_verified_spec() -> VerifiedSpecification:
    return VerifiedSpecification(
        specification_name="Spec_A",
        user_name="",
        verified_conditions=[
            VerifiedCondition(condition_id=10, name="Cond_A", error_count=0),
        ],
    )


def test_run_xml_verification_impl_spec_load_failure():
    with patch(
        "prosuite_mcp.verification.XmlSpecification",
        side_effect=ValueError("bad spec"),
    ):
        result = run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir=None,
            envelope=None,
        )
    assert result["status"] == "error"
    assert "Failed to load spec" in result["error"]
    assert result["engine_confirmed"] is False


def test_run_xml_verification_impl_success(tmp_path):
    final_spec = _mock_xml_verified_spec()

    with (
        patch("prosuite_mcp.verification.XmlSpecification"),
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir=None,
            envelope=None,
        )

    assert result["status"] == "success"
    assert result["total_errors"] == 0
    assert result["conditions"][0]["name"] == "Cond_A"


def test_run_xml_verification_impl_grpc_error(tmp_path):
    import grpc

    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "service unavailable"

    with (
        patch("prosuite_mcp.verification.XmlSpecification"),
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify", side_effect=_FakeRpcError()),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir=None,
            envelope=None,
        )

    assert result["status"] == "error"
    assert "unavailable" in result["error"].lower()
    assert result["engine_confirmed"] is False


def test_run_xml_verification_impl_no_final_summary(tmp_path):
    with (
        patch("prosuite_mcp.verification.XmlSpecification"),
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(total=3), None),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir=None,
            envelope=None,
        )

    assert result["status"] == "error"
    assert result["issues_seen_in_stream"] == 3


def test_run_xml_verification_impl_surfaces_the_service_failure_message(tmp_path):
    """A rejected spec is the common failure here, and only the service knows
    why. This is the exact message the live XML test provoked."""
    outcome = StreamOutcome(
        failure_messages=[
            "Server error: Error deserializing file: The element 'DataQuality' "
            "has invalid child element 'TestDescriptors'"
        ]
    )
    with (
        patch("prosuite_mcp.verification.XmlSpecification"),
        patch("prosuite_mcp.verification._make_service"),
        patch("prosuite_mcp.verification._run_verify", return_value=(outcome, None)),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir=None,
            envelope=None,
        )

    assert result["status"] == "error"
    assert "invalid child element" in result["error"]


def test_run_xml_verification_impl_output_dir_in_result(monkeypatch):
    monkeypatch.setenv("PROSUITE_HOST", "203.0.113.10")
    final_spec = _mock_xml_verified_spec()

    with (
        patch("prosuite_mcp.verification.XmlSpecification"),
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
    ):
        result = run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir="C:/output",
            envelope=None,
        )

    assert result["status"] == "success"
    assert result["output_dir"].startswith("C:/output/")


def test_run_xml_verification_impl_auto_creates_output_dir(tmp_path):
    final_spec = _mock_xml_verified_spec()

    with (
        patch("prosuite_mcp.verification.XmlSpecification"),
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
        patch("prosuite_mcp.verification.Path") as mock_path,
    ):
        mock_path.cwd.return_value = tmp_path
        result = run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir=None,
            envelope=None,
        )

    assert "output_dir" in result
    assert result["status"] == "success"


def test_run_xml_verification_impl_runs_under_an_explicit_output_dir(monkeypatch):
    monkeypatch.setenv("PROSUITE_HOST", "203.0.113.10")
    final_spec = _mock_xml_verified_spec()

    def _run():
        return run_xml_verification_impl(
            spec_path="/tmp/x.qa.xml",
            specification_name="Spec_A",
            replacements=[],
            output_dir="C:/my_dir",
            envelope=None,
        )

    with (
        patch("prosuite_mcp.verification.XmlSpecification"),
        patch("prosuite_mcp.verification._make_service"),
        patch(
            "prosuite_mcp.verification._run_verify",
            return_value=(StreamOutcome(), final_spec),
        ),
    ):
        first, second = _run(), _run()

    assert first["output_dir"].startswith("C:/my_dir/")
    assert first["output_dir"] != second["output_dir"]
