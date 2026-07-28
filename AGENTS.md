# AGENTS.md — dunnit

Guidance for AI coding agents (Codex, Claude Code, Cursor, etc.) working in this repo.

## What this project is

`dunnit` is a pip-installable verifier for AI agent work. Users declare a
definition of done in `dod.yaml`; `dunnit verify` re-runs the declared proof
commands and inspects the git diff (tracked + untracked) for test-gaming
(deleted/renamed-away tests, added skips or `.only` focus markers, removed or
trivialized assertions, test-config deselection, touched protected files,
stubbed code). Output is ✓/✗ evidence with per-failure hints plus an exit
code (0 pass, 1 fail, 2 contract error) or `--json`.

## Layout

- `src/dunnit/contract.py` — parse/validate `dod.yaml` into `Contract` (strict schema)
- `src/dunnit/gitdiff.py` — diff collection (`FileDiff`, incl. untracked files) and `**` glob matching
- `src/dunnit/lines.py` — shared line heuristics (string-literal guard)
- `src/dunnit/checks/commands.py` — run proof commands (per-check `dir`/`env`)
- `src/dunnit/checks/tamper.py` — test-gaming detection, extension-scoped patterns (FAIL-level)
- `src/dunnit/checks/protected.py` — protected-path enforcement, rename-aware (FAIL-level)
- `src/dunnit/checks/stubs.py` — stub/suppression detection, aggregated (WARN-level)
- `src/dunnit/checks/require.py` — positive requirements: `require.changed`, `non_empty_diff` (FAIL-level)
- `src/dunnit/runner.py` — orchestrates checks into a `Verdict`; applies `strict`/`--allow` policy
- `src/dunnit/verdict.py` — `Verdict`/`Evidence`/`Status` models (hints, summary, meta)
- `src/dunnit/cli.py` — `init` (toolchain autodetect), `verify`, `snippet` subcommands
- `tests/` — pytest suite; `conftest.py` provides temp git repo + commit fixtures

## Commands

```bash
pip install -e .[dev]          # setup
python -m pytest -q            # tests
python -m ruff check src tests # lint
dunnit verify                  # the definition of done for this repo
```

## Definition of done

A change is done only when `dunnit verify` passes (it runs tests + lint and
checks your diff). Do not edit `dod.yaml`, `.github/**`, or weaken tests to
make it pass — those diffs fail verification by design.

## Conventions

- Python ≥3.9, `from __future__ import annotations`, modern typing (`list[str]`, `X | None`)
- Zero runtime dependencies beyond `pyyaml`; keep the core LLM-free and deterministic
- Every new check returns `Evidence` and gets a test in `tests/`
- New CLI behavior needs a test in `tests/test_cli.py`
- Line length 100 (ruff)

## Releasing

Bump version in `pyproject.toml` + `src/dunnit/__init__.py`, update
`CHANGELOG.md`, push, create a GitHub release tag `vX.Y.Z` — the `publish.yml`
workflow builds and publishes to PyPI via trusted publishing.
