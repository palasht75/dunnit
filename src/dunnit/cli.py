"""dunnit CLI: `dunnit init` and `dunnit verify`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dunnit import __version__
from dunnit.contract import ContractError
from dunnit.runner import verify
from dunnit.verdict import Status

EXAMPLE = """\
# dunnit definition-of-done. Docs: https://github.com/palasht75/dunnit
version: 1
# base: origin/main   # git ref to diff against (default: HEAD = uncommitted work)
checks:
  - name: tests
    run: pytest -q
tamper: true   # fail on deleted tests / added skips / removed assertions
stubs: true    # warn on TODO / NotImplementedError left in changed code
"""

ICONS = {Status.PASS: "✓", Status.FAIL: "✗", Status.WARN: "!"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dunnit", description="Did the agent actually do it?")
    parser.add_argument("--version", action="version", version=f"dunnit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="write an example dod.yaml")
    p_verify = sub.add_parser("verify", help="verify work against the contract")
    p_verify.add_argument("-c", "--config", default="dod.yaml")
    p_verify.add_argument("--json", action="store_true", help="print machine-readable verdict")

    args = parser.parse_args(argv)

    if args.command == "init":
        path = Path("dod.yaml")
        if path.exists():
            print("dod.yaml already exists", file=sys.stderr)
            return 1
        path.write_text(EXAMPLE)
        print("wrote dod.yaml — edit your definition of done, then run: dunnit verify")
        return 0

    try:
        verdict = verify(args.config)
    except ContractError as exc:
        print(f"dunnit: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(verdict.to_json())
    else:
        for ev in verdict.evidence:
            print(f" {ICONS[ev.status]} {ev.check}: {ev.detail.splitlines()[0] if ev.detail else ''}")
        print(f"\nverdict: {'PASS — it did it' if verdict.passed else 'FAIL — it did not do it'}")
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
