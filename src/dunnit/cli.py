"""Command-line interface for verification, onboarding, and CI reports."""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from dunnit import __version__
from dunnit.contract import Contract, ContractError, load_contract
from dunnit.doctor import doctor
from dunnit.onboarding import PRESETS, detect, render_contract
from dunnit.reporting import (
    outcome_exit_code,
    render_github,
    render_junit,
    write_github_summary,
    write_report,
)
from dunnit.runner import verify
from dunnit.verdict import Evidence, Outcome, Status, Verdict

SNIPPETS = {
    "claude": """\
# Claude Code — add to .claude/settings.json (project) or ~/.claude/settings.json.
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
description: Definition-of-done enforcement via Dunnit
alwaysApply: true
---
Before claiming completion, run `dunnit verify` and read the evidence.
- `fail` means the contract was not satisfied.
- `error` means verification was incomplete and must be repaired or rerun.
- Do not weaken dod.yaml, tests, or CI configuration to obtain a pass.""",
    "codex": """\
# Codex / any AGENTS.md-reading agent — append to AGENTS.md
## Definition of done
This repository's completion contract is dod.yaml, enforced by `dunnit verify`.
- Before reporting completion, run `dunnit verify` and include its outcome.
- A fail means the contract is not satisfied; an error means verification is incomplete.
- Never weaken the policy, protected files, tests, or CI configuration to obtain a pass.""",
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

ICONS = {Status.PASS: "✓", Status.FAIL: "✗", Status.WARN: "!", Status.ERROR: "×"}
_COLORS = {Status.PASS: "32", Status.FAIL: "31", Status.WARN: "33", Status.ERROR: "35"}
_MAX_DETAIL_LINES = 30


def main(argv: Sequence[str] | None = None) -> int:
    # Windows consoles may not be UTF-8; replace unrepresentable repository
    # paths rather than crashing while reporting a failure.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(errors="replace")

    parser = _parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return _cmd_init(args)
    if args.command == "migrate":
        return _cmd_migrate(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "snippet":
        if args.target == "github":
            print(_github_snippet(args.mode), end="")
        else:
            print(SNIPPETS[args.target])
        return 0
    return _cmd_verify(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dunnit",
        description="Offline contract runner and Git-diff integrity guard.",
    )
    parser.add_argument("--version", action="version", version=f"dunnit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="detect proof tools and generate an explicit v2 policy")
    p_init.add_argument("--preset", choices=PRESETS, default="auto")
    p_init.add_argument("--dry-run", action="store_true", help="print YAML without writing it")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing dod.yaml")

    p_verify = sub.add_parser("verify", help="verify work against a trusted contract")
    _add_policy_arguments(p_verify)
    p_verify.add_argument("--bootstrap", action="store_true", help="use a first local uncommitted policy")
    p_verify.add_argument(
        "--format", choices=("text", "json", "github", "junit"), default="text"
    )
    p_verify.add_argument("--json", action="store_true", help="legacy alias for --format json")
    p_verify.add_argument("--report", metavar="PATH", help="write the canonical JSON report")
    p_verify.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p_verify.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="CHECK",
        help="downgrade a reviewed failing rule to a warning (repeatable)",
    )
    p_verify.add_argument("-q", "--quiet", action="store_true", help="hide passing text evidence")

    p_doctor = sub.add_parser("doctor", help="check policy, Git, and command readiness")
    _add_policy_arguments(p_doctor)
    p_doctor.add_argument("--json", action="store_true", help="print structured diagnostics")

    p_migrate = sub.add_parser("migrate", help="materialize a v1 policy as contract v2")
    p_migrate.add_argument("-c", "--config", default="dod.yaml")
    migration = p_migrate.add_mutually_exclusive_group(required=True)
    migration.add_argument("--dry-run", action="store_true", help="print migrated YAML")
    migration.add_argument("--write", action="store_true", help="replace the policy file")

    p_snip = sub.add_parser("snippet", help="print agent or CI integration configuration")
    p_snip.add_argument("target", choices=sorted([*SNIPPETS, "github"]))
    p_snip.add_argument(
        "--mode",
        choices=("shadow", "required"),
        default="required",
        help="GitHub check enforcement mode",
    )
    return parser


def _add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", default="dod.yaml")
    parser.add_argument("-b", "--base", default=None, help="override the candidate diff base")
    parser.add_argument("--ci", action="store_true", help="load policy from the trusted target ref")
    parser.add_argument("--policy-ref", help="target ref/SHA from which CI loads policy")


def _cmd_verify(args: argparse.Namespace) -> int:
    if args.json and args.format not in {"text", "json"}:
        print("dunnit: --json cannot be combined with a non-JSON --format", file=sys.stderr)
        return 2
    output_format = "json" if args.json else args.format
    verdict = verify(
        args.config,
        base=args.base,
        strict=True if args.strict else None,
        allow=tuple(args.allow),
        mode="ci" if args.ci else "local",
        policy_ref=args.policy_ref,
        bootstrap=args.bootstrap,
    )

    if output_format == "github":
        try:
            write_github_summary(verdict, os.environ.get("GITHUB_STEP_SUMMARY"))
        except OSError as exc:
            verdict.add(
                Evidence(
                    "report:github-summary",
                    Status.ERROR,
                    f"could not write GitHub job summary: {exc}",
                    rule_id="report.github-summary",
                    scan_complete=False,
                )
            )

    # Write only after formatter-side operations have had a chance to add
    # reporting errors, so the retained JSON always matches the exit outcome.
    if args.report:
        try:
            write_report(args.report, verdict)
        except OSError as exc:
            verdict.add(
                Evidence(
                    "report",
                    Status.ERROR,
                    f"could not write JSON report {args.report!r}: {exc}",
                    rule_id="report.write",
                    scan_complete=False,
                )
            )

    if output_format == "github":
        print(render_github(verdict), end="")
    elif output_format == "json":
        print(verdict.to_json())
    elif output_format == "junit":
        print(render_junit(verdict), end="")
    else:
        _print_text(verdict, quiet=args.quiet, color=_use_color())
    return outcome_exit_code(verdict.outcome)


def _cmd_doctor(args: argparse.Namespace) -> int:
    verdict = doctor(
        args.config,
        base=args.base,
        mode="ci" if args.ci else "local",
        policy_ref=args.policy_ref,
    )
    if args.json:
        print(verdict.to_json())
    else:
        _print_text(verdict, quiet=False, color=_use_color())
    return outcome_exit_code(verdict.outcome)


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path("dod.yaml")
    if path.exists() and not args.force and not args.dry_run:
        print("dod.yaml already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    detection = detect(Path.cwd(), args.preset)
    try:
        body = render_contract(detection)
    except ValueError as exc:
        print(
            f"dunnit: {exc}; configure a supported test/lint tool, resolve ambiguous "
            "manifests, or create dod.yaml from the v2 example in the README",
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        print(body, end="")
        return 0
    _write_text(path, body)
    commands = ", ".join(shlex.join(item.argv) for item in detection.checks)
    print(f"wrote dod.yaml with evidence-backed checks: {commands}")
    print("review and commit the policy; before the first commit use: dunnit verify --bootstrap")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    path = Path(args.config)
    try:
        contract = load_contract(path)
    except ContractError as exc:
        print(f"dunnit: {exc}", file=sys.stderr)
        return 2
    if contract.version == 2:
        print(f"{path} already uses contract version 2")
        return 0
    body = _render_migrated_contract(contract)
    if args.dry_run:
        print(body, end="")
    else:
        _write_text(path, body)
        print(f"migrated {path} to contract version 2")
    return 0


def _render_migrated_contract(contract: Contract) -> str:
    data: dict[str, object] = {"version": 2}
    if contract.base is not None:
        data["base"] = contract.base
    checks: list[dict[str, object]] = []
    for check in contract.checks:
        item: dict[str, object] = {"name": check.name}
        if check.argv is not None:
            item["argv"] = check.argv
        else:
            item["run"] = check.run or ""
        item.update(
            {
                "timeout": check.timeout,
                "dir": check.dir or ".",
                "env": check.env,
                "writes": check.writes,
            }
        )
        checks.append(item)
    data.update(
        {
            "checks": checks,
            "protected": contract.protected,
            "test_globs": contract.test_globs,
            "tamper": contract.tamper,
            "stubs": contract.stubs,
            "strict": contract.strict,
            "require": {
                "changed": contract.require.changed,
                "non_empty_diff": contract.require.non_empty_diff,
            },
        }
    )
    header = "# Migrated by dunnit; v1 `run` strings retain OS-native shell behavior.\n"
    return header + yaml.safe_dump(data, sort_keys=False, width=100)


def _write_text(path: Path, body: str) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _use_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _paint(text: str, code: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def _print_text(verdict: Verdict, quiet: bool, color: bool) -> None:
    for evidence in verdict.evidence:
        if quiet and evidence.status is Status.PASS:
            continue
        icon = _paint(ICONS[evidence.status], _COLORS[evidence.status], color)
        detail = evidence.detail.splitlines() or [""]
        print(f" {icon} {evidence.check}: {detail[0]}")
        if evidence.status is not Status.PASS:
            body = detail[1:_MAX_DETAIL_LINES]
            for line in body:
                print(f"     {line}")
            hidden = len(detail) - 1 - len(body)
            if hidden > 0:
                print(f"     … ({hidden} more lines)")
            if evidence.hint:
                print(f"   fix: {evidence.hint}")

    counts = verdict.counts
    parts = []
    for key, label in (("error", "errored"), ("fail", "failed"), ("warn", "warned")):
        if counts[key]:
            parts.append(f"{counts[key]} {label}")
    parts.append(f"{counts['pass']} passed")
    labels = {
        Outcome.PASS: "PASS — contract satisfied",
        Outcome.PASS_WITH_WARNINGS: "PASS WITH WARNINGS — contract satisfied",
        Outcome.FAIL: "FAIL — contract not satisfied",
        Outcome.ERROR: "ERROR — verification incomplete",
    }
    colors = {
        Outcome.PASS: "32",
        Outcome.PASS_WITH_WARNINGS: "33",
        Outcome.FAIL: "31",
        Outcome.ERROR: "35",
    }
    word = _paint(labels[verdict.outcome], colors[verdict.outcome], color)
    print(f"\nverdict: {word} ({', '.join(parts)})")


def _github_snippet(mode: str) -> str:
    # Pins are immutable commit SHAs; comments retain the human release tag for
    # Dependabot and manual update review. Proof dependencies must be supplied
    # by a trusted image or a baseline contract check so the snapshot comes first.
    continue_on_error = "    continue-on-error: true\n" if mode == "shadow" else ""
    branch_note = (
        "# Shadow mode is non-blocking. Review docs/threat-model.md before gating."
        if mode == "shadow"
        else (
            "# Prefer a target-branch or organization required-workflow ruleset.\n"
            "# Requiring this job name alone does not make a candidate-editable workflow "
            "immutable.\n"
            "# See docs/threat-model.md."
        )
    )
    template = """name: dunnit

on:
  pull_request:

permissions:
  contents: read

jobs:
  verify:
    name: verify
    runs-on: ubuntu-24.04
__CONTINUE__    steps:
      - name: Check out complete history
        uses: actions/checkout@__CHECKOUT__ # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Set up Python
        uses: actions/setup-python@__PYTHON__ # v7.0.0
        with:
          python-version: "3.14"
      # Pre-provision proof tools in a trusted runner image, or declare setup
      # as a trusted contract check. Do not execute candidate code before Dunnit.
      - name: Install pinned Dunnit
        run: python -m pip install "dunnit==__VERSION__"
      - name: Verify trusted PR policy
        run: >-
          dunnit verify --ci
          --policy-ref "${{ github.event.pull_request.base.sha }}"
          --base "${{ github.event.pull_request.base.sha }}"
          --format github --report dunnit-report.json
      - name: Retain Dunnit JSON report
        if: always()
        uses: actions/upload-artifact@__UPLOAD__ # v7.0.1
        with:
          name: dunnit-report
          path: dunnit-report.json
          if-no-files-found: error
          retention-days: 14

__NOTE__
"""
    # Updated through reviewed releases; never replace these with mutable tags.
    pins = {
        "__CONTINUE__": continue_on_error,
        "__VERSION__": __version__,
        "__CHECKOUT__": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "__PYTHON__": "5fda3b95a4ea91299a34e894583c3862153e4b97",
        "__UPLOAD__": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "__NOTE__": branch_note,
    }
    for marker, value in pins.items():
        template = template.replace(marker, value)
    return template


if __name__ == "__main__":
    sys.exit(main())
