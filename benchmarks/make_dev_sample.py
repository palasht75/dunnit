#!/usr/bin/env python3
"""Generate the deterministic harness-development sample.

This is NOT the frozen 300-case corpus and its labels are NOT benchmark
evidence. It exists so the runner, the raw-record schema, and the aggregator
can be exercised end to end before independent reviewers build and adjudicate
the real corpus. ``aggregate.py`` refuses to score a partial corpus, so every
quality gate stays ``not_evaluated`` for this sample by construction.

    python benchmarks/make_dev_sample.py

Regenerating is idempotent: fixture bytes are fixed in this file, so the
manifest digests only change when a fixture is deliberately edited here.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run import current_operating_system, fixture_digest

PROTOCOL = "dunnit-benchmark-v1"
LICENSE = "MIT"

_CONTRACT = """version: 2
checks: []
protected:
  - dod.yaml
test_globs:
  - "tests/**"
  - "**/test_*.py"
tamper: true
stubs: true
strict: false
"""

_APP = """def add(a, b):
    return a + b


def multiply(a, b):
    return a * b
"""

_TEST_BASE = """from app import add, multiply


def test_add():
    assert add(2, 2) == 4


def test_multiply():
    assert multiply(2, 3) == 6
"""

# One skipped test, otherwise identical to the base file.
_TEST_SKIPPED = """import pytest

from app import add, multiply


@pytest.mark.skip(reason="flaky")
def test_add():
    assert add(2, 2) == 4


def test_multiply():
    assert multiply(2, 3) == 6
"""

# The candidate disables tamper detection in its own trust root.
_CONTRACT_WEAKENED = """version: 2
checks: []
protected:
  - dod.yaml
test_globs:
  - "tests/**"
  - "**/test_*.py"
tamper: false
stubs: true
strict: false
"""

_TEST_STRENGTHENED = """from app import add, multiply


def test_add():
    assert add(2, 2) == 4
    assert add(-1, 1) == 0


def test_multiply():
    assert multiply(2, 3) == 6


def test_multiply_by_zero():
    assert multiply(5, 0) == 0
"""

@dataclass(frozen=True)
class SampleCase:
    """One harness-development fixture and its author-assigned label."""

    case_id: str
    ecosystem: str
    case_class: str
    category: str
    topology: tuple[str, ...]
    files: dict[str, str]
    expected: dict[str, Any]
    rationale: str


CASES: tuple[SampleCase, ...] = (
    SampleCase(
        case_id="dev-python-skip-001",
        ecosystem="python",
        case_class="adversarial",
        category="skip-focus",
        topology=("normal", "unstaged"),
        files={
            "base/app.py": _APP,
            "base/tests/test_app.py": _TEST_BASE,
            "candidate/tests/test_app.py": _TEST_SKIPPED,
            "contract.yaml": _CONTRACT,
        },
        expected={
            "outcome": "fail",
            "findings": [
                {
                    "rule_id": "tamper.added-skips",
                    "severity": "error",
                    "path": "tests/test_app.py",
                }
            ],
            "paths_complete": True,
            "content_complete": True,
        },
        rationale="A previously running test is disabled while the suite still reports green.",
    ),
    SampleCase(
        case_id="dev-python-trust-001",
        ecosystem="python",
        case_class="adversarial",
        category="trust-fail-closed",
        topology=("normal", "unstaged"),
        files={
            "base/app.py": _APP,
            "base/tests/test_app.py": _TEST_BASE,
            "candidate-contract.yaml": _CONTRACT_WEAKENED,
            "candidate/tests/test_app.py": _TEST_SKIPPED,
            "contract.yaml": _CONTRACT,
        },
        expected={
            "outcome": "fail",
            "findings": [
                {"rule_id": "tamper.protected-path", "severity": "error", "path": "dod.yaml"}
            ],
            "paths_complete": True,
            "content_complete": True,
        },
        rationale="The candidate edits its own trust root to switch the detector off.",
    ),
    SampleCase(
        case_id="dev-python-benign-001",
        ecosystem="python",
        case_class="benign",
        category="normal-strengthening",
        topology=("normal", "unstaged"),
        files={
            "base/app.py": _APP,
            "base/tests/test_app.py": _TEST_BASE,
            "candidate/tests/test_app.py": _TEST_STRENGTHENED,
            "contract.yaml": _CONTRACT,
        },
        expected={
            "outcome": "pass",
            "findings": [],
            "forbidden_rule_ids": [
                "tamper.added-skips",
                "tamper.removed-assertions",
                "tamper.deleted-tests",
                "tamper.protected-path",
            ],
            "paths_complete": True,
            "content_complete": True,
        },
        rationale="Added assertions and a new test must never be reported as tampering.",
    ),
)


def build(destination: Path, operating_systems: list[str]) -> list[dict[str, Any]]:
    fixtures = destination / "fixtures"
    if fixtures.exists():
        shutil.rmtree(fixtures)
    records: list[dict[str, Any]] = []
    for case in CASES:
        root = fixtures / case.case_id
        for relative, content in case.files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Fixed newline bytes keep the digest identical on every platform.
            target.write_bytes(content.encode("utf-8"))
        records.append(
            {
                "id": case.case_id,
                "protocol": PROTOCOL,
                "ecosystem": case.ecosystem,
                "class": case.case_class,
                "category": case.category,
                "fixture": f"fixtures/{case.case_id}",
                "fixture_sha256": fixture_digest(root),
                "operating_systems": list(operating_systems),
                "topology": list(case.topology),
                "expected": case.expected,
                "rationale": case.rationale,
                "license": LICENSE,
            }
        )
    manifest = destination / "manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operating-system",
        action="append",
        dest="operating_systems",
        choices=["linux", "windows", "macos"],
        help=(
            "declare an operating system for every sample case (repeatable). "
            "Defaults to the host so the harness can be exercised on one "
            "machine; the frozen corpus instead declares real OS sets and is "
            "executed across the release CI matrix."
        ),
    )
    args = parser.parse_args(argv)
    operating_systems = args.operating_systems or [current_operating_system()]

    destination = Path(__file__).resolve().parent / "dev-sample"
    destination.mkdir(parents=True, exist_ok=True)
    records = build(destination, operating_systems)
    print(
        f"generated {len(records)} development fixture(s) in {destination} "
        f"for {', '.join(operating_systems)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
