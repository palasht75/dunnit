# Changelog

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
