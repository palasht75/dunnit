"""Orchestrate all checks into a single verdict."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dunnit.checks import check_protected, check_require, check_stubs, check_tamper, run_command
from dunnit.contract import Contract, load_contract
from dunnit.gitdiff import collect_diff
from dunnit.verdict import Evidence, Status, Verdict


def verify(
    contract: Contract | str | Path = "dod.yaml",
    cwd: Path | None = None,
    base: str | None = None,
    strict: bool | None = None,
    allow: Sequence[str] = (),
) -> Verdict:
    """Verify the work in ``cwd`` against the contract. Returns a Verdict.

    ``base`` overrides the contract's diff base (handy in CI: the merge base).
    ``strict`` promotes warnings to failures (None: use the contract's setting).
    ``allow`` downgrades named failing checks to warnings — a human escape
    hatch for reviewed exceptions like intentionally deleting an obsolete test.
    """
    if isinstance(contract, Contract):
        contract_file = Path(contract.source) if contract.source else None
    else:
        contract_file = Path(contract)
        contract = load_contract(contract_file)
    cwd = Path(cwd or Path.cwd())
    base_ref = base if base is not None else contract.base
    if strict is None:
        strict = contract.strict

    verdict = Verdict()
    verdict.meta["base"] = base_ref or "HEAD"

    for check in contract.checks:
        verdict.add(run_command(check, cwd))

    require = contract.require
    needs_diff = (
        contract.tamper or contract.stubs or contract.protected
        or require.changed or require.non_empty_diff
    )
    if needs_diff:
        try:
            diffs = collect_diff(cwd, base_ref)
        except RuntimeError as exc:
            verdict.add(Evidence("diff", Status.WARN, f"diff checks skipped: {exc}"))
        else:
            verdict.meta["files_changed"] = len(diffs)
            if contract.protected:
                for ev in check_protected(diffs, contract.protected, _rel(contract_file, cwd)):
                    verdict.add(ev)
            if contract.tamper:
                for ev in check_tamper(diffs, contract.test_globs):
                    verdict.add(ev)
            if contract.stubs:
                for ev in check_stubs(diffs, contract.test_globs):
                    verdict.add(ev)
            for ev in check_require(diffs, require, base_ref or "HEAD"):
                verdict.add(ev)

    _apply_policy(verdict, strict, allow)
    return verdict


def _rel(contract_file: Path | None, cwd: Path) -> str:
    """Repo-relative posix path of the contract file (for the init exemption)."""
    if contract_file is None:
        return "dod.yaml"
    try:
        return contract_file.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return contract_file.name


def _apply_policy(verdict: Verdict, strict: bool, allow: Sequence[str]) -> None:
    for ev in verdict.evidence:
        if ev.status is Status.FAIL and _allowed(ev.check, allow):
            ev.status = Status.WARN
            ev.detail += "  [downgraded by --allow]"
        elif strict and ev.status is Status.WARN:
            ev.status = Status.FAIL


def _allowed(check: str, allow: Sequence[str]) -> bool:
    return any(check == a or check.startswith(a + ":") for a in allow)
