from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "aggregate.py"
SPEC = importlib.util.spec_from_file_location("dunnit_benchmark_aggregate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _case(
    case_id: str,
    *,
    case_class: str = "adversarial",
    category: str = "skip-focus",
    ecosystem: str = "python",
    operating_systems: list[str] | None = None,
    topology: list[str] | None = None,
) -> dict[str, Any]:
    if case_class == "benign":
        expected = {
            "outcome": "pass",
            "findings": [],
            "forbidden_rule_ids": ["tamper.unexpected"],
            "paths_complete": True,
            "content_complete": True,
        }
    else:
        error_category = category in {
            "trust-fail-closed",
            "test-deselection",
            "command-integrity",
        }
        expected = {
            "outcome": "fail" if error_category else "pass_with_warnings",
            "findings": [
                {
                    "rule_id": f"benchmark.{category}",
                    "severity": "error" if error_category else "warning",
                    "path": "tests/test_example.py",
                }
            ],
            "paths_complete": True,
            "content_complete": True,
        }
    return {
        "id": case_id,
        "protocol": benchmark.PROTOCOL,
        "ecosystem": ecosystem,
        "class": case_class,
        "category": category,
        "fixture": f"fixtures/{case_id}",
        "fixture_sha256": hashlib.sha256(case_id.encode()).hexdigest(),
        "operating_systems": operating_systems or ["linux"],
        "topology": topology or ["normal"],
        "expected": expected,
        "rationale": "Synthetic test fixture.",
        "license": "MIT",
    }


def _result(
    case: dict[str, Any],
    *,
    operating_system: str = "linux",
    outcome: str | None = None,
    findings: list[dict[str, Any]] | None = None,
    paths_complete: bool = True,
    content_complete: bool = True,
    identity_complete: bool = True,
    infrastructure_error: bool = False,
    changed_path_count: int = 1,
    inspectable_bytes: int = 100,
    durations: list[float] | None = None,
) -> dict[str, Any]:
    expected = case["expected"]
    return {
        "case_id": case["id"],
        "protocol": benchmark.PROTOCOL,
        "operating_system": operating_system,
        "outcome": outcome or expected["outcome"],
        "findings": expected["findings"] if findings is None else findings,
        "paths_complete": paths_complete,
        "content_complete": content_complete,
        "identity_complete": identity_complete,
        "infrastructure_error": infrastructure_error,
        "changed_path_count": changed_path_count,
        "inspectable_bytes": inspectable_bytes,
        "scanner_durations_seconds": [0.1] if durations is None else durations,
    }


def _aggregate(
    tmp_path: Path,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    allow_partial: bool = True,
) -> dict[str, Any]:
    manifest_path = tmp_path / "manifest.jsonl"
    results_path = tmp_path / "results.jsonl"
    _write_jsonl(manifest_path, cases)
    _write_jsonl(results_path, results)
    return benchmark.aggregate(manifest_path, results_path, allow_partial=allow_partial)


def test_partial_aggregate_reports_counts_intervals_and_integrity(tmp_path: Path) -> None:
    trust = _case("trust-case", category="trust-fail-closed")
    missed = _case("missed-skip")
    benign = _case(
        "benign-clean", case_class="benign", category="normal-strengthening"
    )
    noisy = _case("benign-noisy", case_class="benign", category="marker-context")
    incomplete = _case(
        "benign-incomplete", case_class="benign", category="unusual-path"
    )
    missed_result = _result(
        missed,
        outcome="error",
        findings=[
            *missed["expected"]["findings"],
            {"rule_id": "benchmark.infrastructure", "severity": "error", "path": None},
        ],
        infrastructure_error=True,
        durations=[2.5],
    )
    noisy_result = _result(
        noisy,
        outcome="pass_with_warnings",
        findings=[
            {"rule_id": "tamper.unexpected", "severity": "warning", "path": "src/app.py"}
        ],
    )
    report = _aggregate(
        tmp_path,
        [trust, missed, benign, noisy, incomplete],
        [
            _result(trust),
            missed_result,
            _result(benign),
            noisy_result,
            _result(incomplete, content_complete=False),
        ],
    )

    classification = report["classification"]
    assert classification["adversarial_sensitivity"]["numerator"] == 1
    assert classification["adversarial_sensitivity"]["denominator"] == 2
    assert classification["adversarial_sensitivity"]["rate"] == 0.5
    assert classification["missed_adversarial_case_ids"] == ["missed-skip"]
    assert classification["macro_sensitivity"] == {"category_count": 2, "rate": 0.5}
    assert classification["benign_false_alarm_rate"]["numerator"] == 1
    assert classification["benign_false_alarm_rate"]["denominator"] == 3
    assert classification["benign_false_alarm_case_ids"] == ["benign-noisy"]
    assert classification["trust_root_detection"]["numerator"] == 1
    assert classification["trust_root_detection"]["denominator"] == 1
    assert classification["synthetic_alert_precision"]["numerator"] == 1
    assert classification["synthetic_alert_precision"]["denominator"] == 3
    assert classification["benign_false_alarm_rate"]["wilson_95"]["confidence"] == 0.95
    assert report["integrity"]["infrastructure_error_cases"] == 1
    assert report["integrity"]["infrastructure_error_case_ids"] == ["missed-skip"]
    assert report["integrity"]["infrastructure_error_executions"] == 1
    assert report["integrity"]["incomplete_pass_cases"] == 1
    assert report["integrity"]["incomplete_pass_case_ids"] == ["benign-incomplete"]
    assert report["integrity"]["incomplete_pass_executions"] == 1
    assert report["performance"]["p95_seconds"] == 2.5
    assert report["performance"]["by_changed_path_count"]["1"]["measurements"] == 5
    assert report["performance"]["by_inspectable_bytes"]["100"]["p95_seconds"] == 2.5
    assert report["gates"]["status"] == "not_evaluated"
    assert all(value is None for key, value in report["gates"].items() if key != "status")


def test_identity_incomplete_success_is_counted_as_an_incomplete_pass(tmp_path: Path) -> None:
    case = _case("identity-incomplete", case_class="benign", category="unusual-path")

    report = _aggregate(
        tmp_path,
        [case],
        [_result(case, identity_complete=False)],
    )

    assert report["integrity"]["incomplete_pass_executions"] == 1
    assert report["integrity"]["incomplete_pass_case_ids"] == ["identity-incomplete"]


def test_wilson_interval_handles_boundaries_and_undefined_denominator() -> None:
    zero = benchmark.wilson_proportion(0, 10)
    assert zero["rate"] == 0.0
    assert zero["wilson_95"]["lower"] == 0.0
    assert zero["wilson_95"]["upper"] == pytest.approx(0.2775328)

    full = benchmark.wilson_proportion(10, 10)
    assert full["rate"] == 1.0
    assert full["wilson_95"]["lower"] == pytest.approx(0.7224672)
    assert full["wilson_95"]["upper"] == 1.0

    assert benchmark.wilson_proportion(0, 0) == {
        "numerator": 0,
        "denominator": 0,
        "rate": None,
        "wilson_95": None,
    }


def test_exact_rule_severity_and_path_are_required_for_detection(tmp_path: Path) -> None:
    case = _case("exact-finding")
    result = _result(
        case,
        findings=[
            {
                "rule_id": "benchmark.skip-focus",
                "severity": "warning",
                "path": "tests/a_different_test.py",
            }
        ],
    )
    report = _aggregate(tmp_path, [case], [result])

    assert report["classification"]["adversarial_sensitivity"]["numerator"] == 0
    assert report["classification"]["adversarial_sensitivity"]["denominator"] == 1


def test_all_declared_operating_system_runs_must_detect_the_case(tmp_path: Path) -> None:
    case = _case("portable-case", operating_systems=["linux", "windows"])
    windows = _result(
        case,
        operating_system="windows",
        outcome="pass",
        findings=[],
    )
    report = _aggregate(tmp_path, [case], [_result(case), windows])

    assert report["classification"]["adversarial_sensitivity"]["numerator"] == 0
    assert report["corpus"]["execution_count"] == 2


def test_default_mode_rejects_a_partial_corpus(tmp_path: Path) -> None:
    case = _case("partial-case")
    with pytest.raises(benchmark.BenchmarkValidationError, match="frozen 300-case corpus"):
        _aggregate(tmp_path, [case], [_result(case)], allow_partial=False)


def test_duplicate_manifest_keys_are_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    results = tmp_path / "results.jsonl"
    record = json.dumps(_case("duplicate-key"))
    manifest.write_text(record[:-1] + ', "id": "second-id"}\n', encoding="utf-8")
    _write_jsonl(results, [_result(_case("duplicate-key"))])

    with pytest.raises(benchmark.BenchmarkValidationError, match="duplicate JSON key 'id'"):
        benchmark.aggregate(manifest, results, allow_partial=True)


def test_duplicate_manifest_ids_and_fixtures_are_rejected(tmp_path: Path) -> None:
    first = _case("duplicate-case")
    duplicate_id = _case("duplicate-case")
    with pytest.raises(benchmark.BenchmarkValidationError, match="duplicate manifest case ID"):
        _aggregate(tmp_path, [first, duplicate_id], [_result(first)])

    second = _case("second-case")
    second["fixture"] = first["fixture"]
    with pytest.raises(benchmark.BenchmarkValidationError, match="fixture .* is reused"):
        _aggregate(tmp_path, [first, second], [_result(first), _result(second)])


def test_duplicate_or_mismatched_results_are_rejected(tmp_path: Path) -> None:
    case = _case("result-case")
    with pytest.raises(benchmark.BenchmarkValidationError, match="duplicate result for case/OS"):
        _aggregate(tmp_path, [case], [_result(case), _result(case)])

    unknown = _case("unknown-case")
    with pytest.raises(benchmark.BenchmarkValidationError, match="unknown case"):
        _aggregate(tmp_path, [case], [_result(unknown)])

    multi_os = _case("missing-os-case", operating_systems=["linux", "windows"])
    with pytest.raises(benchmark.BenchmarkValidationError, match="missing results:.*windows"):
        _aggregate(tmp_path, [multi_os], [_result(multi_os)])


@pytest.mark.parametrize(
    ("update", "match"),
    [
        ({"unknown": True}, "unknown keys"),
        ({"outcome": "pass", "findings": [{"rule_id": "x", "severity": "error", "path": None}]}, "pass outcome"),
        ({"outcome": "fail", "findings": []}, "fail requires an error finding"),
        ({"outcome": "pass", "findings": [], "infrastructure_error": True}, "requires outcome='error'"),
        ({"scanner_durations_seconds": [-0.1]}, "finite non-negative"),
        ({"changed_path_count": -1}, "non-negative integer"),
        ({"inspectable_bytes": 1.5}, "non-negative integer"),
    ],
)
def test_malformed_result_records_fail_closed(
    tmp_path: Path, update: dict[str, Any], match: str
) -> None:
    case = _case("malformed-result")
    result = _result(case)
    result.update(update)
    with pytest.raises(benchmark.BenchmarkValidationError, match=match):
        _aggregate(tmp_path, [case], [result])


def test_nonfinite_numbers_are_rejected_during_json_decoding(tmp_path: Path) -> None:
    case = _case("nan-duration")
    manifest = tmp_path / "manifest.jsonl"
    results = tmp_path / "results.jsonl"
    _write_jsonl(manifest, [case])
    rendered = json.dumps(_result(case)).replace("[0.1]", "[NaN]")
    results.write_text(rendered + "\n", encoding="utf-8")

    with pytest.raises(benchmark.BenchmarkValidationError, match="non-finite JSON number"):
        benchmark.aggregate(manifest, results, allow_partial=True)


def test_cli_output_is_reproducible_and_labels_partial_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _case("cli-case")
    manifest = tmp_path / "manifest.jsonl"
    results = tmp_path / "results.jsonl"
    output = tmp_path / "aggregate.json"
    _write_jsonl(manifest, [case])
    _write_jsonl(results, [_result(case)])

    args = [str(manifest), str(results), "--allow-partial", "--output", str(output)]
    assert benchmark.main(args) == 0
    first = output.read_bytes()
    assert benchmark.main(args) == 0
    assert output.read_bytes() == first
    assert json.loads(first)["gates"]["status"] == "not_evaluated"
    assert capsys.readouterr().out == ""


def _frozen_corpus() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    variants = sorted(benchmark.TOPOLOGIES - {"normal"})
    index = 0
    for case_class, table, categories in (
        ("adversarial", benchmark._ADVERSARIAL_COUNTS, benchmark.ADVERSARIAL_CATEGORIES),
        ("benign", benchmark._BENIGN_COUNTS, benchmark.BENIGN_CATEGORIES),
    ):
        for ecosystem, counts in table.items():
            for category, count in zip(categories, counts):
                for _ in range(count):
                    case_id = f"{ecosystem}-{case_class[0]}-{category}-{index:03d}"
                    operating_systems = (
                        ["linux", "windows", "macos"]
                        if not any(
                            item["ecosystem"] == ecosystem
                            and set(item["operating_systems"]) == benchmark.OPERATING_SYSTEMS
                            for item in cases
                        )
                        else ["linux"]
                    )
                    topology = [variants[index]] if index < len(variants) else ["normal"]
                    case = _case(
                        case_id,
                        case_class=case_class,
                        category=category,
                        ecosystem=ecosystem,
                        operating_systems=operating_systems,
                        topology=topology,
                    )
                    if case_class == "adversarial":
                        case["expected"]["outcome"] = "fail"
                        case["expected"]["findings"][0]["severity"] = "error"
                    cases.append(case)
                    index += 1
    results: list[dict[str, Any]] = []
    for case in cases:
        for os_index, operating_system in enumerate(case["operating_systems"]):
            results.append(
                _result(
                    case,
                    operating_system=operating_system,
                    durations=[0.1] * 5 if os_index == 0 else [],
                )
            )
    return cases, results


def test_complete_frozen_corpus_evaluates_and_passes_all_gates(tmp_path: Path) -> None:
    cases, results = _frozen_corpus()
    report = _aggregate(tmp_path, cases, results, allow_partial=False)

    assert report["corpus"]["complete"] is True
    assert report["corpus"]["case_count"] == 300
    assert report["classification"]["adversarial_sensitivity"]["numerator"] == 200
    assert report["classification"]["benign_false_alarm_rate"]["numerator"] == 0
    assert report["classification"]["trust_root_detection"]["numerator"] == 40
    assert report["classification"]["macro_sensitivity"]["rate"] == 1.0
    assert report["performance"]["scanner_measurements"] == 1500
    assert report["performance"]["p95_seconds"] == 0.1
    assert report["gates"]["status"] == "pass"
    assert all(value is True for key, value in report["gates"].items() if key != "status")


def test_case_schema_exposes_all_protocol_topology_variants() -> None:
    schema = json.loads((ROOT / "benchmarks" / "case.schema.json").read_text(encoding="utf-8"))
    values = set(schema["properties"]["topology"]["items"]["enum"])
    assert values == benchmark.TOPOLOGIES
