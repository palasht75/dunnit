# Changelog

## 0.1.0 - 2026-07-28

Initial release.

- `dod.yaml` definition-of-done contracts
- Command checks: dunnit re-runs declared proof commands itself
- Tamper detection: deleted test files, added skip markers, net-removed assertions
- Stub detection: TODO/FIXME, `NotImplementedError`, swallowed exceptions in changed code
- `dunnit init` / `dunnit verify` CLI with `--json` verdicts and CI exit codes
- Python API: `from dunnit import verify`
