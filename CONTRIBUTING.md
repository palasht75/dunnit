# Contributing to Dunnit

Thank you for improving Dunnit. Small, evidence-backed changes are easier to
review and safer for a verifier that teams may place on a merge boundary.

## Before opening a change

- Search existing issues and pull requests.
- For a detector change, include a minimized adversarial case and a nearby
  benign control. A pattern that only demonstrates a true positive is not
  enough.
- For contract, Git, or execution behavior, describe the fail-closed outcome and
  the operating systems/topologies affected.
- Report candidate-controlled pass bypasses privately through
  [SECURITY.md](SECURITY.md).
- Do not make external-adoption or benchmark claims without the evidence package
  required by [docs/validation.md](docs/validation.md).

## Development setup

Dunnit supports CPython 3.9–3.14. Use a maintained version for development and
let CI cover the matrix.

```bash
python -m venv .venv
# POSIX: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the standard checks from the repository root:

```bash
python -m pytest -q
python -m ruff check src tests
python -m mypy
python -m pytest --cov=dunnit --cov-report=term-missing --cov-fail-under=90
python -m build
python -m twine check dist/*
dunnit verify
```

The GitHub workflow also installs the built wheel in a clean virtual environment
and exercises the console entry point. Do not claim platform support based only
on a local run.

## Test-impact map

Identify the affected surfaces before implementing:

- Contract/schema changes: parser validation, duplicate keys, migration, doctor,
  init output, packaged JSON Schema parity, and v1 compatibility.
- Git/diff changes: tracked and untracked metadata, every diff-based rule,
  unusual paths/content, shallow/detached/unborn/worktree/monorepo scenarios, and
  Windows/CRLF behavior.
- Command changes: direct versus shell execution, environment/directory
  containment, output bounds/sanitization, timeout descendants, and declared
  writes.
- Verdict/report changes: all outcomes, exit codes, backward-compatible JSON,
  GitHub/JUnit rendering, completeness, privacy, and waiver provenance.
- CLI changes: direct tests in `tests/test_cli.py`, generated examples/snippets,
  help text, and documentation.

Every new check returns structured evidence and has tests. Every bug fix gets a
regression test that fails without the fix. Keep tests representative; do not
weaken or broadly rewrite nearby tests to make a change pass.

## Style and compatibility

- Support Python 3.9 syntax and use `from __future__ import annotations`.
- Keep the deterministic core free of network/LLM dependencies.
- Preserve the single runtime dependency policy unless a proposal justifies a
  change.
- Use modern built-in typing and a 100-character Ruff line length.
- Keep report changes additive within v1.x. Consumers are instructed to ignore
  unknown fields.
- Update the runtime parser, packaged schema, migration, examples, and docs
  together when the contract changes.
- Avoid unrelated formatting or refactors.

## Documentation and examples

Write “contract satisfied,” not “the agent definitely did it.” Clearly separate
research supporting the problem from evidence supporting Dunnit. Mark seeded
examples, unpublished results, limitations, and compatibility status honestly.

Examples must be explicit materialized contracts; presets are generation-time
convenience only. Pin action and package versions in CI examples. Do not include
private repository data, remotes, command output, or credentials.

## Pull requests

A pull request should contain:

- one focused change with its rationale;
- tests for production behavior, or an explanation for docs-only changes;
- exact commands run and their results;
- affected OS/Python/topology notes; and
- documentation/schema updates for public-interface changes.

Changes to `dod.yaml` or `.github/**` are intentionally protected by this
repository's own policy and require maintainer review. Do not weaken protection
to make the branch pass. A maintainer handling a legitimate protected-file
change may record a narrowly scoped `--allow tamper:protected-path` during
review; it must not be added permanently to agent instructions or required CI.

By contributing, you agree that your contribution is licensed under the
project's [MIT license](LICENSE).
