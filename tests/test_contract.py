from pathlib import Path

import pytest

from dunnit.contract import (
    CommandCheck,
    ContractError,
    _repo_relative,
    load_contract,
    parse_contract,
)


def test_load_contract(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text("version: 1\nchecks:\n  - name: t\n    run: echo hi\n")
    c = load_contract(p)
    assert c.version == 1
    assert c.checks[0].name == "t"
    assert c.tamper and c.stubs


def test_missing_contract(tmp_path: Path):
    with pytest.raises(ContractError):
        load_contract(tmp_path / "nope.yaml")


def test_check_needs_run(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text("checks:\n  - name: broken\n")
    with pytest.raises(ContractError):
        load_contract(p)


def test_unknown_top_level_key_rejected(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text("version: 1\nprotectd:\n  - dod.yaml\n")
    with pytest.raises(ContractError, match="protectd"):
        load_contract(p)


def test_unknown_check_key_rejected(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text("checks:\n  - name: t\n    run: echo hi\n    timout: 5\n")
    with pytest.raises(ContractError, match="timout"):
        load_contract(p)


def test_invalid_yaml_is_contract_error(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text("checks: [unclosed\n")
    with pytest.raises(ContractError, match="invalid YAML"):
        load_contract(p)


def test_check_env_and_dir_parsed(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text(
        "checks:\n  - name: t\n    run: make test\n    dir: backend\n"
        "    env:\n      CI: '1'\n"
    )
    c = load_contract(p)
    assert c.checks[0].dir == "backend"
    assert c.checks[0].env == {"CI": "1"}


def test_require_and_strict_parsed(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text(
        "version: 1\nstrict: true\nrequire:\n  changed:\n    - tests/**\n  non_empty_diff: true\n"
    )
    c = load_contract(p)
    assert c.strict is True
    assert c.require.changed == ["tests/**"]
    assert c.require.non_empty_diff is True


def test_missing_contract_mentions_init(tmp_path: Path):
    with pytest.raises(ContractError, match="dunnit init"):
        load_contract(tmp_path / "nope.yaml")


def test_source_recorded(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text("version: 1\n")
    assert load_contract(p).source == str(p)


def test_v2_argv_and_writes_are_parsed(tmp_path: Path):
    p = tmp_path / "dod.yaml"
    p.write_text(
        "version: 2\n"
        "checks:\n"
        "  - name: unit-tests\n"
        "    argv: [python, -m, pytest, -q]\n"
        "    timeout: 30\n"
        "    writes: [.coverage, build/**]\n"
    )
    check = load_contract(p).checks[0]
    assert check.run is None
    assert check.argv == ["python", "-m", "pytest", "-q"]
    assert check.writes == [".coverage", "build/**"]
    assert check.uses_shell is False


def test_parse_contract_records_trusted_source():
    contract = parse_contract("version: 2\n", source="abc123:dod.yaml")
    assert contract.source == "abc123:dod.yaml"
    assert contract.legacy is False


@pytest.mark.parametrize(
    "text, message",
    [
        ("version: true\n", "version"),
        ("version: 3\n", "unsupported"),
        ("version: 2\ntamper: 1\n", "tamper"),
        ("version: 2\nstubs: yes please\n", "stubs"),
        ("version: 2\nstrict: 0\n", "strict"),
        (
            "version: 2\nchecks:\n  - name: t\n    argv: [python]\n    timeout: 0\n",
            "positive integer",
        ),
        (
            "version: 2\nchecks:\n  - name: t\n    argv: [python]\n    timeout: true\n",
            "positive integer",
        ),
        ("version: 2\nrequire:\n  non_empty_diff: 1\n", "boolean"),
    ],
)
def test_scalar_types_are_not_coerced(text: str, message: str):
    with pytest.raises(ContractError, match=message):
        parse_contract(text)


@pytest.mark.parametrize(
    "text",
    [
        "version: 2\nversion: 2\n",
        "version: 2\nchecks:\n  - name: t\n    name: again\n    argv: [python]\n",
        "version: 2\nchecks:\n  - name: t\n    argv: [python]\n    env:\n      CI: '1'\n      CI: '2'\n",
    ],
)
def test_duplicate_yaml_keys_are_rejected(text: str):
    with pytest.raises(ContractError, match="duplicate key"):
        parse_contract(text)


@pytest.mark.parametrize(
    "command",
    [
        "",
        "    run: echo ok\n    argv: [echo, ok]",
    ],
)
def test_check_requires_exactly_one_command(command: str):
    text = "version: 2\nchecks:\n  - name: t\n"
    if command:
        text += command + "\n"
    with pytest.raises(ContractError, match="exactly one"):
        parse_contract(text)


@pytest.mark.parametrize("argv", ["[]", "[python, '']", "[python, 1]"])
def test_argv_requires_non_empty_string_elements(argv: str):
    with pytest.raises(ContractError, match="non-empty list of strings"):
        parse_contract(f"version: 2\nchecks:\n  - name: t\n    argv: {argv}\n")


def test_v1_run_remains_supported_but_v2_fields_do_not():
    assert parse_contract("version: 1\nchecks:\n  - run: echo ok\n").checks[0].run == "echo ok"
    with pytest.raises(ContractError, match="version 2"):
        parse_contract("version: 1\nchecks:\n  - argv: [echo, ok]\n")


@pytest.mark.parametrize("name", ["has spaces", "bad:name", "-starts-with-dash", ""])
def test_check_name_must_be_a_slug(name: str):
    with pytest.raises(ContractError, match="slug"):
        parse_contract(
            f"version: 2\nchecks:\n  - name: {name!r}\n    argv: [python]\n"
        )


def test_check_names_must_be_unique():
    with pytest.raises(ContractError, match="unique"):
        parse_contract(
            "version: 2\nchecks:\n"
            "  - {name: tests, argv: [python]}\n"
            "  - {name: tests, argv: [python]}\n"
        )


@pytest.mark.parametrize(
    "field, value",
    [
        ("dir", "../outside"),
        ("dir", "/tmp"),
        ("dir", "C:\\\\outside"),
        ("writes", "[../outside/**]"),
        ("writes", "['C:\\\\outside\\\\**']"),
    ],
)
def test_command_paths_must_stay_in_repo(field: str, value: str):
    with pytest.raises(ContractError, match="repository root"):
        parse_contract(
            "version: 2\nchecks:\n  - name: t\n    argv: [python]\n"
            f"    {field}: {value}\n"
        )


def test_environment_values_are_exact_strings():
    with pytest.raises(ContractError, match="environment values"):
        parse_contract(
            "version: 2\nchecks:\n  - name: t\n    argv: [python]\n    env: {CI: 1}\n"
        )


def test_true_no_op_contract_is_rejected():
    with pytest.raises(ContractError, match="no-op"):
        parse_contract(
            "version: 2\nchecks: []\nprotected: []\ntamper: false\nstubs: false\n"
        )

    with pytest.raises(ContractError, match="no-op"):
        parse_contract(
            "version: 2\nchecks: []\nprotected: []\ntest_globs: []\n"
            "tamper: true\nstubs: false\n"
        )


def test_programmatic_command_property_is_explicit():
    assert CommandCheck("argv", argv=["python"]).command == ["python"]
    assert CommandCheck("shell", run="echo ok").command == "echo ok"
    with pytest.raises(ValueError, match="exactly one"):
        _ = CommandCheck("missing").command
    with pytest.raises(ValueError, match="exactly one"):
        _ = CommandCheck("ambiguous", run="echo ok", argv=["echo", "ok"]).command


@pytest.mark.parametrize(
    "text, message",
    [
        ("[]\n", "must be a mapping"),
        ("version: 2\nchecks: {}\n", "checks.*list"),
        ("version: 2\nrequire: []\n", "require.*mapping"),
        ("version: 2\nrequire: {unknown: true}\n", "unknown"),
        ("version: 2\nprotected: dod.yaml\n", "list of strings"),
        ("version: 2\nprotected: ['']\n", "non-empty string"),
        ("version: 2\n1: value\n", "must be strings"),
        ("version: 2\nbase: 1\n", "base.*string"),
        ("version: 2\nbase: --cached\n", "option-like"),
        ("version: 2\nchecks:\n  - run: 1\n", "run.*string"),
        ("version: 2\nchecks:\n  - argv: [python]\n    dir: 1\n", "dir.*string"),
        ("version: 2\nchecks:\n  - argv: [python]\n    env: []\n", "env.*mapping"),
        ("version: 2\nchecks:\n  - argv: [python]\n    env: {'BAD=KEY': ok}\n", "names"),
    ],
)
def test_remaining_invalid_contract_shapes_fail_closed(text, message):
    with pytest.raises(ContractError, match=message):
        parse_contract(text)


def test_v1_writes_and_unhashable_yaml_keys_are_rejected():
    with pytest.raises(ContractError, match="version 2"):
        parse_contract("version: 1\nchecks:\n  - run: echo ok\n    writes: [out]\n")
    with pytest.raises(ContractError, match="unhashable mapping key"):
        parse_contract("? [one, two]\n: value\n")


def test_empty_document_uses_safe_defaults_and_nul_paths_are_rejected():
    assert parse_contract("").version == 1
    with pytest.raises(ContractError, match="NUL"):
        _repo_relative("bad\x00path", "path")


def test_existing_unreadable_contract_is_an_error(tmp_path):
    with pytest.raises(ContractError, match="could not read contract"):
        load_contract(tmp_path)


def test_excessively_nested_yaml_is_a_contract_error_not_a_runtime_crash():
    nested = "[" * 600 + "'dod.yaml'" + "]" * 600
    with pytest.raises(ContractError, match="invalid YAML"):
        parse_contract(f"version: 2\nprotected: {nested}\n")
