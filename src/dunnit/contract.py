"""Load and validate the definition-of-done contract (dod.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_TEST_GLOBS = ["tests/**", "test/**", "**/test_*.py", "**/*_test.py", "**/*.test.*", "**/*.spec.*"]


@dataclass
class CommandCheck:
    name: str
    run: str
    timeout: int = 600


@dataclass
class Contract:
    version: int = 1
    base: str | None = None  # git ref to diff against, e.g. "origin/main"
    checks: list[CommandCheck] = field(default_factory=list)
    test_globs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))
    tamper: bool = True
    stubs: bool = True


class ContractError(ValueError):
    pass


def load_contract(path: Path | str) -> Contract:
    path = Path(path)
    if not path.exists():
        raise ContractError(f"contract not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ContractError("dod.yaml must be a mapping")

    checks = []
    for i, raw in enumerate(data.get("checks", [])):
        if not isinstance(raw, dict) or "run" not in raw:
            raise ContractError(f"checks[{i}] needs a 'run' key")
        checks.append(
            CommandCheck(
                name=str(raw.get("name", f"check-{i}")),
                run=str(raw["run"]),
                timeout=int(raw.get("timeout", 600)),
            )
        )

    return Contract(
        version=int(data.get("version", 1)),
        base=data.get("base"),
        checks=checks,
        test_globs=list(data.get("test_globs", DEFAULT_TEST_GLOBS)),
        tamper=bool(data.get("tamper", True)),
        stubs=bool(data.get("stubs", True)),
    )
