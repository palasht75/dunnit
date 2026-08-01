import pytest

from dunnit.verdict import Evidence, Outcome, Status, Verdict


def test_empty_verdict_is_an_error_not_a_vacuous_pass():
    verdict = Verdict()
    assert verdict.scan_complete is False
    assert verdict.verification_complete is False
    assert verdict.outcome is Outcome.ERROR
    assert verdict.passed is False


def test_outcomes_distinguish_warnings_failures_and_errors():
    assert Verdict([Evidence("ok", Status.PASS)]).outcome is Outcome.PASS
    assert Verdict([Evidence("warn", Status.WARN)]).outcome is Outcome.PASS_WITH_WARNINGS
    assert Verdict([Evidence("fail", Status.FAIL)]).outcome is Outcome.FAIL
    assert Verdict([Evidence("error", Status.ERROR)]).outcome is Outcome.ERROR


def test_incomplete_scan_cannot_pass():
    verdict = Verdict([Evidence("scan", Status.PASS, scan_complete=False)])
    assert verdict.verification_complete is False
    assert verdict.outcome is Outcome.ERROR
    assert verdict.passed is False


def test_add_propagates_incomplete_evidence():
    verdict = Verdict()
    verdict.add(Evidence("scan", Status.ERROR, scan_complete=False))
    assert verdict.scan_complete is False


def test_scan_complete_setter_and_mark_incomplete_are_fail_closed():
    verdict = Verdict([Evidence("ok", Status.PASS)])
    verdict.scan_complete = True
    assert verdict.scan_complete is True
    verdict.mark_incomplete()
    assert verdict.scan_complete is False
    assert verdict.to_json().startswith("{")


def test_json_is_additive_and_preserves_binary_verdict_field():
    evidence = Evidence(
        "tests",
        Status.WARN,
        "warning",
        "hint",
        rule_id="command:tests",
        path="tests/test_one.py",
        line=12,
        fingerprint="abc",
        duration=0.25,
        exit_code=0,
    )
    report = Verdict([evidence], meta={"base": "abc123"}).to_dict()
    assert report["schema_version"] == 1
    assert report["verdict"] == "pass"
    assert report["outcome"] == "pass_with_warnings"
    assert report["scan_complete"] is True
    assert report["summary"] == {"pass": 0, "fail": 0, "warn": 1, "error": 0}
    assert report["evidence"][0] == {
        "check": "tests",
        "status": "warn",
        "detail": "warning",
        "rule_id": "command:tests",
        "severity": "warning",
        "scan_complete": True,
        "hint": "hint",
        "path": "tests/test_one.py",
        "line": 12,
        "fingerprint": "abc",
        "duration": 0.25,
        "exit_code": 0,
    }
    assert report["meta"] == {"base": "abc123"}


def test_error_remains_fail_for_legacy_json_consumers():
    report = Verdict([Evidence("git", Status.ERROR)]).to_dict()
    assert report["verdict"] == "fail"
    assert report["outcome"] == "error"
    assert report["scan_complete"] is False
    assert report["evidence"][0]["scan_complete"] is False


def test_string_status_is_normalized_and_cannot_bypass_failure_identity_checks():
    evidence = Evidence("proof", "fail")  # type: ignore[arg-type]
    assert evidence.status is Status.FAIL
    assert Verdict([evidence]).outcome is Outcome.FAIL

    with pytest.raises(ValueError, match="unsupported evidence status"):
        Evidence("proof", "successful")  # type: ignore[arg-type]


def test_invalid_scan_completeness_types_are_rejected():
    with pytest.raises(TypeError, match="scan_complete"):
        Evidence("proof", Status.PASS, scan_complete=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="scan_complete"):
        Verdict([Evidence("proof", Status.PASS)], scan_complete=1)  # type: ignore[arg-type]

    verdict = Verdict([Evidence("proof", Status.PASS)])
    with pytest.raises(TypeError, match="scan_complete"):
        verdict.scan_complete = 1  # type: ignore[assignment]


def test_mutated_invalid_status_fails_closed_instead_of_passing():
    evidence = Evidence("proof", Status.PASS)
    evidence.status = "pass"  # type: ignore[assignment]
    verdict = Verdict([evidence])
    assert verdict.outcome is Outcome.ERROR
    assert verdict.passed is False
    assert verdict.counts["error"] == 1
