"""Load and validate the definition-of-done contract (``dod.yaml``).

Contracts are part of Dunnit's trust boundary.  Validation is deliberately
strict: strings and booleans are never coerced, duplicate and unknown keys are
errors, and a contract which enables no checks is rejected rather than silently
succeeding. JSON Schema mathematical integers such as ``600.0`` are
canonicalized to Python integers so runtime and editor validation agree.
"""

from __future__ import annotations

import math
import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

DEFAULT_TEST_GLOBS = [
    "tests/**", "test/**", "__tests__/**", "spec/**",
    "**/test_*.py", "**/*_test.py",
    "**/*.test.*", "**/*.spec.*",
    "**/*_test.go", "**/*Test.java", "**/*Test.kt", "**/*_spec.rb",
]
# The contract itself is protected by default: an agent editing dod.yaml to
# weaken its own definition of done is the most obvious meta-hack.
DEFAULT_PROTECTED = ["dod.yaml"]

SUPPORTED_VERSIONS = frozenset({1, 2})

_TOP_KEYS = {
    "version", "base", "checks", "test_globs", "protected",
    "tamper", "stubs", "strict", "require",
}
_CHECK_KEYS = {"name", "run", "argv", "timeout", "dir", "env", "writes"}
_REQUIRE_KEYS = {"changed", "non_empty_diff"}
_CHECK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class CommandCheck:
    """A proof command declared by a contract.

    ``run`` remains the second field for source compatibility with v1 callers.
    V2 contracts may instead provide ``argv`` for shell-free execution.
    """

    name: str
    run: str | None = None
    timeout: int = 600
    dir: str | None = None  # working directory relative to the repo root
    env: dict[str, str] = field(default_factory=dict)
    argv: list[str] | None = None
    writes: list[str] = field(default_factory=list)

    @property
    def uses_shell(self) -> bool:
        return self.run is not None

    @property
    def command(self) -> str | list[str]:
        """Return the value to pass to :class:`subprocess.Popen`."""

        if (self.run is None) == (self.argv is None):
            raise ValueError("command check must define exactly one of 'run' or 'argv'")
        if self.argv is not None:
            return list(self.argv)
        assert self.run is not None
        return self.run


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
    source: str | None = None  # path/ref this contract was loaded from, if any

    @property
    def legacy(self) -> bool:
        """Whether this contract uses the deprecated v1 schema."""

        return self.version == 1


class ContractError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader which refuses duplicate mapping keys at every depth."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _str_list(value: Any, key: str, *, paths: bool = False) -> list[str]:
    if type(value) is not list or not all(type(item) is str for item in value):
        raise ContractError(f"'{key}' must be a list of strings")
    result = list(value)
    for i, item in enumerate(result):
        if not item or "\x00" in item:
            raise ContractError(f"'{key}[{i}]' must be a non-empty string without NUL bytes")
        if paths:
            _repo_relative(item, f"{key}[{i}]")
    return result


def _mapping(value: Any, key: str) -> dict[Any, Any]:
    if type(value) is not dict:
        raise ContractError(f"'{key}' must be a mapping")
    return value


def _bool(value: Any, key: str) -> bool:
    if type(value) is not bool:
        raise ContractError(f"'{key}' must be a boolean")
    return value


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    """Validate JSON Schema integer semantics while explicitly rejecting bool."""

    if type(value) is int:
        parsed = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    else:
        qualifier = "positive " if positive else ""
        raise ContractError(f"{label} must be a {qualifier}integer")
    if positive and parsed <= 0:
        raise ContractError(f"{label} must be a positive integer")
    return parsed


def _reject_unknown(found: dict[Any, Any], allowed: set[str], where: str) -> None:
    non_strings = [key for key in found if type(key) is not str]
    if non_strings:
        rendered = ", ".join(repr(key) for key in non_strings)
        raise ContractError(f"key(s) in {where} must be strings: {rendered}")
    unknown = set(found) - allowed
    if unknown:
        raise ContractError(
            f"unknown key(s) in {where}: {', '.join(sorted(unknown))}"
            f" (valid: {', '.join(sorted(allowed))})"
        )


def _repo_relative(value: str, key: str) -> str:
    """Validate a repo-relative directory/path glob on every host OS.

    Both POSIX and Windows absolute-path syntax are checked so that a contract
    authored on one platform cannot escape the repository on another.  Actual
    symlink containment is checked by the command runner once a repo root is
    available.
    """

    if "\x00" in value:
        raise ContractError(f"'{key}' must not contain NUL bytes")
    posix_value = value.replace("\\", "/")
    posix = PurePosixPath(posix_value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ContractError(f"'{key}' must be relative to the repository root")
    if ".." in posix.parts:
        raise ContractError(f"'{key}' must stay within the repository root")
    return value


def _optional_base(data: dict[str, Any]) -> str | None:
    if "base" not in data:
        return None
    base = data["base"]
    if type(base) is not str or not base.strip() or "\x00" in base:
        raise ContractError("'base' must be a non-empty string")
    if base.startswith("-"):
        raise ContractError("'base' must not be an option-like ref")
    return base


def _parse_check(raw: Any, index: int, version: int) -> CommandCheck:
    where = f"checks[{index}]"
    raw = _mapping(raw, where)
    _reject_unknown(raw, _CHECK_KEYS, where)

    has_run = "run" in raw
    has_argv = "argv" in raw
    if has_run == has_argv:
        raise ContractError(f"{where} must define exactly one of 'run' or 'argv'")
    if has_argv and version < 2:
        raise ContractError(f"{where}: 'argv' requires contract version 2")
    if "writes" in raw and version < 2:
        raise ContractError(f"{where}: 'writes' requires contract version 2")

    raw_name = raw.get("name", f"check-{index}")
    if type(raw_name) is not str or not _CHECK_NAME.fullmatch(raw_name):
        raise ContractError(
            f"{where}: 'name' must be a slug containing only letters, numbers, '.', '_' or '-'"
        )

    run: str | None = None
    argv: list[str] | None = None
    if has_run:
        raw_run = raw["run"]
        if type(raw_run) is not str or not raw_run.strip() or "\x00" in raw_run:
            raise ContractError(f"{where}: 'run' must be a non-empty string")
        run = raw_run
    else:
        raw_argv = raw["argv"]
        if type(raw_argv) is not list or not raw_argv:
            raise ContractError(f"{where}: 'argv' must be a non-empty list of strings")
        if not all(type(arg) is str and arg and "\x00" not in arg for arg in raw_argv):
            raise ContractError(f"{where}: 'argv' must be a non-empty list of strings")
        argv = list(raw_argv)

    raw_timeout = _integer(raw.get("timeout", 600), f"{where}: 'timeout'", positive=True)

    directory: str | None = None
    if "dir" in raw:
        raw_dir = raw["dir"]
        if type(raw_dir) is not str or not raw_dir:
            raise ContractError(f"{where}: 'dir' must be a non-empty repository-relative string")
        directory = _repo_relative(raw_dir, f"{where}.dir")

    raw_env = raw.get("env", {})
    raw_env = _mapping(raw_env, f"{where}.env")
    env: dict[str, str] = {}
    for key, value in raw_env.items():
        if type(key) is not str or not key or "=" in key or "\x00" in key:
            raise ContractError(f"{where}: environment names must be non-empty strings")
        if type(value) is not str or "\x00" in value:
            raise ContractError(f"{where}: environment values must be strings")
        env[key] = value

    writes = _str_list(raw.get("writes", []), f"{where}.writes", paths=True)
    return CommandCheck(
        name=raw_name,
        run=run,
        timeout=raw_timeout,
        dir=directory,
        env=env,
        argv=argv,
        writes=writes,
    )


def parse_contract(text: str, source: str | None = None) -> Contract:
    """Parse a contract from text.

    ``source`` is an informational path or trusted-ref label recorded in the
    resulting contract.  This entry point lets CI load policy bytes directly
    from a trusted Git object without first writing them into the worktree.
    """

    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        label = f" in {source}" if source else ""
        raise ContractError(f"invalid YAML{label}: {exc}") from exc
    if data is None:
        data = {}
    if type(data) is not dict:
        raise ContractError("dod.yaml must be a mapping")
    label = Path(source).name if source else "dod.yaml"
    _reject_unknown(data, _TOP_KEYS, label)

    raw_version = _integer(data.get("version", 1), "'version'")
    if raw_version not in SUPPORTED_VERSIONS:
        supported = ", ".join(str(version) for version in sorted(SUPPORTED_VERSIONS))
        raise ContractError(f"unsupported contract version {raw_version!r} (supported: {supported})")

    raw_checks = data.get("checks", [])
    if type(raw_checks) is not list:
        raise ContractError("'checks' must be a list")
    checks = [_parse_check(raw, i, raw_version) for i, raw in enumerate(raw_checks)]
    names = [check.name for check in checks]
    duplicate_names = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicate_names:
        raise ContractError(f"check names must be unique: {', '.join(duplicate_names)}")

    raw_require = data.get("require", {})
    raw_require = _mapping(raw_require, "require")
    _reject_unknown(raw_require, _REQUIRE_KEYS, "require")
    require_changed = _str_list(
        raw_require.get("changed", []), "require.changed", paths=True,
    )
    raw_non_empty = raw_require.get("non_empty_diff", False)
    non_empty_diff = _bool(raw_non_empty, "require.non_empty_diff")
    require = Requirements(changed=require_changed, non_empty_diff=non_empty_diff)

    test_globs = _str_list(
        data.get("test_globs", DEFAULT_TEST_GLOBS), "test_globs", paths=True,
    )
    protected = _str_list(
        data.get("protected", DEFAULT_PROTECTED), "protected", paths=True,
    )
    tamper = _bool(data.get("tamper", True), "tamper")
    stubs = _bool(data.get("stubs", True), "stubs")
    strict = _bool(data.get("strict", False), "strict")

    if not (
        checks or protected or (tamper and test_globs) or stubs
        or require.changed or require.non_empty_diff
    ):
        raise ContractError("contract is a no-op: enable at least one verification rule")

    contract = Contract(
        version=raw_version,
        base=_optional_base(data),
        checks=checks,
        test_globs=test_globs,
        protected=protected,
        tamper=tamper,
        stubs=stubs,
        strict=strict,
        require=require,
        source=source,
    )
    if contract.legacy:
        warnings.warn(
            "contract version 1 is deprecated; run `dunnit migrate` to upgrade to version 2",
            DeprecationWarning,
            stacklevel=2,
        )
    return contract


def load_contract(path: Path | str) -> Contract:
    path = Path(path)
    if not path.exists():
        raise ContractError(f"contract not found: {path} (run `dunnit init` to create one)")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"could not read contract {path}: {exc}") from exc
    return parse_contract(text, source=str(path))
