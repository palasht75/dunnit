from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pilots" / "aggregate.py"
SPEC = importlib.util.spec_from_file_location("dunnit_pilot_aggregate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
pilot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pilot
SPEC.loader.exec_module(pilot)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )


def _repository(index: int) -> dict[str, Any]:
    return {
        "id": f"repo-{index}",
        "ecosystems": [["python"], ["javascript"], ["go"], ["python"], ["go"]][index],
        "monorepo": index == 4,
        "independent": True,
        "existing_required_ci": True,
        "consent_mode": "anonymized",
        "onboarding_minutes": 5 + index,
        "hand_authored_yaml": index == 4,
        "shadow_started_at": "2026-01-01T00:00:00Z",
        "shadow_observed_through": "2026-03-01T00:00:00Z",
        "required_started_at": "2026-03-02T00:00:00Z",
        "required_observed_through": "2026-04-02T00:00:00Z",
        "required_continuously_enabled": True,
        "maintainer_confirmed": True,
    }


def _event(repository: int, index: int, *, phase: str) -> dict[str, Any]:
    alert = phase == "shadow" and index == 0
    sampled = phase == "shadow" and 1 <= index <= 5
    return {
        "protocol": pilot.PROTOCOL,
        "repository_id": f"repo-{repository}",
        "ecosystem": [["python"], ["javascript"], ["go"], ["python"], ["go"]][repository][0],
        "candidate_id": f"{phase}-{index}",
        "occurred_at": (
            f"2026-02-{(index % 20) + 1:02d}T12:00:00Z"
            if phase == "shadow"
            else f"2026-03-{(index % 10) + 3:02d}T12:00:00Z"
        ),
        "phase": phase,
        "dunnit_version": "1.0.0b1",
        "report_version": 1,
        "configuration_id": f"config-{repository}",
        "environment_id": "hosted-linux-x64",
        "eligible": True,
        "changed_tests_or_config": sampled,
        "dunnit_outcome": "fail" if alert else "pass",
        "alert": alert,
        "rule_ids": ["tamper.added-skip"] if alert else [],
        "existing_ci": "pass",
        "adjudication": (
            "true_positive" if alert else ("no_issue" if sampled else "not_reviewed")
        ),
        "resolution": "corrected" if alert else "none",
        "incomplete_pass": False,
        "scanner_duration_seconds": 0.1,
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    cohort = tmp_path / "cohort.json"
    events = tmp_path / "events.jsonl"
    benchmark = tmp_path / "benchmark.json"
    _write_json(
        cohort,
        {
            "protocol": pilot.PROTOCOL,
            "repositories": [_repository(index) for index in range(5)],
        },
    )
    _write_jsonl(
        events,
        [
            *[
                _event(repository, index, phase="shadow")
                for repository in range(5)
                for index in range(20)
            ],
            *[
                _event(repository, index, phase="required")
                for repository in range(5)
                for index in range(10)
            ],
        ],
    )
    _write_json(
        benchmark,
        {"gates": {"status": "pass"}, "performance": {"p95_seconds": 0.1}},
    )
    return cohort, events, benchmark


def test_complete_pilot_passes_stable_reliance_gate(tmp_path: Path) -> None:
    cohort, events, benchmark = _inputs(tmp_path)

    report = pilot.aggregate(cohort, events, benchmark)

    assert report["cohort"] == {
        "repository_count": 5,
        "ecosystems": ["go", "javascript", "python"],
        "monorepository_count": 1,
        "median_onboarding_minutes": 7.0,
        "repositories_without_hand_authored_yaml": 4,
        "onboarding_minutes": {"minimum": 5.0, "median": 7.0, "maximum": 9.0},
    }
    assert report["shadow"]["eligible_candidates"] == 100
    assert report["shadow"]["alert_precision"]["rate"] == 1.0
    assert report["required"]["eligible_candidates"] == 50
    assert report["gates"]["status"] == "pass"
    assert all(value is True for key, value in report["gates"].items() if key != "status")
    assert report["incidents"] == {
        "qualifying_count": 5,
        "repository_count": 5,
        "claim_gate": True,
    }


def test_missing_required_reliance_fails_closed(tmp_path: Path) -> None:
    cohort, events, benchmark = _inputs(tmp_path)
    value = json.loads(cohort.read_text(encoding="utf-8"))
    value["repositories"][0].update(
        {
            "required_started_at": None,
            "required_observed_through": None,
            "required_continuously_enabled": False,
            "maintainer_confirmed": False,
        }
    )
    _write_json(cohort, value)
    records = [
        item
        for item in map(json.loads, events.read_text(encoding="utf-8").splitlines())
        if not (item["repository_id"] == "repo-0" and item["phase"] == "required")
    ]
    _write_jsonl(events, records)

    report = pilot.aggregate(cohort, events, benchmark)

    assert report["gates"]["required_check_reliance"] is False
    assert report["gates"]["status"] == "fail"


def test_duplicate_candidate_is_rejected(tmp_path: Path) -> None:
    cohort, events, benchmark = _inputs(tmp_path)
    lines = events.read_text(encoding="utf-8").splitlines()
    events.write_text("\n".join([*lines, lines[0]]) + "\n", encoding="utf-8")

    with pytest.raises(pilot.PilotValidationError, match="duplicate candidate"):
        pilot.aggregate(cohort, events, benchmark)


def test_every_alert_requires_adjudication(tmp_path: Path) -> None:
    cohort, events, benchmark = _inputs(tmp_path)
    records = list(map(json.loads, events.read_text(encoding="utf-8").splitlines()))
    records[0]["adjudication"] = "not_reviewed"
    _write_jsonl(events, records)

    with pytest.raises(pilot.PilotValidationError, match="every alert must be adjudicated"):
        pilot.aggregate(cohort, events, benchmark)
