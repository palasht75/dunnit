#!/usr/bin/env python3
"""Execute Dunnit benchmark fixtures blind and append raw JSONL results.

The protocol requires that "the execution runner receives fixture and contract
paths but not expected outcomes". This module enforces that structurally: every
manifest record is projected onto :data:`EXECUTION_KEYS` the moment it is
parsed, so labels, categories, and rationales never reach the code that decides
what to run or how long it took.

Timing uses Dunnit's single public instrumentation point, ``meta`` key
``scanner_duration``, which covers Git discovery through integrity-rule
evaluation and excludes declared proof-command runtime. Per the protocol only
the Linux reference runner reports durations, and it reports exactly five
measured runs after one unreported warm-up.

    python benchmarks/run.py benchmarks/manifest.jsonl \
      --fixtures benchmarks/fixtures \
      --output benchmarks/results/raw.jsonl

Exit codes: 0 when every selected case executed, 2 for an unusable manifest,
fixture, or environment. A refused case is never written as a result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL = "dunnit-benchmark-v1"
LINUX_MEASURED_RUNS = 5

# The only manifest fields this process may read. `expected`, `class`,
# `category`, and `rationale` are labels; loading them here would let the
# runner's behaviour depend on the answer it is meant to discover.
EXECUTION_KEYS = frozenset(
    {"id", "protocol", "fixture", "fixture_sha256", "operating_systems", "topology"}
)

# Topologies this runner can construct deterministically. Anything else is
# refused loudly rather than executed as an approximation, so partial coverage
# can never be published as if the declared topology had been exercised.
SUPPORTED_TOPOLOGIES = frozenset(
    {"normal", "committed", "staged", "unstaged", "untracked", "lf", "crlf", "detached-head"}
)

_ALERT_SEVERITIES = frozenset({"warning", "error"})


class BenchmarkExecutionError(Exception):
    """A case or environment could not be executed as declared."""


@dataclass(frozen=True)
class ExecutionCase:
    """A manifest record reduced to what execution legitimately needs."""

    case_id: str
    fixture: str
    fixture_sha256: str
    operating_systems: tuple[str, ...]
    topologies: tuple[str, ...]


def current_operating_system() -> str:
    system = platform.system()
    if system == "Linux":
        return "linux"
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    raise BenchmarkExecutionError(f"unsupported benchmark operating system: {system!r}")


def fixture_digest(root: Path) -> str:
    """Hash a fixture tree so a published digest pins its exact bytes.

    Paths are POSIX-normalized and sorted, and every file contributes its
    relative path, byte length, and content, so the digest is identical on
    every platform and cannot be altered by directory iteration order.
    """

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def load_execution_cases(manifest: Path) -> list[ExecutionCase]:
    """Parse a manifest into label-free execution records."""

    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkExecutionError(f"cannot read manifest {manifest}: {exc}") from exc

    cases: list[ExecutionCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise BenchmarkExecutionError(f"manifest line {line_number} is not JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise BenchmarkExecutionError(f"manifest line {line_number} is not an object")
        # Project onto execution fields before anything else reads the record.
        execution = {key: value for key, value in record.items() if key in EXECUTION_KEYS}
        missing = EXECUTION_KEYS - execution.keys() - {"topology"}
        if missing:
            raise BenchmarkExecutionError(
                f"manifest line {line_number} is missing {', '.join(sorted(missing))}"
            )
        if execution["protocol"] != PROTOCOL:
            raise BenchmarkExecutionError(f"manifest line {line_number} has a foreign protocol")
        case_id = execution["id"]
        if case_id in seen:
            raise BenchmarkExecutionError(f"duplicate case id {case_id!r}")
        seen.add(case_id)
        cases.append(
            ExecutionCase(
                case_id=case_id,
                fixture=execution["fixture"],
                fixture_sha256=execution["fixture_sha256"],
                operating_systems=tuple(execution["operating_systems"]),
                topologies=tuple(execution.get("topology") or ("normal",)),
            )
        )
    if not cases:
        raise BenchmarkExecutionError(f"manifest {manifest} contains no cases")
    return cases


def _git(repository: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BenchmarkExecutionError(
            f"git {' '.join(args)} failed in {repository}: {result.stderr.strip()}"
        )


def _copy_tree(source: Path, destination: Path, *, newline: bytes | None) -> list[str]:
    written: list[str] = []
    if not source.is_dir():
        return written
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        if newline is not None:
            data = data.replace(b"\r\n", b"\n")
            if newline != b"\n":
                data = data.replace(b"\n", newline)
        target.write_bytes(data)
        written.append(relative.as_posix())
    return written


def materialize(fixture: Path, workspace: Path, topologies: tuple[str, ...]) -> None:
    """Build a disposable Git repository for one measured run.

    ``base/`` is committed so the trusted policy has a committed trust root,
    then ``candidate/`` is applied as the candidate mutation in the state the
    declared topology requires.
    """

    unsupported = set(topologies) - SUPPORTED_TOPOLOGIES
    if unsupported:
        raise BenchmarkExecutionError(
            "this runner cannot construct topologies: " + ", ".join(sorted(unsupported))
        )

    newline = b"\r\n" if "crlf" in topologies else (b"\n" if "lf" in topologies else None)
    workspace.mkdir(parents=True, exist_ok=True)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "benchmark@dunnit.invalid")
    _git(workspace, "config", "user.name", "dunnit benchmark")
    _git(workspace, "config", "commit.gpgsign", "false")
    _git(workspace, "config", "core.autocrlf", "false")

    base = _copy_tree(fixture / "base", workspace, newline=newline)
    if not base:
        raise BenchmarkExecutionError(f"fixture {fixture} has no base/ snapshot")
    contract = fixture / "contract.yaml"
    if not contract.is_file():
        raise BenchmarkExecutionError(f"fixture {fixture} has no contract.yaml")
    (workspace / "dod.yaml").write_bytes(contract.read_bytes())
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "benchmark base snapshot")
    if "detached-head" in topologies:
        _git(workspace, "checkout", "-q", "--detach", "HEAD")

    deletions = fixture / "delete.txt"
    if deletions.is_file():
        for entry in deletions.read_text(encoding="utf-8").splitlines():
            name = entry.strip()
            if not name:
                continue
            target = workspace / name
            if target.is_file():
                target.unlink()

    candidate = _copy_tree(fixture / "candidate", workspace, newline=newline)
    # A candidate that rewrites its own trust root supplies the replacement
    # here rather than as a literal candidate/dod.yaml, so a checked-in fixture
    # never looks like a policy file to tools scanning this repository.
    candidate_contract = fixture / "candidate-contract.yaml"
    if candidate_contract.is_file():
        (workspace / "dod.yaml").write_bytes(candidate_contract.read_bytes())
        candidate.append("dod.yaml")
    if "staged" in topologies:
        _git(workspace, "add", "-A")
    elif "committed" in topologies:
        _git(workspace, "add", "-A")
        _git(workspace, "commit", "-qm", "benchmark candidate mutation")
    # "unstaged", "untracked", and "normal" leave the worktree as written.
    if not candidate and not deletions.is_file():
        raise BenchmarkExecutionError(f"fixture {fixture} has no candidate mutation")


def _findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in payload.get("evidence", []):
        severity = item.get("severity")
        if severity not in _ALERT_SEVERITIES:
            continue
        key = (item.get("rule_id", ""), severity, item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        findings.append({"rule_id": key[0], "severity": severity, "path": key[2]})
    return findings


def _identity_complete(meta: dict[str, Any]) -> bool:
    """Whether Dunnit published the Git identity metadata the protocol needs.

    An unborn repository legitimately has no HEAD commit, so it is complete
    when every digest is present; any other repository must also identify the
    commit it evaluated.
    """

    git = meta.get("git")
    if not isinstance(git, dict):
        return False
    digests = (
        "index_digest",
        "effective_config_digest",
        "configured_files_digest",
        "refs_digest",
        "grafts_digest",
    )
    if not all(isinstance(git.get(name), str) and git.get(name) for name in digests):
        return False
    if git.get("unborn"):
        return True
    return isinstance(git.get("head_sha"), str) and bool(git.get("head_sha"))


def _scan_completeness(meta: dict[str, Any]) -> tuple[bool, bool]:
    stages = meta.get("scan", {}).get("stages", [])
    if not stages:
        return False, False
    paths = all(bool(stage.get("paths_complete")) for stage in stages)
    content = all(bool(stage.get("content_complete")) for stage in stages)
    return paths, content


def execute_once(fixture: Path, case: ExecutionCase) -> dict[str, Any]:
    """Materialize a fresh worktree, verify it, and return one observation."""

    from dunnit.runner import verify

    with tempfile.TemporaryDirectory(prefix="dunnit-benchmark-") as directory:
        workspace = Path(directory) / "repository"
        materialize(fixture, workspace, case.topologies)
        verdict = verify(workspace / "dod.yaml", cwd=workspace)
        payload = verdict.to_dict()
        meta = payload.get("meta", {})
        paths_complete, content_complete = _scan_completeness(meta)
        return {
            "outcome": payload["outcome"],
            "findings": _findings(payload),
            "paths_complete": paths_complete,
            "content_complete": content_complete,
            "identity_complete": _identity_complete(meta),
            "changed_path_count": int(meta.get("files_changed", 0)),
            "inspectable_bytes": int(meta.get("inspectable_bytes", 0)),
            "scanner_duration": float(meta.get("scanner_duration", 0.0)),
        }


def run_case(fixtures_root: Path, case: ExecutionCase, operating_system: str) -> dict[str, Any]:
    fixture = fixtures_root / Path(case.fixture).relative_to("fixtures")
    if not fixture.is_dir():
        raise BenchmarkExecutionError(f"{case.case_id}: fixture directory {fixture} is missing")
    actual = fixture_digest(fixture)
    if actual != case.fixture_sha256:
        raise BenchmarkExecutionError(
            f"{case.case_id}: fixture digest {actual} does not match the frozen "
            f"{case.fixture_sha256}; a published fixture must never be edited in place"
        )

    # One unreported warm-up, then the measured runs. Correctness is read from
    # the warm-up so every reported duration measures identical work.
    reference = execute_once(fixture, case)
    durations: list[float] = []
    if operating_system == "linux":
        for _ in range(LINUX_MEASURED_RUNS):
            observation = execute_once(fixture, case)
            for field in ("outcome", "changed_path_count", "inspectable_bytes"):
                if observation[field] != reference[field]:
                    raise BenchmarkExecutionError(
                        f"{case.case_id}: {field} is not deterministic across runs; "
                        "quarantine the case rather than publishing a flaky result"
                    )
            durations.append(round(observation["scanner_duration"], 6))

    return {
        "case_id": case.case_id,
        "protocol": PROTOCOL,
        "operating_system": operating_system,
        "outcome": reference["outcome"],
        "findings": reference["findings"],
        "paths_complete": reference["paths_complete"],
        "content_complete": reference["content_complete"],
        "identity_complete": reference["identity_complete"],
        "infrastructure_error": reference["outcome"] == "error"
        and any(
            item["rule_id"].startswith("infrastructure.") for item in reference["findings"]
        ),
        "changed_path_count": reference["changed_path_count"],
        "inspectable_bytes": reference["inspectable_bytes"],
        "scanner_durations_seconds": durations,
    }


def environment_record() -> dict[str, Any]:
    """Capture the execution environment the protocol requires alongside results."""

    from dunnit import __version__ as dunnit_version

    git_version = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return {
        "protocol": PROTOCOL,
        "dunnit_version": dunnit_version,
        "operating_system": current_operating_system(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "git": git_version,
        "cpu_count": os.cpu_count(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    args = parser.parse_args(argv)

    try:
        operating_system = current_operating_system()
        cases = load_execution_cases(args.manifest)
        selected = [
            case
            for case in cases
            if operating_system in case.operating_systems
            and (not args.case_ids or case.case_id in set(args.case_ids))
        ]
        if not selected:
            raise BenchmarkExecutionError(
                f"no selected case declares {operating_system}"
            )
        records = [run_case(args.fixtures, case, operating_system) for case in selected]
    except BenchmarkExecutionError as exc:
        print(f"benchmark execution refused: {exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Results are append-only: a rerun adds observations, it never rewrites.
    with args.output.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    if args.environment:
        args.environment.parent.mkdir(parents=True, exist_ok=True)
        args.environment.write_text(
            json.dumps(environment_record(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"wrote {len(records)} result(s) for {operating_system} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
