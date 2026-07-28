"""Detect fake implementations in changed non-test code: TODOs left in
added lines, NotImplementedError, silently swallowed exceptions."""

from __future__ import annotations

import re

from dunnit.gitdiff import FileDiff, matches_any
from dunnit.verdict import Evidence, Status

DOC_GLOBS = ["**/*.md", "**/*.rst", "**/*.txt", "docs/**", "*.md", "*.rst", "*.txt"]

STUB_PATTERNS = [
    (re.compile(r"\b(TODO|FIXME|XXX)\b"), "todo-left-behind"),
    (re.compile(r"raise NotImplementedError"), "not-implemented"),
    (re.compile(r"except[^:]*:\s*pass\s*$"), "swallowed-exception"),
]


def check_stubs(diffs: list[FileDiff], test_globs: list[str]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for d in diffs:
        if d.status == "D" or matches_any(d.path, test_globs) or matches_any(d.path, DOC_GLOBS):
            continue
        for ln in d.added_lines:
            for pattern, label in STUB_PATTERNS:
                if pattern.search(ln):
                    evidence.append(
                        Evidence(f"stubs:{label}", Status.WARN, f"{d.path}: {ln.strip()[:120]}")
                    )
    if not evidence:
        evidence.append(Evidence("stubs", Status.PASS, "no stub patterns in changed code"))
    return evidence
