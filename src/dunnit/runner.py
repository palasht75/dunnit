"""Orchestrate all checks into a single verdict."""

from __future__ import annotations

from pathlib import Path

from dunnit.checks import check_stubs, check_tamper, run_command
from dunnit.contract import Contract, load_contract
from dunnit.gitdiff import collect_diff
from dunnit.verdict import Evidence, Status, Verdict


def verify(
    contract: Contract | str | Path = "dod.yaml",
    cwd: Path | None = None,
) -> Verdict:
    """Verify the work in ``cwd`` against the contract. Returns a Verdict."""
    if not isinstance(contract, Contract):
        contract = load_contract(contract)
    cwd = Path(cwd or Path.cwd())
    verdict = Verdict()

    for check in contract.checks:
        verdict.add(run_command(check, cwd))

    if contract.tamper or contract.stubs:
        try:
            diffs = collect_diff(cwd, contract.base)
        except RuntimeError as exc:
            verdict.add(Evidence("diff", Status.WARN, f"diff checks skipped: {exc}"))
        else:
            if contract.tamper:
                for ev in check_tamper(diffs, contract.test_globs):
                    verdict.add(ev)
            if contract.stubs:
                for ev in check_stubs(diffs, contract.test_globs):
                    verdict.add(ev)
    return verdict
