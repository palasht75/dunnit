#!/usr/bin/env python3
"""Validate and aggregate Dunnit's independent-repository pilot records.

The input deliberately identifies repositories and candidates only by study
IDs. It contains no source, remotes, author identities, or command output.
Missing evidence fails closed: this command reports which release gates are
still false rather than treating absent observations as success.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROTOCOL = "dunnit-pilot-v1"
PHASES = {"seeded", "shadow", "required"}
OUTCOMES = {"pass", "pass_with_warnings", "fail", "error"}
EXISTING_CI = {"pass", "fail", "not_run"}
ADJUDICATIONS = {
    "true_positive",
    "false_positive",
    "false_negative",
    "no_issue",
    "not_reviewed",
}
RESOLUTIONS = {"corrected", "reverted", "accepted", "none"}
_Z_95 = 1.959963984540054


class PilotValidationError(ValueError):
    """Pilot data is malformed, contradictory, or incomplete as a record."""


def _exact_keys(value: dict[str, Any], *, required: set[str], where: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing:
        raise PilotValidationError(f"{where} is missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise PilotValidationError(f"{where} has unknown keys: {', '.join(sorted(extra))}")


def _text(value: Any, *, where: str) -> str:
    if type(value) is not str or not value.strip():
        raise PilotValidationError(f"{where} must be a non-empty string")
    return value


def _boolean(value: Any, *, where: str) -> bool:
    if type(value) is not bool:
        raise PilotValidationError(f"{where} must be a boolean")
    return value


def _number(value: Any, *, where: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise PilotValidationError(f"{where} must be a finite non-negative number")
    return float(value)


def _timestamp(value: Any, *, where: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    rendered = _text(value, where=where)
    if not rendered.endswith("Z"):
        raise PilotValidationError(f"{where} must be an RFC 3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(rendered[:-1] + "+00:00")
    except ValueError as exc:
        raise PilotValidationError(f"{where} is not a valid timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise PilotValidationError(f"{where} must use UTC")
    return parsed


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PilotValidationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_json(text: str, *, where: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PilotValidationError(f"non-finite JSON number {value!r}")
            ),
        )
    except (ValueError, PilotValidationError) as exc:
        raise PilotValidationError(f"{where}: {exc}") from exc


def _read_json(path: Path, *, kind: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PilotValidationError(f"cannot read {kind} {path}: {exc}") from exc
    return _decode_json(text, where=kind)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PilotValidationError(f"cannot read events {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise PilotValidationError(f"events line {line_number} is blank")
        value = _decode_json(line, where=f"events line {line_number}")
        if type(value) is not dict:
            raise PilotValidationError(f"events line {line_number} must be an object")
        records.append(value)
    if not records:
        raise PilotValidationError("events contains no records")
    return records


def _repository(value: Any, *, index: int) -> dict[str, Any]:
    where = f"cohort.repositories[{index}]"
    if type(value) is not dict:
        raise PilotValidationError(f"{where} must be an object")
    required = {
        "id",
        "ecosystems",
        "monorepo",
        "independent",
        "existing_required_ci",
        "consent_mode",
        "onboarding_minutes",
        "hand_authored_yaml",
        "shadow_started_at",
        "shadow_observed_through",
        "required_started_at",
        "required_observed_through",
        "required_continuously_enabled",
        "maintainer_confirmed",
    }
    _exact_keys(value, required=required, where=where)
    repository_id = _text(value["id"], where=f"{where}.id")
    ecosystems = value["ecosystems"]
    if (
        type(ecosystems) is not list
        or not ecosystems
        or any(type(item) is not str or not item for item in ecosystems)
        or len(set(ecosystems)) != len(ecosystems)
    ):
        raise PilotValidationError(f"{where}.ecosystems must contain unique non-empty strings")
    consent = _text(value["consent_mode"], where=f"{where}.consent_mode")
    if consent not in {"named", "anonymized"}:
        raise PilotValidationError(f"{where}.consent_mode must be named or anonymized")
    shadow_start = _timestamp(value["shadow_started_at"], where=f"{where}.shadow_started_at")
    shadow_end = _timestamp(
        value["shadow_observed_through"], where=f"{where}.shadow_observed_through"
    )
    required_start = _timestamp(
        value["required_started_at"], where=f"{where}.required_started_at", nullable=True
    )
    required_end = _timestamp(
        value["required_observed_through"],
        where=f"{where}.required_observed_through",
        nullable=True,
    )
    assert shadow_start is not None and shadow_end is not None
    if shadow_end < shadow_start:
        raise PilotValidationError(f"{where}: shadow observation ends before it starts")
    if (required_start is None) != (required_end is None):
        raise PilotValidationError(f"{where}: required-check timestamps must both be set or null")
    if required_start is not None and required_end is not None and required_end < required_start:
        raise PilotValidationError(f"{where}: required-check observation ends before it starts")
    return {
        "id": repository_id,
        "ecosystems": tuple(ecosystems),
        "monorepo": _boolean(value["monorepo"], where=f"{where}.monorepo"),
        "independent": _boolean(value["independent"], where=f"{where}.independent"),
        "existing_required_ci": _boolean(
            value["existing_required_ci"], where=f"{where}.existing_required_ci"
        ),
        "consent_mode": consent,
        "onboarding_minutes": _number(
            value["onboarding_minutes"], where=f"{where}.onboarding_minutes"
        ),
        "hand_authored_yaml": _boolean(
            value["hand_authored_yaml"], where=f"{where}.hand_authored_yaml"
        ),
        "shadow_started_at": shadow_start,
        "shadow_observed_through": shadow_end,
        "required_started_at": required_start,
        "required_observed_through": required_end,
        "required_continuously_enabled": _boolean(
            value["required_continuously_enabled"],
            where=f"{where}.required_continuously_enabled",
        ),
        "maintainer_confirmed": _boolean(
            value["maintainer_confirmed"], where=f"{where}.maintainer_confirmed"
        ),
    }


def _event(
    value: dict[str, Any], *, line_number: int, repositories: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    where = f"events line {line_number}"
    required = {
        "protocol",
        "repository_id",
        "ecosystem",
        "candidate_id",
        "occurred_at",
        "dunnit_version",
        "report_version",
        "configuration_id",
        "environment_id",
        "phase",
        "eligible",
        "changed_tests_or_config",
        "dunnit_outcome",
        "alert",
        "rule_ids",
        "existing_ci",
        "adjudication",
        "resolution",
        "incomplete_pass",
        "scanner_duration_seconds",
    }
    _exact_keys(value, required=required, where=where)
    if value["protocol"] != PROTOCOL:
        raise PilotValidationError(f"{where}.protocol must be {PROTOCOL!r}")
    repository_id = _text(value["repository_id"], where=f"{where}.repository_id")
    if repository_id not in repositories:
        raise PilotValidationError(f"{where} references unknown repository {repository_id!r}")
    ecosystem = _text(value["ecosystem"], where=f"{where}.ecosystem")
    if ecosystem not in repositories[repository_id]["ecosystems"]:
        raise PilotValidationError(
            f"{where}.ecosystem is not declared by repository {repository_id!r}"
        )
    candidate_id = _text(value["candidate_id"], where=f"{where}.candidate_id")
    dunnit_version = _text(value["dunnit_version"], where=f"{where}.dunnit_version")
    configuration_id = _text(
        value["configuration_id"], where=f"{where}.configuration_id"
    )
    environment_id = _text(value["environment_id"], where=f"{where}.environment_id")
    report_version = value["report_version"]
    if type(report_version) is not int or report_version < 1:
        raise PilotValidationError(f"{where}.report_version must be a positive integer")
    occurred_at = _timestamp(value["occurred_at"], where=f"{where}.occurred_at")
    phase = _text(value["phase"], where=f"{where}.phase")
    if phase not in PHASES:
        raise PilotValidationError(f"{where}.phase is unsupported")
    outcome = _text(value["dunnit_outcome"], where=f"{where}.dunnit_outcome")
    if outcome not in OUTCOMES:
        raise PilotValidationError(f"{where}.dunnit_outcome is unsupported")
    existing_ci = _text(value["existing_ci"], where=f"{where}.existing_ci")
    if existing_ci not in EXISTING_CI:
        raise PilotValidationError(f"{where}.existing_ci is unsupported")
    adjudication = _text(value["adjudication"], where=f"{where}.adjudication")
    if adjudication not in ADJUDICATIONS:
        raise PilotValidationError(f"{where}.adjudication is unsupported")
    resolution = _text(value["resolution"], where=f"{where}.resolution")
    if resolution not in RESOLUTIONS:
        raise PilotValidationError(f"{where}.resolution is unsupported")
    alert = _boolean(value["alert"], where=f"{where}.alert")
    rules = value["rule_ids"]
    if (
        type(rules) is not list
        or any(type(item) is not str or not item for item in rules)
        or len(set(rules)) != len(rules)
    ):
        raise PilotValidationError(f"{where}.rule_ids must contain unique non-empty strings")
    if alert and adjudication not in {"true_positive", "false_positive"}:
        raise PilotValidationError(f"{where}: every alert must be adjudicated true or false positive")
    if not alert and adjudication in {"true_positive", "false_positive"}:
        raise PilotValidationError(f"{where}: a non-alert cannot be a true or false positive")
    if adjudication == "false_negative" and alert:
        raise PilotValidationError(f"{where}: a false negative cannot also be an alert")
    if outcome == "error" and alert:
        raise PilotValidationError(f"{where}: infrastructure errors are not detector alerts")
    if resolution in {"corrected", "reverted"} and adjudication not in {
        "true_positive",
        "false_negative",
    }:
        raise PilotValidationError(f"{where}: correction/revert requires a confirmed issue")
    if adjudication in {"true_positive", "false_positive", "false_negative"} and not rules:
        raise PilotValidationError(f"{where}: classified findings require at least one rule ID")
    incomplete_pass = _boolean(value["incomplete_pass"], where=f"{where}.incomplete_pass")
    if incomplete_pass and outcome not in {"pass", "pass_with_warnings"}:
        raise PilotValidationError(f"{where}: incomplete_pass requires a passing outcome")
    assert occurred_at is not None
    return {
        "repository_id": repository_id,
        "ecosystem": ecosystem,
        "candidate_id": candidate_id,
        "dunnit_version": dunnit_version,
        "report_version": report_version,
        "configuration_id": configuration_id,
        "environment_id": environment_id,
        "occurred_at": occurred_at,
        "phase": phase,
        "eligible": _boolean(value["eligible"], where=f"{where}.eligible"),
        "changed_tests_or_config": _boolean(
            value["changed_tests_or_config"], where=f"{where}.changed_tests_or_config"
        ),
        "dunnit_outcome": outcome,
        "alert": alert,
        "rule_ids": tuple(rules),
        "existing_ci": existing_ci,
        "adjudication": adjudication,
        "resolution": resolution,
        "incomplete_pass": incomplete_pass,
        "scanner_duration_seconds": _number(
            value["scanner_duration_seconds"], where=f"{where}.scanner_duration_seconds"
        ),
    }


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    if not denominator:
        return {"numerator": numerator, "denominator": denominator, "rate": None, "wilson_95": None}
    rate = numerator / denominator
    z_squared = _Z_95**2
    center = (rate + z_squared / (2 * denominator)) / (1 + z_squared / denominator)
    margin = _Z_95 * math.sqrt(
        (rate * (1 - rate) + z_squared / (4 * denominator)) / denominator
    ) / (1 + z_squared / denominator)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(rate, 8),
        "wilson_95": {
            "lower": round(max(0.0, center - margin), 8),
            "upper": round(min(1.0, center + margin), 8),
            "confidence": 0.95,
        },
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 8)


def _confusion(events: list[dict[str, Any]]) -> dict[str, Any]:
    labels = ("true_positive", "false_positive", "false_negative")
    overall = Counter(event["adjudication"] for event in events)
    by_ecosystem: dict[str, Counter[str]] = defaultdict(Counter)
    by_rule: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        label = event["adjudication"]
        if label not in labels:
            continue
        by_ecosystem[event["ecosystem"]][label] += 1
        for rule_id in event["rule_ids"]:
            by_rule[rule_id][label] += 1

    def render(counts: Counter[str]) -> dict[str, int]:
        return {label: counts[label] for label in labels}

    return {
        "overall": render(overall),
        "by_ecosystem": {key: render(value) for key, value in sorted(by_ecosystem.items())},
        "by_rule": {key: render(value) for key, value in sorted(by_rule.items())},
    }


def aggregate(cohort_path: Path, events_path: Path, benchmark_path: Path) -> dict[str, Any]:
    cohort = _read_json(cohort_path, kind="cohort")
    if type(cohort) is not dict:
        raise PilotValidationError("cohort must be an object")
    _exact_keys(cohort, required={"protocol", "repositories"}, where="cohort")
    if cohort["protocol"] != PROTOCOL:
        raise PilotValidationError(f"cohort.protocol must be {PROTOCOL!r}")
    raw_repositories = cohort["repositories"]
    if type(raw_repositories) is not list or not raw_repositories:
        raise PilotValidationError("cohort.repositories must be a non-empty array")
    repositories = [_repository(item, index=index) for index, item in enumerate(raw_repositories)]
    repository_ids = [item["id"] for item in repositories]
    if len(set(repository_ids)) != len(repository_ids):
        raise PilotValidationError("cohort contains duplicate repository IDs")
    by_id = {item["id"]: item for item in repositories}

    events = [
        _event(value, line_number=index, repositories=by_id)
        for index, value in enumerate(_read_jsonl(events_path), 1)
    ]
    seen_candidates: set[tuple[str, str]] = set()
    for event in events:
        key = (event["repository_id"], event["candidate_id"])
        if key in seen_candidates:
            raise PilotValidationError(
                f"duplicate candidate {event['candidate_id']!r} for {event['repository_id']!r}"
            )
        seen_candidates.add(key)
        repository = by_id[event["repository_id"]]
        start = repository[f"{event['phase']}_started_at"] if event["phase"] != "seeded" else None
        end = (
            repository[f"{event['phase']}_observed_through"]
            if event["phase"] != "seeded"
            else None
        )
        if start is not None and not (start <= event["occurred_at"] <= end):
            raise PilotValidationError(
                f"{event['repository_id']}/{event['candidate_id']}: event is outside its phase window"
            )

    benchmark = _read_json(benchmark_path, kind="benchmark aggregate")
    try:
        benchmark_status = benchmark["gates"]["status"]
        benchmark_p95 = benchmark["performance"]["p95_seconds"]
    except (KeyError, TypeError) as exc:
        raise PilotValidationError("benchmark aggregate lacks gates/performance fields") from exc

    shadow = [event for event in events if event["phase"] == "shadow" and event["eligible"]]
    required = [event for event in events if event["phase"] == "required" and event["eligible"]]
    seeded = [event for event in events if event["phase"] == "seeded"]
    shadow_by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    required_by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in shadow:
        shadow_by_repo[event["repository_id"]].append(event)
    for event in required:
        required_by_repo[event["repository_id"]].append(event)

    alerts = [event for event in shadow if event["alert"]]
    true_positives = sum(event["adjudication"] == "true_positive" for event in alerts)
    infrastructure_errors = sum(event["dunnit_outcome"] == "error" for event in shadow)
    incomplete_passes = sum(event["incomplete_pass"] for event in shadow)
    precision = _rate(true_positives, len(alerts))
    infrastructure_rate = _rate(infrastructure_errors, len(shadow))

    review_coverage: dict[str, dict[str, int | bool]] = {}
    review_coverage_ok = True
    for repository in repositories:
        candidates = [
            event
            for event in shadow_by_repo[repository["id"]]
            if not event["alert"] and event["changed_tests_or_config"]
        ]
        reviewed = sum(event["adjudication"] != "not_reviewed" for event in candidates)
        required_reviews = max(math.ceil(len(candidates) * 0.2), min(5, len(candidates)))
        passed = reviewed >= required_reviews
        review_coverage_ok = review_coverage_ok and passed
        review_coverage[repository["id"]] = {
            "eligible_unflagged_test_changes": len(candidates),
            "reviewed": reviewed,
            "required": required_reviews,
            "pass": passed,
        }

    onboarding_values = [repository["onboarding_minutes"] for repository in repositories]
    no_manual_yaml = sum(not repository["hand_authored_yaml"] for repository in repositories)
    eligible_repositories = all(
        repository["independent"] and repository["existing_required_ci"]
        for repository in repositories
    )
    ecosystems = sorted({item for repository in repositories for item in repository["ecosystems"]})
    cohort_shape = (
        len(repositories) >= 5 and len(ecosystems) >= 3 and any(r["monorepo"] for r in repositories)
    )

    shadow_repository_gates: dict[str, bool] = {}
    required_repository_gates: dict[str, bool] = {}
    for repository in repositories:
        shadow_days = (
            repository["shadow_observed_through"] - repository["shadow_started_at"]
        ).total_seconds() / 86400
        shadow_repository_gates[repository["id"]] = (
            shadow_days >= 56 and len(shadow_by_repo[repository["id"]]) >= 10
        )
        required_start = repository["required_started_at"]
        required_end = repository["required_observed_through"]
        required_days = (
            (required_end - required_start).total_seconds() / 86400
            if required_start is not None and required_end is not None
            else 0
        )
        required_repository_gates[repository["id"]] = (
            required_days >= 30
            and len(required_by_repo[repository["id"]]) >= 10
            and repository["required_continuously_enabled"]
            and repository["maintainer_confirmed"]
        )

    incidents = [
        event
        for event in (*shadow, *required)
        if event["alert"]
        and event["adjudication"] == "true_positive"
        and event["existing_ci"] == "pass"
        and event["resolution"] in {"corrected", "reverted"}
    ]
    incident_repositories = {event["repository_id"] for event in incidents}
    gates = {
        "eligible_independent_repositories": eligible_repositories,
        "cohort_shape": cohort_shape,
        "onboarding": statistics.median(onboarding_values) < 10 and no_manual_yaml >= 4,
        "benchmark": benchmark_status == "pass" and benchmark_p95 is not None and benchmark_p95 < 2,
        "shadow_duration_and_volume": len(shadow) >= 100 and all(shadow_repository_gates.values()),
        "review_coverage": review_coverage_ok,
        "alert_precision": precision["rate"] is not None and precision["rate"] >= 0.9,
        "infrastructure_error_rate": (
            infrastructure_rate["rate"] is not None and infrastructure_rate["rate"] < 0.01
        ),
        "no_incomplete_pass": incomplete_passes == 0,
        "required_check_reliance": all(required_repository_gates.values()),
    }
    gates["status"] = "pass" if all(gates.values()) else "fail"

    seeded_matrix = Counter()
    for event in seeded:
        if event["existing_ci"] == "not_run":
            seeded_matrix["not_comparable"] += 1
        elif event["alert"] and event["existing_ci"] == "fail":
            seeded_matrix["both"] += 1
        elif event["alert"]:
            seeded_matrix["dunnit_only"] += 1
        elif event["existing_ci"] == "fail":
            seeded_matrix["existing_ci_only"] += 1
        else:
            seeded_matrix["neither"] += 1
    scanner_durations = [event["scanner_duration_seconds"] for event in (*shadow, *required)]

    return {
        "protocol": PROTOCOL,
        "cohort": {
            "repository_count": len(repositories),
            "ecosystems": ecosystems,
            "monorepository_count": sum(repository["monorepo"] for repository in repositories),
            "median_onboarding_minutes": statistics.median(onboarding_values),
            "repositories_without_hand_authored_yaml": no_manual_yaml,
            "onboarding_minutes": {
                "minimum": min(onboarding_values),
                "median": statistics.median(onboarding_values),
                "maximum": max(onboarding_values),
            },
        },
        "seeded": {
            key: seeded_matrix[key]
            for key in ("existing_ci_only", "dunnit_only", "both", "neither", "not_comparable")
        },
        "shadow": {
            "eligible_candidates": len(shadow),
            "alert_precision": precision,
            "infrastructure_error_rate": infrastructure_rate,
            "incomplete_passes": incomplete_passes,
            "review_coverage": review_coverage,
            "repository_gates": shadow_repository_gates,
            "confusion": _confusion(shadow),
        },
        "required": {
            "eligible_candidates": len(required),
            "repository_gates": required_repository_gates,
        },
        "incidents": {
            "qualifying_count": len(incidents),
            "repository_count": len(incident_repositories),
            "claim_gate": len(incidents) >= 3 and len(incident_repositories) >= 2,
        },
        "benchmark": {"status": benchmark_status, "scanner_p95_seconds": benchmark_p95},
        "observed_scanner_performance": {
            "measurements": len(scanner_durations),
            "p50_seconds": _percentile(scanner_durations, 0.5),
            "p95_seconds": _percentile(scanner_durations, 0.95),
            "maximum_seconds": max(scanner_durations) if scanner_durations else None,
            "environment_ids": sorted({event["environment_id"] for event in (*shadow, *required)}),
        },
        "reproducibility": {
            "dunnit_versions": sorted({event["dunnit_version"] for event in events}),
            "report_versions": sorted({event["report_version"] for event in events}),
            "configuration_ids": sorted({event["configuration_id"] for event in events}),
        },
        "gates": gates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", type=Path)
    parser.add_argument("events", type=Path)
    parser.add_argument("benchmark_aggregate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = aggregate(args.cohort, args.events, args.benchmark_aggregate)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (PilotValidationError, OSError) as exc:
        print(f"pilot aggregation refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
