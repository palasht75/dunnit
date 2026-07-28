"""Detect test-gaming in the diff: deleted or renamed-away test files, added
skip/xfail markers, focused tests (.only), net-removed or trivialized
assertions, and test-runner config edits that deselect tests.

Patterns are scoped by file extension so `fit(` flags a Jasmine focus marker
in a .ts file without flagging curve-fitting code in a .py file. Matches that
sit inside string literals are ignored (see dunnit.lines.probably_in_string).
"""

from __future__ import annotations

import re

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
    "phpunit.xml*", ".nycrc*", "codecov.yml",
]
# Shared config files are edited legitimately all the time (version bumps,
# new deps) — only flag them when a deselection pattern is added.
_SHARED_CONFIGS = ["pyproject.toml", "setup.cfg", "tox.ini", "package.json", "**/conftest.py"]

_DESELECT = re.compile(
    r"--ignore(?:-glob)?[=\s]|--deselect|collect_ignore|norecursedirs"
    r"|testPathIgnorePatterns|testIgnore|ignoreTestFiles|--no-?cov\b"
    r"|\bomit\s*=|fail_under\s*=\s*0"
)


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _matching(lines: list[str], pattern: re.Pattern | None) -> list[str]:
    if pattern is None:
        return []
    out = []
    for ln in lines:
        m = pattern.search(ln)
        if m and not probably_in_string(ln, m.start()):
            out.append(ln.strip()[:120])
    return out


def check_tamper(diffs: list[FileDiff], test_globs: list[str]) -> list[Evidence]:
    evidence: list[Evidence] = []
    test_diffs = [d for d in diffs if matches_any(d.path, test_globs)]

    deleted = [d.path for d in test_diffs if d.status == "D"]
    deleted += [
        f"{d.old_path} -> {d.path}"
        for d in diffs
        if d.status == "R" and d.old_path
        and matches_any(d.old_path, test_globs) and not matches_any(d.path, test_globs)
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
        skips = _matching(d.added_lines, _SKIP.get(group))
        if skips:
            evidence.append(
                Evidence(
                    "tamper:added-skips", Status.FAIL,
                    f"{d.path}: skip markers added: {skips[:3]}",
                    hint="Remove the skip/xfail marker and fix the code so the test passes.",
                )
            )
        focused = _matching(d.added_lines, _FOCUS.get(group))
        if focused:
            evidence.append(
                Evidence(
                    "tamper:focused-tests", Status.FAIL,
                    f"{d.path}: focus markers added: {focused[:3]}",
                    hint="Remove .only/focus markers so the whole suite runs, "
                         "not just the tests that pass.",
                )
            )
        trivial = _matching(d.added_lines, _TRIVIAL.get(group))
        if trivial:
            evidence.append(
                Evidence(
                    "tamper:trivial-assertions", Status.FAIL,
                    f"{d.path}: trivial assertions added: {trivial[:3]}",
                    hint="Assert on real behavior — `assert True` proves nothing.",
                )
            )

    removed = sum(
        1 for d in test_diffs if d.status != "D"
        for ln in d.removed_lines if _ASSERT.search(ln)
    )
    added = sum(1 for d in test_diffs for ln in d.added_lines if _ASSERT.search(ln))
    if removed > added:
        evidence.append(
            Evidence(
                "tamper:removed-assertions", Status.FAIL,
                f"net assertions removed from tests ({removed} removed vs {added} added)",
                hint="Restore the removed assertions — weakening tests does not "
                     "complete the task.",
            )
        )

    evidence.extend(_check_configs(diffs))

    if not evidence:
        evidence.append(Evidence("tamper", Status.PASS, "no test tampering detected"))
    return evidence


def _check_configs(diffs: list[FileDiff]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for d in diffs:
        if d.status == "D":
            continue
        dedicated = matches_any(d.path, _DEDICATED_CONFIGS)
        if not dedicated and not matches_any(d.path, _SHARED_CONFIGS):
            continue
        bad = []
        for ln in d.added_lines:
            m = _DESELECT.search(ln)
            if m and not probably_in_string(ln, m.start()):
                bad.append(ln.strip()[:120])
        if bad:
            evidence.append(
                Evidence(
                    "tamper:test-config", Status.FAIL,
                    f"{d.path}: test-deselection added: {bad[:3]}",
                    hint="Revert config changes that ignore, deselect, or exclude "
                         "tests from running.",
                )
            )
        elif dedicated and d.status == "M":
            evidence.append(
                Evidence(
                    "tamper:test-config-changed", Status.WARN,
                    f"{d.path}: test-runner config modified",
                    hint="Confirm this config change does not silence or narrow the suite.",
                )
            )
    return evidence
