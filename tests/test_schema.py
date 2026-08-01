from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest
import yaml

from dunnit.contract import ContractError, parse_contract


def _schema() -> dict[str, object]:
    resource = resources.files("dunnit").joinpath("dod-v2.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _benchmark_schema() -> dict[str, object]:
    path = Path(__file__).parents[1] / "benchmarks" / "case.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _benchmark_case(case_class: str = "adversarial") -> dict[str, object]:
    if case_class == "adversarial":
        category = "skip-focus"
        expected = {
            "outcome": "fail",
            "findings": [
                {
                    "rule_id": "tamper.skip-added",
                    "severity": "error",
                    "path": "tests/test_app.py",
                }
            ],
            "paths_complete": True,
            "content_complete": True,
        }
    else:
        category = "normal-strengthening"
        expected = {
            "outcome": "pass",
            "findings": [],
            "forbidden_rule_ids": ["tamper.skip-added"],
            "paths_complete": True,
            "content_complete": True,
        }
    return {
        "id": f"python-{case_class}-001",
        "protocol": "dunnit-benchmark-v1",
        "ecosystem": "python",
        "class": case_class,
        "category": category,
        "fixture": f"fixtures/python/{case_class}-001",
        "fixture_sha256": "a" * 64,
        "operating_systems": ["linux"],
        "topology": ["normal"],
        "expected": expected,
        "rationale": "A labeled synthetic benchmark case.",
        "license": "MIT",
    }


def test_v2_schema_is_a_packaged_resource():
    schema = _schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["version"]["const"] == 2  # type: ignore[index]
    assert schema["additionalProperties"] is False


def test_v2_schema_describes_runtime_strictness():
    schema = _schema()
    properties = schema["properties"]
    definitions = schema["$defs"]

    assert schema["required"] == ["version"]
    assert definitions["check"]["additionalProperties"] is False  # type: ignore[index]
    assert definitions["requirements"]["additionalProperties"] is False  # type: ignore[index]
    assert len(definitions["check"]["oneOf"]) == 2  # type: ignore[index]
    assert properties["checks"]["items"] == {"$ref": "#/$defs/check"}  # type: ignore[index]


VALID_CONTRACTS = [
    "version: 2\n",
    "version: 2.0\nchecks:\n  - {argv: [python], timeout: 600.0}\n",
    """\
version: 2
base: origin/main
checks:
  - name: unit-tests
    argv: [python, -m, pytest, -q]
    timeout: 30
    dir: packages/core
    env: {CI: "1", EMPTY: ""}
    writes: [.coverage, build/**]
protected: [dod.yaml, .github/**]
test_globs: [tests/**, "**/test_*.py"]
tamper: true
stubs: true
strict: false
require:
  changed: [src/**]
  non_empty_diff: true
""",
    "version: 2\nchecks:\n  - run: python -m pytest\n",
    "version: 2\nchecks:\n  - argv: [python]\n",
    (
        "version: 2\nchecks: []\nprotected: []\ntamper: false\nstubs: false\n"
        "require: {changed: [src/**]}\n"
    ),
    (
        "version: 2\nchecks: []\nprotected: [dod.yaml]\ntest_globs: []\n"
        "tamper: true\nstubs: false\n"
    ),
]


INVALID_CONTRACTS = [
    "version: true\n",
    "version: 2.5\n",
    "version: 2\nunknown: true\n",
    "version: 2\nchecks: {}\n",
    "version: 2\nchecks:\n  - name: missing-command\n",
    "version: 2\nchecks:\n  - {run: echo ok, argv: [echo, ok]}\n",
    "version: 2\nchecks:\n  - argv: []\n",
    "version: 2\nchecks:\n  - argv: [python, '']\n",
    "version: 2\nchecks:\n  - run: '   '\n",
    "version: 2\nchecks:\n  - {name: 'bad name', argv: [python]}\n",
    "version: 2\nchecks:\n  - {argv: [python], timeout: 0}\n",
    "version: 2\nchecks:\n  - {argv: [python], timeout: true}\n",
    "version: 2\nchecks:\n  - {argv: [python], timeout: 0.5}\n",
    "version: 2\nchecks:\n  - {argv: [python], dir: ../outside}\n",
    "version: 2\nchecks:\n  - {argv: [python], dir: /outside}\n",
    "version: 2\nchecks:\n  - {argv: [python], dir: C:/outside}\n",
    "version: 2\nchecks:\n  - {argv: [python], writes: [a/../outside]}\n",
    "version: 2\nchecks:\n  - {argv: [python], env: {CI: 1}}\n",
    "version: 2\nchecks:\n  - {argv: [python], env: {'': value}}\n",
    "version: 2\nchecks:\n  - {argv: [python], env: {'A=B': value}}\n",
    "version: 2\nbase: --output=/tmp/result\n",
    "version: 2\nbase: '   '\n",
    "version: 2\nprotected: [../dod.yaml]\n",
    "version: 2\nrequire: {unexpected: true}\n",
    "version: 2\nrequire: {non_empty_diff: 1}\n",
    "version: 2\nchecks: []\nprotected: []\ntamper: false\nstubs: false\n",
    "version: 2\nchecks: []\nprotected: []\ntest_globs: []\ntamper: true\nstubs: false\n",
    "version: 2\nchecks: []\nprotected: []\ntest_globs: []\nstubs: false\n",
]


@pytest.mark.parametrize("text", VALID_CONTRACTS)
def test_schema_accepts_every_runtime_valid_example(text: str):
    jsonschema = pytest.importorskip("jsonschema")
    instance = yaml.safe_load(text)

    parse_contract(text)
    jsonschema.Draft202012Validator(_schema()).validate(instance)


@pytest.mark.parametrize("text", INVALID_CONTRACTS)
def test_schema_rejects_every_runtime_invalid_example(text: str):
    jsonschema = pytest.importorskip("jsonschema")
    instance = yaml.safe_load(text)

    with pytest.raises(ContractError):
        parse_contract(text)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_schema()).validate(instance)


@pytest.mark.parametrize("text", ["{}\n", "version: 1\n"])
def test_v2_schema_excludes_legacy_contracts_that_runtime_still_reads(text: str):
    jsonschema = pytest.importorskip("jsonschema")

    with pytest.warns(DeprecationWarning):
        parse_contract(text)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_schema()).validate(yaml.safe_load(text))


def test_duplicate_check_names_remain_an_explicit_runtime_only_constraint():
    schema = _schema()
    duplicate = """\
version: 2
checks:
  - {name: tests, argv: [python]}
  - {name: tests, argv: [python]}
"""

    with pytest.raises(ContractError, match="unique"):
        parse_contract(duplicate)
    assert "cannot express uniqueness" in schema["properties"]["checks"]["$comment"]  # type: ignore[index]


@pytest.mark.parametrize(
    "path",
    sorted((Path(__file__).parents[1] / "examples").rglob("dod.yaml")),
    ids=lambda path: str(path.relative_to(Path(__file__).parents[1] / "examples")),
)
def test_every_ecosystem_example_matches_runtime_and_json_schema(path: Path):
    jsonschema = pytest.importorskip("jsonschema")
    text = path.read_text(encoding="utf-8")

    contract = parse_contract(text, source=str(path))
    jsonschema.Draft202012Validator(_schema()).validate(yaml.safe_load(text))

    assert contract.version == 2
    assert contract.checks
    assert all((check.argv is None) != (check.run is None) for check in contract.checks)
    if path.parent.name != "examples":
        assert all(check.argv is not None for check in contract.checks)


def test_benchmark_schema_is_valid_and_accepts_both_case_classes():
    jsonschema = pytest.importorskip("jsonschema")
    schema = _benchmark_schema()

    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(_benchmark_case("adversarial"))
    validator.validate(_benchmark_case("benign"))


@pytest.mark.parametrize(
    "fixture",
    [
        "fixtures/../outside",
        "fixtures/python/../../outside",
        "fixtures/python\\outside",
        "fixtures/python/./case",
        "fixtures/python/",
    ],
)
def test_benchmark_schema_rejects_uncontained_or_noncanonical_fixtures(fixture: str):
    jsonschema = pytest.importorskip("jsonschema")
    case = _benchmark_case()
    case["fixture"] = fixture

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_benchmark_schema()).validate(case)


def test_benchmark_schema_requires_nonempty_topology():
    jsonschema = pytest.importorskip("jsonschema")
    case = _benchmark_case()
    case["topology"] = []

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_benchmark_schema()).validate(case)


@pytest.mark.parametrize("missing", ["severity", "path"])
def test_benchmark_schema_requires_expected_finding_identity(missing: str):
    jsonschema = pytest.importorskip("jsonschema")
    case = _benchmark_case()
    finding = case["expected"]["findings"][0]  # type: ignore[index]
    del finding[missing]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_benchmark_schema()).validate(case)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("severity", "info"),
        ("path", "../outside"),
        ("path", "C:/outside"),
        ("path", "tests\\test_app.py"),
    ],
)
def test_benchmark_schema_rejects_invalid_expected_finding_values(field: str, value: str):
    jsonschema = pytest.importorskip("jsonschema")
    case = _benchmark_case()
    case["expected"]["findings"][0][field] = value  # type: ignore[index]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_benchmark_schema()).validate(case)


@pytest.mark.parametrize(
    ("case_class", "category", "outcome"),
    [
        ("adversarial", "trust-fail-closed", "pass_with_warnings"),
        ("adversarial", "normal-strengthening", "fail"),
        ("adversarial", "skip-focus", "pass"),
        ("benign", "skip-focus", "pass"),
        ("benign", "normal-strengthening", "pass_with_warnings"),
    ],
)
def test_benchmark_schema_enforces_class_specific_labels(
    case_class: str, category: str, outcome: str
):
    jsonschema = pytest.importorskip("jsonschema")
    case = _benchmark_case(case_class)
    case["category"] = category
    case["expected"]["outcome"] = outcome  # type: ignore[index]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_benchmark_schema()).validate(case)


def test_benchmark_schema_requires_benign_forbidden_rules_and_no_findings():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(_benchmark_schema())
    missing_forbidden = _benchmark_case("benign")
    del missing_forbidden["expected"]["forbidden_rule_ids"]  # type: ignore[index]
    unexpected_finding = _benchmark_case("benign")
    unexpected_finding["expected"]["findings"] = [  # type: ignore[index]
        {"rule_id": "tamper.skip-added", "severity": "error", "path": None}
    ]

    with pytest.raises(jsonschema.ValidationError):
        validator.validate(missing_forbidden)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(unexpected_finding)


@pytest.mark.parametrize("category", ["test-deselection", "command-integrity"])
def test_benchmark_schema_requires_failure_level_narrowing_labels(category: str):
    jsonschema = pytest.importorskip("jsonschema")
    case = _benchmark_case("adversarial")
    case["category"] = category
    case["expected"]["outcome"] = "pass_with_warnings"  # type: ignore[index]
    case["expected"]["findings"][0]["severity"] = "warning"  # type: ignore[index]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_benchmark_schema()).validate(case)
