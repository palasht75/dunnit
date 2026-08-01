# Changelog

## 1.0.0a1 - 2026-08-01

Trust-root hardening alpha. This prerelease is for corpus validation and
external pilots; stable 1.0.0 remains gated on five independent repositories.

- Load local policy from committed `HEAD` and CI policy from the resolved
  target commit; Git, policy, shallow-history, and incomplete-scan failures now
  produce a fail-closed `error` outcome.
- Capture candidate integrity before proof commands and audit post-command
  workspace changes against explicit v2 `writes` paths.
- Add contract v2, shell-free `argv`, strict types and duplicate-key rejection,
  bounded/sanitized process output, and process-tree timeout termination.
- Add `init` presets, `doctor`, v1 migration, JSON Schema, GitHub/JUnit output,
  versioned JSON reports, and a pinned GitHub required-check workflow.
- Add cross-platform Git topology coverage, a public benchmark protocol, and
  honest threat-model, validation, migration, and pilot documentation.
- Enforce exact SHA-1/SHA-256 object-ID widths, resolve Windows `PATHEXT`
  command shims, attempt descendant cleanup even after a command root exits,
  and materialize every detector-evidence manifest and lockfile as protected.

## 0.3.0 - 2026-07-28

Detection depth and agent-feedback release.

- **Untracked files are now inspected.** Brand-new files the agent never
  staged were previously invisible to every diff check.
- **Renamed-away tests are deleted tests**: `git mv tests/test_x.py x.bak`
  now fails `tamper:deleted-tests`.
- **New tamper checks**: `tamper:focused-tests` (`.only`, `fit`, `fdescribe`
  silently deselect the rest of the suite), `tamper:trivial-assertions`
  (`assert True`, `expect(true).toBe(true)`), and `tamper:test-config` /
  `tamper:test-config-changed` (deselection patterns like `--ignore`,
  `--deselect`, `collect_ignore`, `norecursedirs`, `testPathIgnorePatterns`
  added to pytest/jest/vitest/mocha/playwright/cypress configs or conftest).
- **Multi-language skip detection**, scoped by file extension: Python, JS/TS,
  Go (`t.Skip`), Rust (`#[ignore]`), JVM (`@Disabled`/`@Ignore`), Ruby,
  C#, PHP. Extension scoping also kills cross-language false positives.
- **String-literal guard**: markers inside string literals no longer flag —
  a test suite that tests skip-detection doesn't flag itself.
- **Stub detection expanded**: multi-line `except:`/`pass`, empty JS
  `catch {}`, Rust `todo!()`/`unimplemented!()`, checker suppressions
  (`# noqa`, `# type: ignore`, `eslint-disable`, `@ts-ignore`) and coverage
  exclusions (`# pragma: no cover`) in changed code; findings aggregated
  per file.
- **`require:` contract section**: `changed: [globs]` ("done includes a test
  change") and `non_empty_diff: true` (the agent must have done *something*).
- **`strict` mode** (`strict: true` or `--strict`): warnings become failures.
- **`--allow CHECK`**: human escape hatch that downgrades a reviewed failing
  check to a warning; deliberately CLI-only so agents can't self-grant it.
- **Hints on every failure** (`fix: ...` in text output, `hint` in JSON) so
  agents consuming verdicts are steered to the honest fix.
- **Verdict upgrades**: `summary` counts, `meta` (base ref, files changed),
  tool version in `--json`; full failing-command output shown in text mode.
- **CLI**: color output (`NO_COLOR` respected), `-q/--quiet`,
  `dunnit init` auto-detects pytest/ruff/npm/go/cargo, `--force` overwrite,
  `dunnit snippet codex` for AGENTS.md-driven agents.
- **Per-check `dir` and `env`** in dod.yaml; command duration reported.
- **Strict contract schema**: unknown keys in dod.yaml are errors, so a typo
  like `protectd:` can't silently disable protection.
- Proper `**` glob semantics (gitignore-style) replacing fnmatch quirks;
  renames tracked with old path; protected-path check covers moves and
  newly created files (the contract's own first commit is exempt).

## 0.2.0 - 2026-07-28

- **Protected paths**: `protected:` globs fail verification when touched.
  `dod.yaml` is protected by default — agents can no longer weaken their own
  definition of done. Add `.github/**` or `tests/**` to freeze more.
- `dunnit snippet {claude,cursor,github,pre-commit}`: paste-ready integration
  configs (Claude Code Stop hook, Cursor rule, GitHub Actions step, pre-commit).
- `dunnit verify --base <ref>`: override the diff base from the CLI (CI-friendly).
- `verify()` Python API gained the same `base=` parameter.
- New docs: rewritten README with worked examples, AGENTS.md for coding agents.

## 0.1.0 - 2026-07-28

Initial release.

- `dod.yaml` definition-of-done contracts
- Command checks: dunnit re-runs declared proof commands itself
- Tamper detection: deleted test files, added skip markers, net-removed assertions
- Stub detection: TODO/FIXME, `NotImplementedError`, swallowed exceptions in changed code
- `dunnit init` / `dunnit verify` CLI with `--json` verdicts and CI exit codes
- Python API: `from dunnit import verify`
