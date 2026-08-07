"""Dunnit: an offline contract runner and Git-diff integrity guard.

Dunnit establishes whether a committed, deterministic verification contract
was satisfied. It does not prove that an agent understood or fulfilled every
part of human intent.
"""

from dunnit.contract import CommandCheck, Contract, Requirements, load_contract, parse_contract
from dunnit.runner import verify
from dunnit.verdict import Evidence, Outcome, Status, Verdict

__version__ = "1.0.0b1"
__all__ = [
    "CommandCheck",
    "Contract",
    "Evidence",
    "Outcome",
    "Requirements",
    "Status",
    "Verdict",
    "__version__",
    "load_contract",
    "parse_contract",
    "verify",
]
