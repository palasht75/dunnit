"""dunnit CLI: `dunnit init`, `dunnit verify`, `dunnit snippet`."""

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
protected:            # files the agent must not touch (dod.yaml always should be)
  - dod.yaml
  - .github/**
tamper: true   # fail on deleted tests / added skips / removed assertions
stubs: true    # warn on TODO / NotImplementedError left in changed code
"""

SNIPPETS = {
    "claude": """\
# Claude Code — add to .claude/settings.json (project) or ~/.claude/settings.json.
# The Stop hook runs when Claude thinks it's finished; exit code 2 blocks it
# from stopping and feeds the failing evidence back so it keeps working.
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "dunnit verify >&2 || exit 2" } ] }
    ]
  }
}""",
    "cursor": """\
# Cursor — save as .cursor/rules/dunnit.mdc
---
description: Definition-of-done enforcement via dunnit
alwaysApply: true
---
Before you claim any task is complete, run `dunnit verify` and read the verdict.
- FAIL means the task is NOT done. Fix the real problem and re-run.
- Never edit dod.yaml, tests, or CI config to make verification pass.
- Never add skip markers or delete failing tests.
- Include the dunnit verdict output in your final summary.""",
    "github": """\
# GitHub Actions — add as a job step (after checkout + deps install)
      - name: dunnit verify
        run: |
          pip install dunnit
          git fetch origin ${{ github.base_ref || 'main' }}
          dunnit verify --base origin/${{ github.base_ref || 'main' }} --json""",
    "pre-commit": """\
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: dunnit
        name: dunnit verify
        entry: dunnit verify
        language: system
        pass_filenames: false""",
}

ICONS = {Status.PASS: "✓", Status.FAIL: "✗", Status.WARN: "!"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dunnit", description="Did the agent actually do it?")
    parser.add_argument("--version", action="version", version=f"dunnit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="write an example dod.yaml")

    p_verify = sub.add_parser("verify", help="verify work against the contract")
    p_verify.add_argument("-c", "--config", default="dod.yaml")
    p_verify.add_argument("-b", "--base", default=None, help="override the git diff base ref")
    p_verify.add_argument("--json", action="store_true", help="print machine-readable verdict")

    p_snip = sub.add_parser("snippet", help="print integration config for your agent/CI")
    p_snip.add_argument("target", choices=sorted(SNIPPETS))

    args = parser.parse_args(argv)

    if args.command == "init":
        path = Path("dod.yaml")
        if path.exists():
            print("dod.yaml already exists", file=sys.stderr)
            return 1
        path.write_text(EXAMPLE)
        print("wrote dod.yaml — edit your definition of done, then run: dunnit verify")
        return 0

    if args.command == "snippet":
        print(SNIPPETS[args.target])
        return 0

    try:
        verdict = verify(args.config, base=args.base)
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
