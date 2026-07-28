"""dunnit — did the agent actually do it?

Tamper-evident verification of AI agent work. Declare your definition of
done in ``dod.yaml``; ``dunnit verify`` re-runs the proof itself and checks
the diff for test-gaming (deleted tests, added skips, focused tests,
weakened assertions, config edits that deselect tests, stubbed
implementations). Never trust the transcript.
"""

from dunnit.contract import CommandCheck, Contract, Requirements, load_contract
from dunnit.runner import verify
from dunnit.verdict import Evidence, Status, Verdict

__version__ = "0.3.0"
__all__ = [
    "CommandCheck",
    "Contract",
    "Evidence",
    "Requirements",
    "Status",
    "Verdict",
    "__version__",
    "load_contract",
    "verify",
]
