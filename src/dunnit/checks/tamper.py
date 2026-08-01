"""Detect test-gaming in the diff: deleted or renamed-away test files, added
skip/xfail markers, focused tests (.only), net-removed or trivialized
assertions, and test-runner config edits that deselect tests.

Patterns are scoped by file extension so `fit(` flags a Jasmine focus marker
in a .ts file without flagging curve-fitting code in a .py file. Source-code
matches inside string literals are ignored (see dunnit.lines.probably_in_string),
while quoted values in structured config remain active policy and are scanned.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from dunnit.gitdiff import FileDiff, matches_any
from dunnit.lines import probably_in_string
from dunnit.verdict import Evidence, Status

_EXT_GROUP = {
    ".py": "py", ".pyi": "py",
    ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js",
    ".mjs": "js", ".cjs": "js", ".mts": "js", ".cts": "js",
    ".svelte": "js", ".vue": "js",
    ".go": "go",
    ".rs": "rust",
    ".java": "jvm", ".kt": "jvm", ".kts": "jvm", ".scala": "jvm", ".groovy": "jvm",
    ".rb": "ruby",
    ".cs": "dotnet", ".vb": "dotnet",
    ".php": "php",
}

_SKIP = {
    "py": re.compile(
        r"pytest\.mark\.skip|pytest\.skip\(|pytest\.mark\.xfail|pytest\.xfail\("
        r"|unittest\.skip|\bskipTest\(|@skip\b|@skipIf\b|@skipUnless\b|expectedFailure"
    ),
    "js": re.compile(
        r"\b(?:it|test|describe|context|suite)\.skip\b|\bx(?:it|test|describe|context)\s*\("
        r"|\bthis\.skip\(\)|\b(?:it|test)\.todo\("
    ),
    "go": re.compile(r"\b[tb]\.Skip(?:f|Now)?\("),
    "rust": re.compile(r"#\[\s*ignore"),
    "jvm": re.compile(r"@Ignore\b|@Disabled\b"),
    "ruby": re.compile(r"\bxit\b|\bxdescribe\b|\bxcontext\b|\bskip\s*[:(]"),
    "dotnet": re.compile(r"\[Ignore\b|\bSkip\s*=\s*\""),
    "php": re.compile(r"markTestSkipped|markTestIncomplete"),
}

# Focused tests silently deselect everything else in the suite.
_FOCUS = {
    "js": re.compile(r"\b(?:it|test|describe|context)\.only\b|\bf(?:it|describe)\s*\("),
    "ruby": re.compile(r"\bfit\b|\bfdescribe\b|focus:\s*true"),
}

_TRIVIAL = {
    "py": re.compile(r"^\s*assert\s+(?:True|1)\s*(?:,|#|$)"),
    "js": re.compile(r"expect\(\s*(?:true|1)\s*\)\s*\.\s*(?:toBe|toEqual|toBeTruthy)"),
    "jvm": re.compile(r"assert(?:True|That)\(\s*true\s*\)"),
}

_ASSERT = re.compile(
    r"^\s*(?:await\s+)?(?:"
    r"assert\b|assert\w*!?\s*\(|self\.assert\w*\("
    r"|(?:with\s+)?pytest\.(?:raises|warns)\s*\("
    r"|expect\s*\(|\bASSERT_\w+"
    r"|[tb]\.(?:Error|Fatal|Fail)\w*\("
    r"|(?:assert|require|check|Assert)\.\w+\("
    r")"
)

# Files whose only job is configuring the test runner: any edit deserves a
# second look, a deselection pattern is an outright fail.
_DEDICATED_CONFIGS = [
    "pytest.ini", "nose2.cfg", ".coveragerc",
    "jest.config.*", "vitest.config.*", "vitest.workspace.*",
    "karma.conf.*", ".mocharc*", "playwright.config.*", "cypress.config.*",
    "phpunit.xml*", ".nycrc*", "codecov.yml", ".codecov.yml",
]
# Shared config files are edited legitimately all the time (version bumps,
# new deps) — only flag them when a deselection pattern is added.
_SHARED_CONFIGS = ["pyproject.toml", "setup.cfg", "tox.ini", "package.json", "**/conftest.py"]

_DESELECT = re.compile(
    r"--ignore(?:-glob)?[=\s]|--deselect|collect_ignore|norecursedirs"
    r"|testPathIgnorePatterns|testIgnore|ignoreTestFiles|--no-?cov\b"
    r"|\bomit\s*=|fail_under\s*=\s*0"
)

# In a dedicated runner config, an ``ignore``/``exclude`` key is itself
# executable policy. Keep this narrower pattern out of shared configuration
# such as package.json, where an unrelated tool may legitimately own a key
# with the same generic name.
_DEDICATED_DESELECT = re.compile(
    r"^\s*[\"']?(?:ignore|exclude)[\"']?\s*[:=]", re.IGNORECASE
)

# Only report command narrowing when an existing broad test invocation is
# replaced by one with an obvious path/node selector. This deliberately does
# not attempt to parse every shell language or every runner option.
_TEST_RUNNERS = {
    "pytest": re.compile(r"\b(?:pytest|python(?:3(?:\.\d+)?)?\s+-m\s+pytest)\b"),
    "jest": re.compile(r"\b(?:jest|vitest|mocha)\b"),
    "go": re.compile(r"\bgo\s+test\b"),
    "rust": re.compile(r"\bcargo\s+test\b"),
}
_PYTEST_SELECTOR = re.compile(
    r"(?:^|\s)(?:-k|-m|--keyword|--markexpr)(?:=|\s+)\S+"
    r"|(?:^|\s)--(?:lf|last-failed)\b"
    r"|(?:^|\s)(?:[^\s\"',;{}]+[/\\])?[^\s\"',;{}]+\.py"
    r"(?:::[^\s\"',;{}]+)?"
    r"|(?:^|\s)(?:tests?|spec|__tests__)(?=\s|[/\\\"',;{}]|$)"
)
_JEST_SELECTOR = re.compile(
    r"(?:^|\s)(?:-t|--testNamePattern|--testPathPattern|--runTestsByPath"
    r"|--changedSince|--onlyChanged|--findRelatedTests|--shard)(?:=|\s|$)"
    r"|(?:^|\s)[^\s\"',;{}]+\.(?:test|spec)\.[A-Za-z0-9]+"
)
_GO_SELECTOR = re.compile(r"(?:^|\s)-(?:run|skip|list)(?:=|\s)|(?:^|\s)\./(?!\.\.)(?!\.\.\.)\S+")
_RUST_SELECTOR = re.compile(
    r"(?:^|\s)(?:-p|--package|--exclude|--test|--lib|--bin)(?:=|\s|$)"
    r"|^\s+[A-Za-z_][A-Za-z0-9_:-]*(?=\s|[\"',;{}]|$)"
)
_NO_EXECUTE = {
    "pytest": re.compile(r"(?:^|\s)(?:--collect-only|--co|--fixtures|--markers)(?=\s|[\"',;{}]|$)"),
    "jest": re.compile(r"(?:^|\s)(?:--listTests|--list|--showConfig)(?=\s|[\"',;{}]|$)"),
    "go": re.compile(r"(?:^|\s)-list(?:=|\s)"),
    "rust": re.compile(r"(?:^|\s)(?:--no-run|--list)(?=\s|[\"',;{}]|$)"),
}
_TEST_SCRIPT = re.compile(
    r"(?:^|[{,\s])[\"']?(?:test|tests|check|verify)[\"']?\s*[:=]",
    re.IGNORECASE,
)
_NOOP_TEST_SCRIPT = re.compile(
    r"[\"']?(?:test|tests|check|verify)[\"']?\s*[:=]\s*"
    r"(?P<quote>[\"'])\s*(?:true|:|exit(?:\s+/b)?\s+0|echo(?:\s+[^;&|]*?)?|"
    r"printf(?:\s+[^;&|]*?)?)\s*(?P=quote)",
    re.IGNORECASE,
)

_PY_TEST_DEF = re.compile(
    r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+test[A-Za-z0-9_]*\s*\([^)]*\)"
    r"(?:\s*->\s*[^:]+)?\s*:\s*(?P<tail>.*)$"
)
_PY_EMPTY_STMT = re.compile(r"^(?:pass|\.\.\.|return(?:\s+None)?)\s*(?:#.*)?$")
_EMPTY_INLINE = {
    "js": re.compile(
        r"\b(?:it|test)\s*\(\s*[^,]+,\s*(?:async\s+)?"
        r"(?:(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|function\s*\([^)]*\))"
        r"\s*\{\s*\}\s*\)\s*;?",
        re.DOTALL,
    ),
    "go": re.compile(r"\bfunc\s+Test\w*\s*\([^)]*\)\s*\{\s*\}", re.DOTALL),
    "rust": re.compile(
        r"#\[\s*test\s*\]\s*(?:async\s+)?fn\s+\w+\s*\([^)]*\)\s*\{\s*\}",
        re.DOTALL,
    ),
}


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _matching(lines: list[str], pattern: re.Pattern[str] | None) -> list[str]:
    if pattern is None:
        return []
    out = []
    for ln in lines:
        m = pattern.search(ln)
        if m and not probably_in_string(ln, m.start()):
            out.append(ln.strip()[:120])
    return out


def _empty_added_tests(
    lines: list[str], group: str | None, removed_lines: list[str] | None = None,
) -> list[str]:
    """Return syntactically empty tests wholly visible in added lines.

    This is intentionally conservative. We do not infer emptiness when only a
    fragment of a pre-existing body is present in the diff; assertion-removal
    accounting handles that case. Python suites are recognized only when the
    added body contains one explicit no-op statement. Other ecosystems are
    limited to a fully empty callback/body visible in the added text.
    """

    if group == "py":
        empty: list[str] = []
        for index, line in enumerate(lines):
            match = _PY_TEST_DEF.match(line)
            if not match:
                continue
            tail = match.group("tail").strip()
            if tail:
                if _PY_EMPTY_STMT.fullmatch(tail):
                    empty.append(line.strip()[:120])
                continue

            base_indent = len(match.group("indent").expandtabs(8))
            body: list[str] = []
            for candidate in lines[index + 1:]:
                stripped = candidate.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent = len(candidate) - len(candidate.lstrip(" \t"))
                if len(candidate[:indent].expandtabs(8)) <= base_indent:
                    break
                body.append(stripped)
            if len(body) == 1 and _PY_EMPTY_STMT.fullmatch(body[0]):
                empty.append(line.strip()[:120])
        # When the declaration is unchanged, a diff contains only the removed
        # expectation and the new no-op body. Treat pytest.raises/warns as
        # assertions too, so those common tests cannot be replaced by ``pass``.
        if removed_lines and any(_ASSERT.search(line) for line in removed_lines):
            for line in lines:
                if _PY_EMPTY_STMT.fullmatch(line.strip()) and line.strip() not in empty:
                    empty.append(line.strip()[:120])
        return empty

    pattern = _EMPTY_INLINE.get(group) if group is not None else None
    if pattern is None:
        return []
    return [match.group(0).strip()[:120] for match in pattern.finditer("\n".join(lines))]


def _structured_config(path: str) -> bool:
    """Whether quoted strings in ``path`` are active configuration values."""

    name = PurePosixPath(path).name.lower()
    suffix = PurePosixPath(name).suffix
    return suffix in {".cfg", ".ini", ".json", ".toml", ".xml", ".yaml", ".yml"} or name in {
        ".coveragerc",
        ".nycrc",
    }


def _active_config_match(path: str, line: str, pos: int) -> bool:
    # A quote is data syntax in JSON/TOML/YAML/INI, not evidence that the
    # matched option is merely a source-code fixture. Retain the guard for
    # executable configs such as conftest.py and jest.config.js.
    return _structured_config(path) or not probably_in_string(line, pos)


def _runner_tail(line: str, runner: re.Pattern[str]) -> str | None:
    match = runner.search(line)
    return line[match.end():] if match else None


def _has_selector(name: str, tail: str) -> bool:
    if name == "pytest":
        return bool(_PYTEST_SELECTOR.search(tail))
    if name == "jest":
        return bool(_JEST_SELECTOR.search(tail))
    if name == "go":
        return bool(_GO_SELECTOR.search(tail))
    return bool(_RUST_SELECTOR.search(tail))


def _became_narrower(name: str, before: str, after: str) -> bool:
    if _NO_EXECUTE[name].search(after) and not _NO_EXECUTE[name].search(before):
        return True
    if name == "go" and "./..." in before and "./..." not in after:
        return True
    if name == "rust" and "--workspace" in before and "--workspace" not in after:
        return True
    return not _has_selector(name, before) and _has_selector(name, after)


def _test_command_findings(d: FileDiff) -> tuple[list[str], list[str]]:
    """Find clear command disablement and paired broad -> scoped changes."""

    narrowed: list[str] = []
    changed: list[str] = []
    for name, runner in _TEST_RUNNERS.items():
        removed = [
            tail
            for line in d.removed_lines
            if (tail := _runner_tail(line, runner)) is not None
        ]
        added = [
            tail
            for line in d.added_lines
            if (tail := _runner_tail(line, runner)) is not None
        ]
        if not removed:
            continue
        narrowed_tail = next(
            (after for before in removed for after in added if _became_narrower(name, before, after)),
            None,
        )
        if narrowed_tail is not None:
            narrowed.append(f"{name}: {narrowed_tail.strip()[:100]}")

    removed_script = any(_TEST_SCRIPT.search(line) for line in d.removed_lines)
    added_script_lines = [line for line in d.added_lines if _TEST_SCRIPT.search(line)]
    if removed_script and not added_script_lines:
        changed.append("test/check script removed")
    elif removed_script and any(_NOOP_TEST_SCRIPT.search(line) for line in added_script_lines):
        changed.append("test/check script replaced by an explicit no-op")
    return narrowed, changed


def _discoverable_test_path(path: str) -> bool:
    """Recognize strong, ecosystem-native test discovery identities."""

    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    ext = _EXT_GROUP.get(_ext(normalized))
    if ext == "py":
        return name.startswith("test_") or name.endswith("_test.py")
    if ext == "js":
        return ".test." in name or ".spec." in name or "/__tests__/" in f"/{normalized}"
    if ext == "go":
        return name.endswith("_test.go")
    if ext == "rust":
        return normalized.startswith("tests/") or "/tests/" in f"/{normalized}"
    if ext == "jvm":
        stem = name.rsplit(".", 1)[0]
        return stem.endswith(("test", "tests", "it"))
    if ext == "ruby":
        return name.endswith("_spec.rb")
    if ext == "dotnet":
        stem = name.rsplit(".", 1)[0]
        return stem.endswith(("test", "tests"))
    if ext == "php":
        return name.endswith("test.php")
    return False


def _config_parent(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized.rsplit("/", 1)[0] if "/" in normalized else ""


def _shared_config_has_proof(path: str, lines: list[str]) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in {"tox.ini", "conftest.py"}:
        return True
    content = "\n".join(lines)
    if name == "pyproject.toml":
        return bool(re.search(r"\[tool\.(?:pytest|coverage|tox)\b", content, re.IGNORECASE))
    if name == "setup.cfg":
        return bool(re.search(r"\[(?:tool:pytest|coverage:|tox\b)", content, re.IGNORECASE))
    if name == "package.json":
        return bool(_TEST_SCRIPT.search(content) or re.search(r"\b(?:jest|vitest|mocha)\b", content))
    return False


def check_tamper(diffs: list[FileDiff], test_globs: list[str]) -> list[Evidence]:
    evidence: list[Evidence] = []
    test_diffs = [d for d in diffs if matches_any(d.path, test_globs)]

    deleted = [d.path for d in test_diffs if d.status == "D"]
    deleted += [
        f"{d.old_path} -> {d.path}"
        for d in diffs
        if d.status == "R" and d.old_path
        and matches_any(d.old_path, test_globs)
        and (
            not matches_any(d.path, test_globs)
            or (
                _discoverable_test_path(d.old_path)
                and not _discoverable_test_path(d.path)
            )
        )
    ]
    if deleted:
        evidence.append(
            Evidence(
                "tamper:deleted-tests", Status.FAIL,
                f"test files deleted or renamed away: {', '.join(deleted)}",
                hint="Restore the tests and make them pass — deleting a failing test "
                     "does not complete the task.",
            )
        )

    for d in test_diffs:
        group = _EXT_GROUP.get(_ext(d.path))
        skips = _matching(d.added_lines, _SKIP.get(group) if group is not None else None)
        if skips:
            evidence.append(
                Evidence(
                    "tamper:added-skips", Status.FAIL,
                    f"{d.path}: skip markers added: {skips[:3]}",
                    hint="Remove the skip/xfail marker and fix the code so the test passes.",
                    path=d.path,
                )
            )
        focused = _matching(d.added_lines, _FOCUS.get(group) if group is not None else None)
        if focused:
            evidence.append(
                Evidence(
                    "tamper:focused-tests", Status.FAIL,
                    f"{d.path}: focus markers added: {focused[:3]}",
                    hint="Remove .only/focus markers so the whole suite runs, "
                         "not just the tests that pass.",
                    path=d.path,
                )
            )
        trivial = _matching(d.added_lines, _TRIVIAL.get(group) if group is not None else None)
        if trivial:
            evidence.append(
                Evidence(
                    "tamper:trivial-assertions", Status.FAIL,
                    f"{d.path}: trivial assertions added: {trivial[:3]}",
                    hint="Assert on real behavior — `assert True` proves nothing.",
                    path=d.path,
                )
            )

        empty = _empty_added_tests(d.added_lines, group, d.removed_lines)
        if empty:
            evidence.append(
                Evidence(
                    "tamper:empty-tests", Status.FAIL,
                    f"{d.path}: empty test bodies added: {empty[:3]}",
                    hint="Exercise behavior and assert its result; an empty test cannot prove work.",
                    path=d.path,
                )
            )

    # Account per file. A new assertion in an unrelated test must not offset a
    # removed assertion in the test that covered the changed behavior.
    for d in test_diffs:
        if d.status == "D":
            continue
        removed = sum(1 for line in d.removed_lines if _ASSERT.search(line))
        added = sum(1 for line in d.added_lines if _ASSERT.search(line))
        if removed > added:
            evidence.append(
                Evidence(
                    "tamper:removed-assertions", Status.FAIL,
                    f"{d.path}: assertions removed ({removed} removed vs {added} added)",
                    hint="Restore the removed assertions — weakening tests does not "
                         "complete the task.",
                    path=d.path,
                )
            )

    evidence.extend(_check_configs(diffs))

    if not evidence:
        evidence.append(Evidence("tamper", Status.PASS, "no test tampering detected"))
    return evidence


def _check_configs(diffs: list[FileDiff]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for d in diffs:
        dedicated = matches_any(d.path, _DEDICATED_CONFIGS)
        shared = matches_any(d.path, _SHARED_CONFIGS)
        old_dedicated = bool(d.old_path and matches_any(d.old_path, _DEDICATED_CONFIGS))
        old_shared = bool(d.old_path and matches_any(d.old_path, _SHARED_CONFIGS))
        removed_path = d.status == "D" and dedicated
        renamed_away = bool(
            d.status == "R"
            and d.old_path
            and old_dedicated
            and (
                not (dedicated or shared)
                or _config_parent(d.old_path) != _config_parent(d.path)
            )
        )
        removed_shared = bool(
            d.status == "D"
            and shared
            and _shared_config_has_proof(d.path, d.removed_lines)
        )
        renamed_shared = bool(
            d.status == "R"
            and d.old_path
            and old_shared
            and (
                not (dedicated or shared)
                or _config_parent(d.old_path) != _config_parent(d.path)
            )
            and (
                _shared_config_has_proof(d.old_path, d.removed_lines)
                or _config_parent(d.old_path) == ""
            )
        )
        if removed_path or renamed_away or removed_shared or renamed_shared:
            was_renamed = renamed_away or renamed_shared
            path = d.old_path if was_renamed else d.path
            action = f"renamed outside its runner scope to {d.path}" if was_renamed else "deleted"
            evidence.append(
                Evidence(
                    "tamper:test-config-removed", Status.FAIL,
                    f"{path}: test/proof configuration {action}",
                    hint="Restore the test-runner configuration so the full proof harness remains active.",
                    path=path,
                )
            )
            continue
        if not dedicated and not shared:
            continue
        bad = []
        for ln in d.added_lines:
            patterns = (_DESELECT, _DEDICATED_DESELECT) if dedicated else (_DESELECT,)
            match = None
            for pattern in patterns:
                match = pattern.search(ln)
                if match:
                    break
            if match and _active_config_match(d.path, ln, match.start()):
                bad.append(ln.strip()[:120])
        if bad:
            evidence.append(
                Evidence(
                    "tamper:test-config", Status.FAIL,
                    f"{d.path}: test-deselection added: {bad[:3]}",
                    hint="Revert config changes that ignore, deselect, or exclude "
                         "tests from running.",
                    path=d.path,
                )
            )
        elif dedicated and d.status in {"M", "R"}:
            evidence.append(
                Evidence(
                    "tamper:test-config-changed", Status.WARN,
                    f"{d.path}: test-runner config modified",
                    hint="Confirm this config change does not silence or narrow the suite.",
                    path=d.path,
                )
            )
        elif d.status == "R" and (old_dedicated or old_shared):
            evidence.append(
                Evidence(
                    "tamper:test-config-changed", Status.WARN,
                    f"{d.old_path} -> {d.path}: runner configuration migrated in the same scope",
                    hint="Confirm the replacement preserves test discovery and proof coverage.",
                    path=d.path,
                )
            )

        narrowed, disabled = _test_command_findings(d)
        if narrowed:
            evidence.append(
                Evidence(
                    "tamper:test-command-narrowed", Status.FAIL,
                    f"{d.path}: test command changed from broad discovery to a path selector: "
                    f"{narrowed[:3]}",
                    hint="Restore broad test discovery; use a separate developer-only command for filters.",
                    path=d.path,
                )
            )
        if disabled:
            evidence.append(
                Evidence(
                    "tamper:test-command-disabled", Status.FAIL,
                    f"{d.path}: proof command disabled: {disabled[:3]}",
                    hint="Restore an executable test/proof command that exercises the intended suite.",
                    path=d.path,
                )
            )
    return evidence
