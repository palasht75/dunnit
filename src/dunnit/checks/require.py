"""Positive requirements: the diff must contain the work, not merely avoid
gaming. `require.changed` expresses things like "done includes a test change";
`require.non_empty_diff` catches an agent that claims success without
touching anything.
"""

from __future__ import annotations

from dunnit.contract import Requirements
from dunnit.gitdiff import FileDiff, matches_any
from dunnit.verdict import Evidence, Status


def check_require(diffs: list[FileDiff], require: Requirements, base: str) -> list[Evidence]:
    evidence: list[Evidence] = []
    if require.non_empty_diff and not diffs:
        evidence.append(
            Evidence(
                "require:non-empty-diff", Status.FAIL,
                f"no changes detected against {base}",
                hint="The working tree matches the base ref — there is no work to verify.",
            )
        )
    for glob in require.changed:
        if not any(d.status != "D" and matches_any(d.path, [glob]) for d in diffs):
            evidence.append(
                Evidence(
                    "require:changed", Status.FAIL,
                    f"no changed file matches required pattern '{glob}'",
                    hint="The definition of done requires a change here — "
                         "e.g. a new or updated test.",
                )
            )
    if not evidence and (require.changed or require.non_empty_diff):
        evidence.append(Evidence("require", Status.PASS, "required changes present"))
    return evidence
