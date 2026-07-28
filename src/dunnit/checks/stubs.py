"""Detect fake implementations in changed non-test code: TODOs left in added
lines, not-implemented stubs, swallowed exceptions, and comments that
suppress linters, type checkers, or coverage.

WARN-level by design — each pattern has legitimate uses — but `strict: true`
(or --strict) promotes them to failures. Findings are aggregated per file and
category so a large diff cannot flood the verdict.
"""

from __future__ import annotations

import re

from dunnit.gitdiff import FileDiff, matches_any
from dunnit.lines import probably_in_string
from dunnit.verdict import Evidence, Status

DOC_GLOBS = ["**/*.md", "**/*.rst", "**/*.txt", "docs/**", "LICENSE*", "CHANGELOG*"]

_STUB_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\b(?:TODO|FIXME|XXX)\b"),
        "todo-left-behind",
        "Finish the work the marker refers to, or drop it if it is stale.",
    ),
    (
        re.compile(
            r"raise NotImplementedError|NotImplementedException"
            r"|\btodo!\s*\(|\bunimplemented!\s*\("
            r"|throw new Error\(\s*['\"](?:not.?implemented|todo)",
            re.IGNORECASE,
        ),
        "not-implemented",
        "Implement the function — a stub that raises is not a completed task.",
    ),
    (
        re.compile(r"except[^:]*:\s*pass\s*(?:#.*)?$"),
        "swallowed-exception",
        "Handle or propagate the error; silencing it hides failures.",
    ),
    (
        re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}"),
        "swallowed-exception",
        "Handle or propagate the error; an empty catch block hides failures.",
    ),
    (
        re.compile(
            r"#\s*noqa|#\s*type:\s*ignore|pylint:\s*disable|eslint-disable"
            r"|@ts-ignore|@ts-nocheck|@ts-expect-error|@SuppressWarnings|rubocop:disable"
        ),
        "suppressed-checker",
        "Fix the underlying warning instead of suppressing the checker in new code.",
    ),
    (
        re.compile(r"pragma:\s*no\s*cover|istanbul ignore|c8 ignore"),
        "coverage-excluded",
        "New code excluded from coverage is unverified — remove the exclusion or test it.",
    ),
]

_EXCEPT_HEAD = re.compile(r"^\s*except\b[^:]*:\s*(?:#.*)?$")


def check_stubs(diffs: list[FileDiff], test_globs: list[str]) -> list[Evidence]:
    # keyed by (path, label) -> [first offending line, count, hint]
    found: dict[tuple[str, str], list] = {}

    def record(path: str, label: str, line: str, hint: str) -> None:
        entry = found.setdefault((path, label), ["", 0, hint])
        if entry[1] == 0:
            entry[0] = line.strip()[:120]
        entry[1] += 1

    for d in diffs:
        if d.status == "D" or matches_any(d.path, test_globs) or matches_any(d.path, DOC_GLOBS):
            continue
        for ln in d.added_lines:
            for pattern, label, hint in _STUB_PATTERNS:
                m = pattern.search(ln)
                if m and not probably_in_string(ln, m.start()):
                    record(d.path, label, ln, hint)
        # `except X:` on one added line, `pass` on the next. Consecutive added
        # lines are usually adjacent in the file; good enough for a WARN.
        for prev, cur in zip(d.added_lines, d.added_lines[1:]):
            if _EXCEPT_HEAD.match(prev) and cur.strip() in ("pass", "..."):
                record(d.path, "swallowed-exception", prev + " " + cur.strip(),
                       "Handle or propagate the error; silencing it hides failures.")

    evidence = []
    for (path, label), (first, count, hint) in sorted(found.items()):
        more = f" (+{count - 1} more)" if count > 1 else ""
        evidence.append(
            Evidence(f"stubs:{label}", Status.WARN, f"{path}: {first}{more}", hint=hint)
        )
    if not evidence:
        evidence.append(Evidence("stubs", Status.PASS, "no stub patterns in changed code"))
    return evidence
