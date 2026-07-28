"""dunnit CLI: `dunnit init`, `dunnit verify`, `dunnit snippet`."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

from dunnit import __version__
from dunnit.contract import ContractError
from dunnit.runner import verify
from dunnit.verdict import Status, Verdict

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
- Never add skip or .only markers, delete failing tests, or weaken assertions.
- Include the dunnit verdict output in your final summary.""",
    "codex": """\
# Codex / any AGENTS.md-reading agent — append to AGENTS.md
## Definition of done
This repo's completion contract is dod.yaml, enforced by `dunnit verify`.
- Before reporting a task complete, run `dunnit verify` and include its verdict.
- A FAIL verdict means the task is NOT done — fix the underlying problem and re-run.
- Never edit dod.yaml, protected files, or test/CI config to make verification pass.
- Never add skip or .only markers, delete tests, or weaken assertions.""",
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
_COLORS = {Status.PASS: "32", Status.FAIL: "31", Status.WARN: "33"}
_MAX_DETAIL_LINES = 30


def main(argv=None) -> int:
    # Windows consoles may not be UTF-8; degrade gracefully instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(prog="dunnit", description="Did the agent actually do it?")
    parser.add_argument("--version", action="version", version=f"dunnit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a dod.yaml tailored to this repo's toolchain")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing dod.yaml")

    p_verify = sub.add_parser("verify", help="verify work against the contract")
    p_verify.add_argument("-c", "--config", default="dod.yaml")
    p_verify.add_argument("-b", "--base", default=None, help="override the git diff base ref")
    p_verify.add_argument("--json", action="store_true", help="print machine-readable verdict")
    p_verify.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p_verify.add_argument(
        "--allow", action="append", default=[], metavar="CHECK",
        help="downgrade a failing check to a warning, e.g. --allow tamper:deleted-tests "
             "(for human-reviewed exceptions; repeatable)",
    )
    p_verify.add_argument("-q", "--quiet", action="store_true", help="only print failures/warnings")

    p_snip = sub.add_parser("snippet", help="print integration config for your agent/CI")
    p_snip.add_argument("target", choices=sorted(SNIPPETS))

    args = parser.parse_args(argv)

    if args.command == "init":
        return _cmd_init(force=args.force)
    if args.command == "snippet":
        print(SNIPPETS[args.target])
        return 0

    try:
        verdict = verify(
            args.config,
            base=args.base,
            strict=True if args.strict else None,
            allow=tuple(args.allow),
        )
    except ContractError as exc:
        print(f"dunnit: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(verdict.to_json())
    else:
        _print_text(verdict, quiet=args.quiet, color=_use_color())
    return 0 if verdict.passed else 1


# ---------------------------------------------------------------- verify output

def _use_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _paint(text: str, code: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def _print_text(verdict: Verdict, quiet: bool, color: bool) -> None:
    for ev in verdict.evidence:
        if quiet and ev.status is Status.PASS:
            continue
        icon = _paint(ICONS[ev.status], _COLORS[ev.status], color)
        detail = ev.detail.splitlines() or [""]
        print(f" {icon} {ev.check}: {detail[0]}")
        if ev.status is not Status.PASS:
            body = detail[1:_MAX_DETAIL_LINES]
            for ln in body:
                print(f"     {ln}")
            hidden = len(detail) - 1 - len(body)
            if hidden > 0:
                print(f"     … ({hidden} more lines)")
            if ev.hint:
                print(f"   fix: {ev.hint}")

    c = verdict.counts
    parts = [f"{c['fail']} failed"] if c["fail"] else []
    if c["warn"]:
        parts.append(f"{c['warn']} warned")
    parts.append(f"{c['pass']} passed")
    word = "PASS — it did it" if verdict.passed else "FAIL — it did not do it"
    word = _paint(word, "32" if verdict.passed else "31", color)
    print(f"\nverdict: {word} ({', '.join(parts)})")


# ------------------------------------------------------------------------- init

def _cmd_init(force: bool) -> int:
    path = Path("dod.yaml")
    if path.exists() and not force:
        print("dod.yaml already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    checks = _detect_checks(Path.cwd())
    path.write_text(_render_contract(checks), encoding="utf-8")
    if checks:
        detected = ", ".join(f"`{run}`" for _, run in checks)
        print(f"wrote dod.yaml with detected checks: {detected}")
    else:
        print("wrote dod.yaml — no toolchain detected, edit the checks section")
    print("review your definition of done, then run: dunnit verify")
    return 0


def _detect_checks(cwd: Path) -> list[tuple[str, str]]:
    """Best-effort toolchain detection. Only suggests commands the repo's own
    files advertise; the user reviews the result either way."""
    checks: list[tuple[str, str]] = []
    if any((cwd / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini")):
        checks.append(("tests", "python -m pytest -q"))
        py_cfg = ""
        for f in ("pyproject.toml", "setup.cfg"):
            with contextlib.suppress(OSError):
                py_cfg += (cwd / f).read_text(encoding="utf-8")
        if "ruff" in py_cfg:
            checks.append(("lint", "python -m ruff check ."))
    try:
        scripts = json.loads((cwd / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError):
        scripts = {}
    if isinstance(scripts, dict):
        if "test" in scripts:
            checks.append(("node-tests", "npm test --silent"))
        if "lint" in scripts:
            checks.append(("node-lint", "npm run lint --silent"))
    if (cwd / "go.mod").exists():
        checks.append(("go-tests", "go test ./..."))
        checks.append(("go-vet", "go vet ./..."))
    if (cwd / "Cargo.toml").exists():
        checks.append(("rust-tests", "cargo test --quiet"))
    return checks


def _render_contract(checks: list[tuple[str, str]]) -> str:
    lines = [
        "# dunnit definition of done — docs: https://github.com/palasht75/dunnit",
        "# `dunnit verify` re-runs these checks and inspects the git diff for test-gaming.",
        "version: 1",
        "# base: origin/main   # git ref to diff against (default: HEAD = uncommitted work)",
        "checks:",
    ]
    if checks:
        for name, run in checks:
            lines += [f"  - name: {name}", f"    run: {run}"]
    else:
        lines += [
            "  # no toolchain auto-detected — declare your proof commands, e.g.:",
            "  # - name: tests",
            "  #   run: make test",
        ]
    lines += [
        "protected:             # touching these fails verification",
        "  - dod.yaml",
        "  - .github/**",
        "tamper: true           # deleted/skipped/focused tests, weakened assertions, config gaming",
        "stubs: true            # warn on TODOs / stubbed code left in the diff",
        "# strict: true         # promote warnings to failures",
        "# require:",
        "#   changed: [tests/**]   # done must include a test change",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
