"""Load and validate the definition-of-done contract (dod.yaml).

Validation is strict: unknown keys are contract errors. A typo like
``protectd:`` silently disabling protection would defeat the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_TEST_GLOBS = [
    "tests/**", "test/**", "__tests__/**", "spec/**",
    "**/test_*.py", "**/*_test.py",
    "**/*.test.*", "**/*.spec.*",
    "**/*_test.go", "**/*Test.java", "**/*Test.kt", "**/*_spec.rb",
]
# The contract itself is protected by default: an agent editing dod.yaml to
# weaken its own definition of done is the most obvious meta-hack.
DEFAULT_PROTECTED = ["dod.yaml"]

_TOP_KEYS = {
    "version", "base", "checks", "test_globs", "protected",
    "tamper", "stubs", "strict", "require",
}
_CHECK_KEYS = {"name", "run", "timeout", "dir", "env"}
_REQUIRE_KEYS = {"changed", "non_empty_diff"}


@dataclass
class CommandCheck:
    name: str
    run: str
    timeout: int = 600
    dir: str | None = None  # working directory relative to the repo root
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Requirements:
    """Positive requirements on the diff: the work must actually be present."""

    changed: list[str] = field(default_factory=list)  # each glob must match >=1 changed file
    non_empty_diff: bool = False


@dataclass
class Contract:
    version: int = 1
    base: str | None = None  # git ref to diff against, e.g. "origin/main"
    checks: list[CommandCheck] = field(default_factory=list)
    test_globs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))
    protected: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED))
    tamper: bool = True
    stubs: bool = True
    strict: bool = False  # promote warnings to failures
    require: Requirements = field(default_factory=Requirements)
    source: str | None = None  # path this contract was loaded from, if any


class ContractError(ValueError):
    pass


def _str_list(value, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ContractError(f"'{key}' must be a list of strings")
    return list(value)


def _reject_unknown(found, allowed: set, where: str) -> None:
    unknown = set(found) - allowed
    if unknown:
        raise ContractError(
            f"unknown key(s) in {where}: {', '.join(sorted(unknown))}"
            f" (valid: {', '.join(sorted(allowed))})"
        )


def load_contract(path: Path | str) -> Contract:
    path = Path(path)
    if not path.exists():
        raise ContractError(f"contract not found: {path} (run `dunnit init` to create one)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ContractError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError("dod.yaml must be a mapping")
    _reject_unknown(data, _TOP_KEYS, path.name)

    raw_checks = data.get("checks") or []
    if not isinstance(raw_checks, list):
        raise ContractError("'checks' must be a list")
    checks = []
    for i, raw in enumerate(raw_checks):
        if not isinstance(raw, dict) or "run" not in raw:
            raise ContractError(f"checks[{i}] needs a 'run' key")
        _reject_unknown(raw, _CHECK_KEYS, f"checks[{i}]")
        env = raw.get("env") or {}
        if not isinstance(env, dict):
            raise ContractError(f"checks[{i}]: 'env' must be a mapping")
        checks.append(
            CommandCheck(
                name=str(raw.get("name", f"check-{i}")),
                run=str(raw["run"]),
                timeout=int(raw.get("timeout", 600)),
                dir=str(raw["dir"]) if raw.get("dir") else None,
                env={str(k): str(v) for k, v in env.items()},
            )
        )

    raw_require = data.get("require") or {}
    if not isinstance(raw_require, dict):
        raise ContractError("'require' must be a mapping")
    _reject_unknown(raw_require, _REQUIRE_KEYS, "require")
    require = Requirements(
        changed=_str_list(raw_require.get("changed") or [], "require.changed"),
        non_empty_diff=bool(raw_require.get("non_empty_diff", False)),
    )

    protected = data.get("protected", DEFAULT_PROTECTED)

    return Contract(
        version=int(data.get("version", 1)),
        base=str(data["base"]) if data.get("base") else None,
        checks=checks,
        test_globs=_str_list(data.get("test_globs", DEFAULT_TEST_GLOBS), "test_globs"),
        protected=_str_list(protected, "protected") if protected else [],
        tamper=bool(data.get("tamper", True)),
        stubs=bool(data.get("stubs", True)),
        strict=bool(data.get("strict", False)),
        require=require,
        source=str(path),
    )
