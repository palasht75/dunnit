from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "run.py"
SPEC = importlib.util.spec_from_file_location("dunnit_benchmark_run", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _record(**overrides):
    record = {
        "id": "python-skip-001",
        "protocol": "dunnit-benchmark-v1",
        "ecosystem": "python",
        "class": "adversarial",
        "category": "skip-focus",
        "fixture": "fixtures/python-skip-001",
        "fixture_sha256": "a" * 64,
        "operating_systems": ["linux"],
        "topology": ["normal"],
        "expected": {
            "outcome": "fail",
            "findings": [],
            "paths_complete": True,
            "content_complete": True,
        },
        "rationale": "a label the runner must never read",
        "license": "MIT",
    }
    record.update(overrides)
    return record


def _manifest(tmp_path, *records):
    path = tmp_path / "manifest.jsonl"
    path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )
    return path


def _fixture(tmp_path: Path, candidate_path: str, data: bytes = b"x = 2\n") -> Path:
    fixture = tmp_path / "fixture"
    (fixture / "base").mkdir(parents=True)
    (fixture / "candidate" / Path(candidate_path).parent).mkdir(parents=True)
    (fixture / "base" / "app.py").write_bytes(b"x = 1\n")
    (fixture / "candidate" / candidate_path).write_bytes(data)
    (fixture / "contract.yaml").write_bytes(b"version: 2\nchecks:\n  - name: ok\n    argv: [git, status]\n")
    return fixture


def test_execution_cases_never_carry_labels(tmp_path):
    cases = runner.load_execution_cases(_manifest(tmp_path, _record()))

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "python-skip-001"
    assert case.topologies == ("normal",)
    # The blindness guarantee: no label reaches the execution record or the
    # set of fields the runner is even allowed to read.
    for label in ("expected", "category", "class", "rationale", "ecosystem"):
        assert not hasattr(case, label)
        assert label not in runner.EXECUTION_KEYS


def test_manifest_problems_are_refused(tmp_path):
    unparsable = tmp_path / "bad.jsonl"
    unparsable.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(runner.BenchmarkExecutionError, match="not JSON"):
        runner.load_execution_cases(unparsable)

    with pytest.raises(runner.BenchmarkExecutionError, match="foreign protocol"):
        runner.load_execution_cases(_manifest(tmp_path, _record(protocol="other-v1")))

    with pytest.raises(runner.BenchmarkExecutionError, match="duplicate case id"):
        runner.load_execution_cases(_manifest(tmp_path, _record(), _record()))

    with pytest.raises(runner.BenchmarkExecutionError, match="no cases"):
        runner.load_execution_cases(_manifest(tmp_path))

    incomplete = _record()
    del incomplete["fixture_sha256"]
    with pytest.raises(runner.BenchmarkExecutionError, match="fixture_sha256"):
        runner.load_execution_cases(_manifest(tmp_path, incomplete))


def test_fixture_digest_is_content_addressed_and_order_independent(tmp_path):
    first = tmp_path / "first"
    (first / "base").mkdir(parents=True)
    (first / "base" / "app.py").write_bytes(b"x = 1\n")
    (first / "contract.yaml").write_bytes(b"version: 2\n")

    second = tmp_path / "second"
    (second / "base").mkdir(parents=True)
    # Written in a different order with identical bytes.
    (second / "contract.yaml").write_bytes(b"version: 2\n")
    (second / "base" / "app.py").write_bytes(b"x = 1\n")

    assert runner.fixture_digest(first) == runner.fixture_digest(second)

    (second / "base" / "app.py").write_bytes(b"x = 2\n")
    assert runner.fixture_digest(first) != runner.fixture_digest(second)


def test_edited_fixture_is_refused_before_execution(tmp_path):
    fixture = tmp_path / "fixtures" / "python-skip-001"
    (fixture / "base").mkdir(parents=True)
    (fixture / "base" / "app.py").write_bytes(b"x = 1\n")
    case = runner.ExecutionCase(
        case_id="python-skip-001",
        fixture="fixtures/python-skip-001",
        fixture_sha256="b" * 64,
        operating_systems=("linux",),
        topologies=("normal",),
    )

    with pytest.raises(runner.BenchmarkExecutionError, match="never be edited in place"):
        runner.run_case(tmp_path / "fixtures", case, "linux")


def test_unsupported_topology_is_refused_rather_than_approximated(tmp_path):
    fixture = _fixture(tmp_path, "app.py")

    with pytest.raises(runner.BenchmarkExecutionError, match="cannot construct topologies"):
        runner.materialize(fixture, tmp_path / "workspace", ("imaginary-topology",))


@pytest.mark.parametrize(
    ("topology", "candidate_path", "data"),
    [
        ("spaces", "dir with space/test.py", b"x = 2\n"),
        ("unicode", "caf\u00e9/test.py", b"x = 2\n"),
        *([("tabs", "tab\tname/test.py", b"x = 2\n")] if sys.platform != "win32" else []),
        ("binary", "binary.bin", b"before\0after"),
        ("oversized", "large.py", None),
        ("monorepo", "packages/api/test.py", b"x = 2\n"),
    ],
)
def test_declarative_topologies_are_proved_by_fixture_content(
    tmp_path: Path, topology: str, candidate_path: str, data: bytes | None
) -> None:
    fixture = _fixture(tmp_path, candidate_path, b"x" * 1_000_001 if data is None else data)

    runner.materialize(fixture, tmp_path / "workspace", (topology,))


@pytest.mark.parametrize(
    "topology",
    ["unborn", "worktree", "shallow-sufficient", "shallow-missing-history"],
)
def test_repository_topologies_are_constructed(tmp_path: Path, topology: str) -> None:
    fixture = _fixture(tmp_path, "app.py")

    runner.materialize(fixture, tmp_path / "workspace", (topology,))


def test_many_untracked_topology_requires_more_than_one_thousand_paths(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, "untracked/0000.py")
    for index in range(1, 1001):
        (fixture / "candidate" / "untracked" / f"{index:04d}.py").write_text(
            f"value = {index}\n", encoding="utf-8"
        )

    runner.materialize(fixture, tmp_path / "workspace", ("many-untracked",))


def test_declared_topology_is_refused_when_fixture_does_not_prove_it(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, "app.py")

    with pytest.raises(runner.BenchmarkExecutionError, match="does not prove"):
        runner.materialize(fixture, tmp_path / "workspace", ("unicode",))


def test_only_alert_findings_are_recorded_and_deduplicated():
    payload = {
        "evidence": [
            {"rule_id": "scan.completeness", "severity": "info", "path": None},
            {"rule_id": "tamper.added-skips", "severity": "error", "path": "tests/t.py"},
            {"rule_id": "tamper.added-skips", "severity": "error", "path": "tests/t.py"},
            {"rule_id": "stubs.todo", "severity": "warning", "path": "app.py"},
        ]
    }

    findings = runner._findings(payload)

    assert findings == [
        {"rule_id": "tamper.added-skips", "severity": "error", "path": "tests/t.py"},
        {"rule_id": "stubs.todo", "severity": "warning", "path": "app.py"},
    ]


def test_identity_completeness_requires_published_git_metadata():
    digests = {
        "index_digest": "sha256:a",
        "effective_config_digest": "sha256:b",
        "configured_files_digest": "sha256:c",
        "refs_digest": "sha256:d",
        "grafts_digest": "sha256:e",
    }
    assert runner._identity_complete({"git": {**digests, "head_sha": "abc"}}) is True
    # An unborn repository legitimately has no HEAD commit.
    assert runner._identity_complete({"git": {**digests, "unborn": True}}) is True
    assert runner._identity_complete({"git": {**digests, "head_sha": None}}) is False
    assert runner._identity_complete({"git": {"head_sha": "abc"}}) is False
    assert runner._identity_complete({}) is False


def test_scan_completeness_requires_every_stage():
    complete = {
        "scan": {
            "stages": [
                {"paths_complete": True, "content_complete": True},
                {"paths_complete": True, "content_complete": True},
            ]
        }
    }
    assert runner._scan_completeness(complete) == (True, True)

    partial = {
        "scan": {
            "stages": [
                {"paths_complete": True, "content_complete": True},
                {"paths_complete": True, "content_complete": False},
            ]
        }
    }
    assert runner._scan_completeness(partial) == (True, False)
    assert runner._scan_completeness({}) == (False, False)
