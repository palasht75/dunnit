"""Orchestrate all checks into a single verdict."""

from __future__ import annotations

from pathlib import Path

from dunnit.checks import check_protected, check_stubs, check_tamper, run_command
from dunnit.contract import Contract, load_contract
from dunnit.gitdiff import collect_diff
from dunnit.verdict import Evidence, Status, Verdict


def verify(
    contract: Contract | str | Path = "dod.yaml",
    cwd: Path | None = None,
    base: str | None = None,
) -> Verdict:
    """Verify the work in ``cwd`` against the contract. Returns a Verdict.

    ``base`` overrides the contract's diff base (handy in CI: the merge base).
    """
    if not isinstance(contract, Contract):
        contract = load_contract(contract)
    if base is not None:
        contract.base = base
    cwd = Path(cwd or Path.cwd())
    verdict = Verdict()

    for check in contract.checks:
        verdict.add(run_command(check, cwd))

    if contract.tamper or contract.stubs or contract.protected:
        try:
            diffs = collect_diff(cwd, contract.base)
        except RuntimeError as exc:
            verdict.add(Evidence("diff", Status.WARN, f"diff checks skipped: {exc}"))
        else:
            if contract.protected:
                for ev in check_protected(diffs, contract.protected):
                    verdict.add(ev)
            if contract.tamper:
                for ev in check_tamper(diffs, contract.test_globs):
                    verdict.add(ev)
            if contract.stubs:
                for ev in check_stubs(diffs, contract.test_globs):
                    verdict.add(ev)
    return verdict
