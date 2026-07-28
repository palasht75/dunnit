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
