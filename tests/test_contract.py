from pathlib import Path

import pytest

from dunnit.contract import ContractError, load_contract


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
