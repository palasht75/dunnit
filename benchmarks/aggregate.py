#!/usr/bin/env python3
"""Validate and aggregate Dunnit's preregistered benchmark JSONL files.

The manifest format is defined by ``case.schema.json``. Raw result files contain
one record for every case/operating-system pair with these exact keys::

    {
      "case_id": "python-skip-001",
      "protocol": "dunnit-benchmark-v1",
      "operating_system": "linux",
      "outcome": "fail",
      "findings": [
        {"rule_id": "tamper.added-skip", "severity": "error", "path": "tests/test_a.py"}
      ],
      "paths_complete": true,
      "content_complete": true,
      "identity_complete": true,
      "infrastructure_error": false,
      "changed_path_count": 3,
      "inspectable_bytes": 1842,
      "scanner_durations_seconds": [0.031, 0.030, 0.029, 0.031, 0.030]
    }

The file is intentionally dependency-free so the published calculation can be
rerun with CPython alone. By default, the command rejects anything other than
the frozen 300-case corpus. ``--allow-partial`` is only for harness development;
quality gates remain ``not_evaluated`` until the frozen corpus is complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL = "dunnit-benchmark-v1"
OUTCOMES = {"pass", "pass_with_warnings", "fail", "error"}
OPERATING_SYSTEMS = {"linux", "windows", "macos"}
ADVERSARIAL_CATEGORIES = (
    "trust-fail-closed",
    "deleted-renamed-tests",
    "skip-focus",
    "assertion-weakening",
    "test-deselection",
    "stub-suppression",
    "command-integrity",
)
BENIGN_CATEGORIES = (
    "normal-strengthening",
    "marker-context",
    "legitimate-config",
    "declared-writes",
    "unusual-path",
    "topology-line-endings",
)
ECOSYSTEMS = (
    "python",
    "javascript-typescript",
    "go",
    "rust",
    "jvm",
    "ruby",
    "csharp",
    "php",
    "mixed",
)
TOPOLOGIES = {
    "normal",
    "spaces",
    "unicode",
    "tabs",
    "lf",
    "crlf",
    "binary",
    "oversized",
    "shallow-sufficient",
    "shallow-missing-history",
    "detached-head",
    "unborn",
    "worktree",
    "monorepo",
    "committed",
    "staged",
    "unstaged",
    "untracked",
    "many-untracked",
}

_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_Z_95 = 1.959963984540054

_ADVERSARIAL_COUNTS: Mapping[str, tuple[int, ...]] = {
    "python": (5, 3, 4, 4, 4, 2, 3),
    "javascript-typescript": (5, 3, 5, 3, 4, 2, 3),
    "go": (5, 4, 4, 4, 2, 3, 3),
    "rust": (5, 4, 3, 4, 2, 4, 3),
    "jvm": (5, 4, 4, 4, 3, 2, 3),
    "ruby": (5, 4, 4, 4, 2, 3, 3),
    "csharp": (5, 4, 4, 4, 3, 2, 3),
    "php": (5, 4, 4, 3, 3, 3, 3),
}
_BENIGN_COUNTS: Mapping[str, tuple[int, ...]] = {
    "python": (5, 2, 2, 1, 1, 1),
    "javascript-typescript": (5, 2, 2, 1, 1, 1),
    "go": (5, 2, 1, 1, 1, 2),
    "rust": (5, 2, 1, 1, 1, 2),
    "jvm": (5, 2, 2, 1, 1, 1),
    "ruby": (5, 2, 1, 1, 1, 2),
    "csharp": (5, 2, 2, 1, 1, 1),
    "php": (5, 2, 2, 1, 1, 1),
    "mixed": (0, 0, 0, 1, 1, 2),
}


class BenchmarkValidationError(ValueError):
    """The manifest or raw result set is not safe to aggregate."""


@dataclass(frozen=True, order=True)
class Finding:
    rule_id: str
    severity: str
    path: str | None


@dataclass(frozen=True)
class Case:
    case_id: str
    ecosystem: str
    case_class: str
    category: str
    fixture: str
    fixture_sha256: str
    operating_systems: tuple[str, ...]
    topologies: tuple[str, ...]
    expected_outcome: str
    expected_findings: frozenset[Finding]
    forbidden_rule_ids: frozenset[str]
    expected_paths_complete: bool
    expected_content_complete: bool


@dataclass(frozen=True)
class Result:
    case_id: str
    operating_system: str
    outcome: str
    findings: frozenset[Finding]
    paths_complete: bool
    content_complete: bool
    identity_complete: bool
    infrastructure_error: bool
    changed_path_count: int
    inspectable_bytes: int
    scanner_durations: tuple[float, ...]


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkValidationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise BenchmarkValidationError(f"non-finite JSON number {value!r} is not permitted")


def _read_jsonl(path: Path, *, kind: str) -> tuple[list[dict[str, Any]], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BenchmarkValidationError(f"cannot read {kind} {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BenchmarkValidationError(f"{kind} is not valid UTF-8: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise BenchmarkValidationError(f"{kind} line {line_number} is blank")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_duplicate_safe_object,
                parse_constant=_reject_nonfinite_json,
            )
        except (json.JSONDecodeError, BenchmarkValidationError) as exc:
            raise BenchmarkValidationError(f"{kind} line {line_number}: {exc}") from exc
        if type(value) is not dict:
            raise BenchmarkValidationError(f"{kind} line {line_number} must be a JSON object")
        records.append(value)
    if not records:
        raise BenchmarkValidationError(f"{kind} contains no records")
    return records, digest


def _exact_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    where: str,
) -> None:
    optional = optional or set()
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise BenchmarkValidationError(f"{where} is missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise BenchmarkValidationError(f"{where} has unknown keys: {', '.join(sorted(extra))}")


def _text(value: Any, *, where: str) -> str:
    if type(value) is not str or not value:
        raise BenchmarkValidationError(f"{where} must be a non-empty string")
    return value


def _boolean(value: Any, *, where: str) -> bool:
    if type(value) is not bool:
        raise BenchmarkValidationError(f"{where} must be a boolean")
    return value


def _unique_strings(
    value: Any,
    *,
    where: str,
    allowed: set[str] | None = None,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if type(value) is not list or (not value and not allow_empty):
        suffix = "an array" if allow_empty else "a non-empty array"
        raise BenchmarkValidationError(f"{where} must be {suffix}")
    items = tuple(_text(item, where=f"{where} item") for item in value)
    if len(set(items)) != len(items):
        raise BenchmarkValidationError(f"{where} contains duplicate values")
    if allowed is not None:
        unknown = set(items) - allowed
        if unknown:
            raise BenchmarkValidationError(
                f"{where} contains unsupported values: {', '.join(sorted(unknown))}"
            )
    return items


def _repository_path(value: Any, *, where: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    path = _text(value, where=where)
    parts = path.split("/")
    if (
        path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in parts)
        or (len(path) >= 2 and path[0].isalpha() and path[1] == ":")
    ):
        raise BenchmarkValidationError(f"{where} must be a safe repository-relative path")
    return path


def _finding(value: Any, *, where: str, expected: bool) -> Finding:
    if type(value) is not dict:
        raise BenchmarkValidationError(f"{where} must be an object")
    _exact_keys(
        value,
        required={"rule_id", "severity", "path"},
        where=where,
    )
    severity = _text(value["severity"], where=f"{where}.severity")
    allowed = {"warning", "error"} if expected else {"info", "warning", "error"}
    if severity not in allowed:
        raise BenchmarkValidationError(
            f"{where}.severity must be one of {', '.join(sorted(allowed))}"
        )
    return Finding(
        rule_id=_text(value["rule_id"], where=f"{where}.rule_id"),
        severity=severity,
        path=_repository_path(value["path"], where=f"{where}.path", nullable=True),
    )


def _findings(value: Any, *, where: str, expected: bool) -> frozenset[Finding]:
    if type(value) is not list:
        raise BenchmarkValidationError(f"{where} must be an array")
    items = [_finding(item, where=f"{where}[{index}]", expected=expected) for index, item in enumerate(value)]
    if len(set(items)) != len(items):
        raise BenchmarkValidationError(f"{where} contains duplicate findings")
    return frozenset(items)


def _parse_case(value: Mapping[str, Any], *, line_number: int) -> Case:
    where = f"manifest line {line_number}"
    required = {
        "id",
        "protocol",
        "ecosystem",
        "class",
        "category",
        "fixture",
        "fixture_sha256",
        "operating_systems",
        "topology",
        "expected",
        "rationale",
        "license",
    }
    _exact_keys(value, required=required, where=where)
    case_id = _text(value["id"], where=f"{where}.id")
    if not _CASE_ID.fullmatch(case_id):
        raise BenchmarkValidationError(f"{where}.id is not a valid benchmark case ID")
    if value["protocol"] != PROTOCOL:
        raise BenchmarkValidationError(f"{where}.protocol must be {PROTOCOL!r}")
    ecosystem = _text(value["ecosystem"], where=f"{where}.ecosystem")
    if ecosystem not in ECOSYSTEMS:
        raise BenchmarkValidationError(f"{where}.ecosystem is unsupported")
    case_class = _text(value["class"], where=f"{where}.class")
    if case_class not in {"adversarial", "benign"}:
        raise BenchmarkValidationError(f"{where}.class must be 'adversarial' or 'benign'")
    category = _text(value["category"], where=f"{where}.category")
    categories = ADVERSARIAL_CATEGORIES if case_class == "adversarial" else BENIGN_CATEGORIES
    if category not in categories:
        raise BenchmarkValidationError(f"{where}.category is invalid for class {case_class!r}")
    fixture = _repository_path(value["fixture"], where=f"{where}.fixture")
    assert fixture is not None
    if not fixture.startswith("fixtures/"):
        raise BenchmarkValidationError(f"{where}.fixture must be below fixtures/")
    fixture_sha256 = _text(value["fixture_sha256"], where=f"{where}.fixture_sha256")
    if not _SHA256.fullmatch(fixture_sha256):
        raise BenchmarkValidationError(f"{where}.fixture_sha256 must be lowercase SHA-256")
    operating_systems = _unique_strings(
        value["operating_systems"],
        where=f"{where}.operating_systems",
        allowed=OPERATING_SYSTEMS,
    )
    topologies = _unique_strings(
        value["topology"],
        where=f"{where}.topology",
        allowed=TOPOLOGIES,
    )
    expected = value["expected"]
    if type(expected) is not dict:
        raise BenchmarkValidationError(f"{where}.expected must be an object")
    _exact_keys(
        expected,
        required={"outcome", "findings", "paths_complete", "content_complete"},
        optional={"forbidden_rule_ids"},
        where=f"{where}.expected",
    )
    expected_outcome = _text(expected["outcome"], where=f"{where}.expected.outcome")
    if expected_outcome not in OUTCOMES:
        raise BenchmarkValidationError(f"{where}.expected.outcome is unsupported")
    expected_findings = _findings(
        expected["findings"], where=f"{where}.expected.findings", expected=True
    )
    forbidden = _unique_strings(
        expected.get("forbidden_rule_ids", []),
        where=f"{where}.expected.forbidden_rule_ids",
        allow_empty=True,
    )
    paths_complete = _boolean(
        expected["paths_complete"], where=f"{where}.expected.paths_complete"
    )
    content_complete = _boolean(
        expected["content_complete"], where=f"{where}.expected.content_complete"
    )
    if case_class == "adversarial":
        if expected_outcome == "pass" or not expected_findings:
            raise BenchmarkValidationError(
                f"{where}: adversarial cases require a non-pass outcome and findings"
            )
    else:
        if expected_outcome != "pass" or expected_findings or not forbidden:
            raise BenchmarkValidationError(
                f"{where}: benign cases require pass, no findings, and forbidden rule IDs"
            )
    if category in {
        "trust-fail-closed",
        "test-deselection",
        "command-integrity",
    } and (
        expected_outcome not in {"fail", "error"}
        or not any(item.severity == "error" for item in expected_findings)
    ):
        raise BenchmarkValidationError(
            f"{where}: {category} requires fail/error and an error-severity finding"
        )
    _text(value["rationale"], where=f"{where}.rationale")
    _text(value["license"], where=f"{where}.license")
    return Case(
        case_id=case_id,
        ecosystem=ecosystem,
        case_class=case_class,
        category=category,
        fixture=fixture,
        fixture_sha256=fixture_sha256,
        operating_systems=operating_systems,
        topologies=topologies,
        expected_outcome=expected_outcome,
        expected_findings=expected_findings,
        forbidden_rule_ids=frozenset(forbidden),
        expected_paths_complete=paths_complete,
        expected_content_complete=content_complete,
    )


def read_manifest(path: Path) -> tuple[dict[str, Case], str]:
    records, digest = _read_jsonl(path, kind="manifest")
    cases: dict[str, Case] = {}
    fixtures: dict[str, str] = {}
    fixture_digests: dict[str, str] = {}
    for line_number, record in enumerate(records, 1):
        case = _parse_case(record, line_number=line_number)
        if case.case_id in cases:
            raise BenchmarkValidationError(f"duplicate manifest case ID {case.case_id!r}")
        if case.fixture in fixtures:
            raise BenchmarkValidationError(
                f"fixture {case.fixture!r} is reused by {fixtures[case.fixture]!r} and {case.case_id!r}"
            )
        if case.fixture_sha256 in fixture_digests:
            raise BenchmarkValidationError(
                "fixture digest is reused by "
                f"{fixture_digests[case.fixture_sha256]!r} and {case.case_id!r}"
            )
        cases[case.case_id] = case
        fixtures[case.fixture] = case.case_id
        fixture_digests[case.fixture_sha256] = case.case_id
    return cases, digest


def _durations(value: Any, *, where: str) -> tuple[float, ...]:
    if type(value) is not list:
        raise BenchmarkValidationError(f"{where} must be an array")
    durations: list[float] = []
    for index, item in enumerate(value):
        if type(item) not in {int, float} or not math.isfinite(item) or item < 0:
            raise BenchmarkValidationError(
                f"{where}[{index}] must be a finite non-negative number"
            )
        durations.append(float(item))
    return tuple(durations)


def _nonnegative_integer(value: Any, *, where: str) -> int:
    if type(value) is not int or value < 0:
        raise BenchmarkValidationError(f"{where} must be a non-negative integer")
    return value


def _parse_result(value: Mapping[str, Any], *, line_number: int) -> Result:
    where = f"results line {line_number}"
    _exact_keys(
        value,
        required={
            "case_id",
            "protocol",
            "operating_system",
            "outcome",
            "findings",
            "paths_complete",
            "content_complete",
            "identity_complete",
            "infrastructure_error",
            "changed_path_count",
            "inspectable_bytes",
            "scanner_durations_seconds",
        },
        where=where,
    )
    case_id = _text(value["case_id"], where=f"{where}.case_id")
    if not _CASE_ID.fullmatch(case_id):
        raise BenchmarkValidationError(f"{where}.case_id is invalid")
    if value["protocol"] != PROTOCOL:
        raise BenchmarkValidationError(f"{where}.protocol must be {PROTOCOL!r}")
    operating_system = _text(value["operating_system"], where=f"{where}.operating_system")
    if operating_system not in OPERATING_SYSTEMS:
        raise BenchmarkValidationError(f"{where}.operating_system is unsupported")
    outcome = _text(value["outcome"], where=f"{where}.outcome")
    if outcome not in OUTCOMES:
        raise BenchmarkValidationError(f"{where}.outcome is unsupported")
    findings = _findings(value["findings"], where=f"{where}.findings", expected=False)
    alerts = {item for item in findings if item.severity in {"warning", "error"}}
    if outcome == "pass" and alerts:
        raise BenchmarkValidationError(f"{where}: pass outcome cannot contain alert findings")
    if outcome == "pass_with_warnings" and (
        not any(item.severity == "warning" for item in alerts)
        or any(item.severity == "error" for item in alerts)
    ):
        raise BenchmarkValidationError(
            f"{where}: pass_with_warnings requires warnings and no error findings"
        )
    if outcome in {"fail", "error"} and not any(item.severity == "error" for item in alerts):
        raise BenchmarkValidationError(f"{where}: {outcome} requires an error finding")
    infrastructure_error = _boolean(
        value["infrastructure_error"], where=f"{where}.infrastructure_error"
    )
    if infrastructure_error and outcome != "error":
        raise BenchmarkValidationError(
            f"{where}: infrastructure_error=true requires outcome='error'"
        )
    return Result(
        case_id=case_id,
        operating_system=operating_system,
        outcome=outcome,
        findings=findings,
        paths_complete=_boolean(value["paths_complete"], where=f"{where}.paths_complete"),
        content_complete=_boolean(
            value["content_complete"], where=f"{where}.content_complete"
        ),
        identity_complete=_boolean(
            value["identity_complete"], where=f"{where}.identity_complete"
        ),
        infrastructure_error=infrastructure_error,
        changed_path_count=_nonnegative_integer(
            value["changed_path_count"], where=f"{where}.changed_path_count"
        ),
        inspectable_bytes=_nonnegative_integer(
            value["inspectable_bytes"], where=f"{where}.inspectable_bytes"
        ),
        scanner_durations=_durations(
            value["scanner_durations_seconds"],
            where=f"{where}.scanner_durations_seconds",
        ),
    )


def read_results(
    path: Path, cases: Mapping[str, Case]
) -> tuple[dict[str, tuple[Result, ...]], str]:
    records, digest = _read_jsonl(path, kind="results")
    grouped: dict[str, list[Result]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for line_number, record in enumerate(records, 1):
        result = _parse_result(record, line_number=line_number)
        case = cases.get(result.case_id)
        if case is None:
            raise BenchmarkValidationError(f"result references unknown case {result.case_id!r}")
        if result.operating_system not in case.operating_systems:
            raise BenchmarkValidationError(
                f"result for {result.case_id!r} uses undeclared OS {result.operating_system!r}"
            )
        key = (result.case_id, result.operating_system)
        if key in seen:
            raise BenchmarkValidationError(
                f"duplicate result for case/OS {result.case_id!r}/{result.operating_system!r}"
            )
        seen.add(key)
        grouped[result.case_id].append(result)
    missing = [
        f"{case.case_id}/{operating_system}"
        for case in cases.values()
        for operating_system in case.operating_systems
        if (case.case_id, operating_system) not in seen
    ]
    if missing:
        rendered = ", ".join(sorted(missing)[:10])
        suffix = "" if len(missing) <= 10 else f" (+{len(missing) - 10} more)"
        raise BenchmarkValidationError(f"missing results: {rendered}{suffix}")
    return {
        case_id: tuple(sorted(items, key=lambda item: item.operating_system))
        for case_id, items in grouped.items()
    }, digest


def wilson_proportion(numerator: int, denominator: int) -> dict[str, Any]:
    """Return exact counts, point estimate, and a two-sided 95% Wilson interval."""

    if type(numerator) is not int or type(denominator) is not int:
        raise TypeError("Wilson counts must be integers")
    if denominator < 0 or numerator < 0 or numerator > denominator:
        raise ValueError("Wilson counts must satisfy 0 <= numerator <= denominator")
    if denominator == 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "rate": None,
            "wilson_95": None,
        }
    rate = numerator / denominator
    z_squared = _Z_95 * _Z_95
    scale = 1.0 + z_squared / denominator
    center = (rate + z_squared / (2.0 * denominator)) / scale
    margin = (
        _Z_95
        * math.sqrt(
            rate * (1.0 - rate) / denominator
            + z_squared / (4.0 * denominator * denominator)
        )
        / scale
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "wilson_95": {
            "confidence": 0.95,
            "lower": 0.0 if numerator == 0 else max(0.0, center - margin),
            "upper": 1.0 if numerator == denominator else min(1.0, center + margin),
            "method": "wilson-score",
        },
    }


def _expected_distribution() -> Counter[tuple[str, str, str]]:
    expected: Counter[tuple[str, str, str]] = Counter()
    for ecosystem, counts in _ADVERSARIAL_COUNTS.items():
        for category, count in zip(ADVERSARIAL_CATEGORIES, counts):
            expected[(ecosystem, "adversarial", category)] = count
    for ecosystem, counts in _BENIGN_COUNTS.items():
        for category, count in zip(BENIGN_CATEGORIES, counts):
            expected[(ecosystem, "benign", category)] = count
    return expected


def _frozen_corpus_issues(
    cases: Mapping[str, Case], results: Mapping[str, Sequence[Result]]
) -> list[str]:
    issues: list[str] = []
    actual = Counter(
        (case.ecosystem, case.case_class, case.category) for case in cases.values()
    )
    expected = _expected_distribution()
    for key in sorted(set(actual) | set(expected)):
        if actual[key] != expected[key]:
            ecosystem, case_class, category = key
            issues.append(
                f"{ecosystem}/{case_class}/{category}: expected {expected[key]}, got {actual[key]}"
            )
    present_topologies = {item for case in cases.values() for item in case.topologies}
    required_topologies = TOPOLOGIES - {"normal"}
    missing_topologies = required_topologies - present_topologies
    if missing_topologies:
        issues.append("missing topology variants: " + ", ".join(sorted(missing_topologies)))
    for ecosystem in ECOSYSTEMS[:-1]:
        if not any(
            case.ecosystem == ecosystem
            and OPERATING_SYSTEMS.issubset(case.operating_systems)
            for case in cases.values()
        ):
            issues.append(f"{ecosystem}: no case declares Linux, Windows, and macOS")
    for case_id, case_results in sorted(results.items()):
        sizes = {(item.changed_path_count, item.inspectable_bytes) for item in case_results}
        if len(sizes) != 1:
            issues.append(f"{case_id}: changed-path/inspectable-byte metadata differs by OS")
        linux = next(
            (item for item in case_results if item.operating_system == "linux"), None
        )
        if linux is None:
            issues.append(f"{case_id}: no Linux reference result")
        elif len(linux.scanner_durations) != 5:
            issues.append(
                f"{case_id}: expected 5 Linux scanner measurements, "
                f"got {len(linux.scanner_durations)}"
            )
        non_linux_count = sum(
            len(item.scanner_durations)
            for item in case_results
            if item.operating_system != "linux"
        )
        if non_linux_count:
            issues.append(
                f"{case_id}: found {non_linux_count} scanner measurements outside Linux"
            )
    return issues


def _execution_correct(case: Case, result: Result) -> bool:
    if result.infrastructure_error or not result.identity_complete:
        return False
    if (
        result.paths_complete != case.expected_paths_complete
        or result.content_complete != case.expected_content_complete
    ):
        return False
    if not case.expected_findings.issubset(result.findings):
        return False
    if case.case_class == "benign":
        alerts = [item for item in result.findings if item.severity in {"warning", "error"}]
        forbidden = case.forbidden_rule_ids & {item.rule_id for item in result.findings}
        return result.outcome == "pass" and not alerts and not forbidden
    if case.category == "trust-fail-closed" or case.expected_outcome == "error":
        return result.outcome == case.expected_outcome
    if case.expected_outcome == "fail":
        return result.outcome == "fail"
    return result.outcome in {"pass_with_warnings", "fail"}


def _case_correct(case: Case, results: Sequence[Result]) -> bool:
    return bool(results) and all(_execution_correct(case, result) for result in results)


def _case_alerted(results: Sequence[Result]) -> bool:
    return any(
        result.outcome != "pass"
        or any(item.severity in {"warning", "error"} for item in result.findings)
        for result in results
    )


def _benign_false_alarm(results: Sequence[Result]) -> bool:
    """Classify only emitted warnings/failures/errors as benign false alarms.

    Completeness defects are reported separately as integrity failures. Keeping
    the measures separate prevents an invalid pass from being mislabeled as a
    detector false positive.
    """

    return _case_alerted(results)


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _performance_summary(values: Sequence[float]) -> dict[str, Any]:
    return {
        "measurements": len(values),
        "p50_seconds": _nearest_rank(values, 0.50),
        "p95_seconds": _nearest_rank(values, 0.95),
        "maximum_seconds": max(values) if values else None,
    }


def _performance_by(results: Sequence[Result], attribute: str) -> dict[str, dict[str, Any]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for result in results:
        grouped[int(getattr(result, attribute))].extend(result.scanner_durations)
    return {
        str(value): _performance_summary(durations)
        for value, durations in sorted(grouped.items())
        if durations
    }


def _proportions_by(
    cases: Iterable[Case], correct: Mapping[str, bool], attribute: str
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        grouped[str(getattr(case, attribute))].append(case)
    return {
        key: wilson_proportion(
            sum(1 for case in items if correct[case.case_id]),
            len(items),
        )
        for key, items in sorted(grouped.items())
    }


def aggregate(
    manifest_path: Path,
    results_path: Path,
    *,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Validate both inputs and return a deterministic aggregate report."""

    cases, manifest_digest = read_manifest(manifest_path)
    results, results_digest = read_results(results_path, cases)
    corpus_issues = _frozen_corpus_issues(cases, results)
    if corpus_issues and not allow_partial:
        rendered = "; ".join(corpus_issues[:10])
        suffix = "" if len(corpus_issues) <= 10 else f" (+{len(corpus_issues) - 10} more)"
        raise BenchmarkValidationError(f"not the frozen 300-case corpus: {rendered}{suffix}")
    corpus_complete = not corpus_issues

    correct = {
        case_id: _case_correct(case, results[case_id]) for case_id, case in cases.items()
    }
    adversarial = [case for case in cases.values() if case.case_class == "adversarial"]
    benign = [case for case in cases.values() if case.case_class == "benign"]
    detected_count = sum(1 for case in adversarial if correct[case.case_id])
    false_alarm_count = sum(
        1 for case in benign if _benign_false_alarm(results[case.case_id])
    )
    missed_case_ids = sorted(
        case.case_id for case in adversarial if not correct[case.case_id]
    )
    false_alarm_case_ids = sorted(
        case.case_id
        for case in benign
        if _benign_false_alarm(results[case.case_id])
    )
    trust_cases = [case for case in adversarial if case.category == "trust-fail-closed"]
    trust_detected = sum(1 for case in trust_cases if correct[case.case_id])
    trust_missed_case_ids = sorted(
        case.case_id for case in trust_cases if not correct[case.case_id]
    )

    category_sensitivity = _proportions_by(adversarial, correct, "category")
    ecosystem_sensitivity = _proportions_by(adversarial, correct, "ecosystem")
    defined_category_rates = [
        metric["rate"] for metric in category_sensitivity.values() if metric["rate"] is not None
    ]
    macro_sensitivity = (
        sum(defined_category_rates) / len(defined_category_rates)
        if defined_category_rates
        else None
    )

    alerted_case_ids = {
        case_id for case_id, case_results in results.items() if _case_alerted(case_results)
    }
    true_positive_alerts = sum(
        1
        for case in adversarial
        if case.case_id in alerted_case_ids and correct[case.case_id]
    )
    infrastructure_executions = [
        result
        for case_results in results.values()
        for result in case_results
        if result.infrastructure_error
    ]
    infrastructure_cases = {
        result.case_id for result in infrastructure_executions
    }
    incomplete_pass_executions = [
        result
        for case_results in results.values()
        for result in case_results
        if result.outcome in {"pass", "pass_with_warnings"}
        and not (
            result.paths_complete
            and result.content_complete
            and result.identity_complete
        )
    ]
    incomplete_pass_cases = {result.case_id for result in incomplete_pass_executions}
    all_results = [result for case_results in results.values() for result in case_results]
    outcome_counts = Counter(result.outcome for result in all_results)
    durations = [
        duration for result in all_results for duration in result.scanner_durations
    ]

    observed_gates = {
        "trust_root_100_percent": bool(trust_cases) and trust_detected == len(trust_cases),
        "macro_sensitivity_at_least_95_percent": (
            macro_sensitivity is not None and macro_sensitivity >= 0.95
        ),
        "benign_false_alarm_at_most_5_percent": (
            bool(benign) and false_alarm_count / len(benign) <= 0.05
        ),
        "zero_incomplete_passes": not incomplete_pass_executions,
        "scanner_p95_below_2_seconds": (
            bool(durations) and (_nearest_rank(durations, 0.95) or 0.0) < 2.0
        ),
    }
    gates: dict[str, Any]
    if corpus_complete:
        gates = {
            "status": "pass" if all(observed_gates.values()) else "fail",
            **observed_gates,
        }
    else:
        gates = {
            "status": "not_evaluated",
            **{key: None for key in observed_gates},
        }

    return {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "manifest_sha256": manifest_digest,
            "results_sha256": results_digest,
        },
        "corpus": {
            "complete": corpus_complete,
            "case_count": len(cases),
            "adversarial_cases": len(adversarial),
            "benign_cases": len(benign),
            "execution_count": len(all_results),
            "validation_issues": corpus_issues,
        },
        "classification": {
            "adversarial_sensitivity": wilson_proportion(detected_count, len(adversarial)),
            "missed_adversarial_case_ids": missed_case_ids,
            "sensitivity_by_category": category_sensitivity,
            "sensitivity_by_ecosystem": ecosystem_sensitivity,
            "macro_sensitivity": {
                "category_count": len(defined_category_rates),
                "rate": macro_sensitivity,
            },
            "benign_false_alarm_rate": wilson_proportion(false_alarm_count, len(benign)),
            "benign_false_alarm_case_ids": false_alarm_case_ids,
            "trust_root_detection": wilson_proportion(trust_detected, len(trust_cases)),
            "missed_trust_root_case_ids": trust_missed_case_ids,
            "synthetic_alert_precision": wilson_proportion(
                true_positive_alerts, len(alerted_case_ids)
            ),
        },
        "integrity": {
            "infrastructure_error_cases": len(infrastructure_cases),
            "infrastructure_error_case_ids": sorted(infrastructure_cases),
            "infrastructure_error_executions": len(infrastructure_executions),
            "incomplete_pass_cases": len(incomplete_pass_cases),
            "incomplete_pass_case_ids": sorted(incomplete_pass_cases),
            "incomplete_pass_executions": len(incomplete_pass_executions),
            "outcome_counts": {outcome: outcome_counts[outcome] for outcome in sorted(OUTCOMES)},
        },
        "performance": {
            "scanner_measurements": len(durations),
            **_performance_summary(durations),
            "by_changed_path_count": _performance_by(all_results, "changed_path_count"),
            "by_inspectable_bytes": _performance_by(all_results, "inspectable_bytes"),
            "percentile_method": "nearest-rank",
        },
        "gates": gates,
    }


def _write_json(path: Path | None, value: Mapping[str, Any]) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path is None:
        sys.stdout.write(rendered)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and aggregate Dunnit's preregistered benchmark results."
    )
    parser.add_argument("manifest", type=Path, help="frozen manifest.jsonl")
    parser.add_argument("results", type=Path, help="append-only raw results JSONL")
    parser.add_argument("--output", type=Path, help="write aggregate JSON instead of stdout")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="accept a development subset; all quality gates remain not_evaluated",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = aggregate(args.manifest, args.results, allow_partial=args.allow_partial)
        _write_json(args.output, report)
    except (BenchmarkValidationError, OSError) as exc:
        print(f"benchmark input error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
