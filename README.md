# dunnit

**Offline, deterministic verification for repository-owned definitions of done.**

[![PyPI](https://img.shields.io/pypi/v/dunnit)](https://pypi.org/project/dunnit/)
[![CI](https://github.com/palasht75/dunnit/actions/workflows/ci.yml/badge.svg)](https://github.com/palasht75/dunnit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/dunnit)](https://pypi.org/project/dunnit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/palasht75/dunnit/blob/main/LICENSE)

> **Pre-release status:** `1.0.0a1` is the first trust-hardening release. The
> external-validation program is specified, but its results have not been
> published yet. Do not interpret the alpha label—or this README—as evidence
> that five independent teams rely on Dunnit today.

AI coding agents can produce a green test run after deleting a failing test,
adding a skip, focusing one passing test, changing test discovery, or leaving a
stub behind. Dunnit complements ordinary CI by re-running an explicit proof
contract and inspecting the candidate Git change for those integrity failures.

Dunnit answers a deliberately narrow question:

> Did the repository's trusted, machine-checkable contract run completely, and
> did the candidate change avoid the diff-integrity failures Dunnit knows how to
> detect?

It does **not** prove that an implementation matches unstated human intent, is
secure, or is free of bugs. A successful result is reported as **contract
satisfied**, not “the agent definitely did it.”

- Dunnit's core is offline after installation: no service, account, telemetry,
  or LLM. Repository-owned proof commands retain their own network behavior.
- Deterministic core with PyYAML as its only runtime dependency.
- Portable argument-vector checks plus an explicit shell-command escape hatch.
- Fail-closed Git and scan errors; incomplete verification cannot pass.
- Text, JSON, GitHub annotation, and JUnit output for humans and automation.

Research such as [EvilGenie](https://arxiv.org/html/2511.21654) and
[SpecBench](https://arxiv.org/html/2605.21384v1) motivates the reward-hacking
problem. It does not validate Dunnit itself. See [Validation](https://github.com/palasht75/dunnit/blob/main/docs/validation.md)
for the evidence bar this project uses.

## Local quick start

From the repository root:

```bash
# Once 1.0.0a1 is published; keep the version pinned in automation.
python -m pip install "dunnit==1.0.0a1"

# Preview only evidence-backed toolchain detection, then materialize it.
dunnit init --preset auto --dry-run
dunnit init --preset auto

# Review dod.yaml. Before its first commit, verification is explicitly bootstrap-only.
dunnit verify --bootstrap
git add dod.yaml
git commit -m "Add Dunnit verification contract"

# Check Git/history, policy trust, commands, paths, and schema after committing it.
dunnit doctor
dunnit verify
```

`init` never writes an empty contract. If detection is ambiguous or finds no
declared test harness, it exits with guidance instead of inventing a command.
Bootstrap is local-only and is labeled in the report; CI never accepts it.

## Five-minute GitHub required check

First commit a reviewed `dod.yaml` on the default branch. Then add the workflow
below as `.github/workflows/dunnit.yml`. The workflow intentionally installs
only the exact Dunnit release before verification. Do not install or build the
candidate project first: candidate-controlled build hooks could change the
worktree before Dunnit's initial snapshot. The workflow is intended for the
published `1.0.0a1` release; update the exact pin deliberately for later releases.

```yaml
name: dunnit

"on":
  pull_request:

permissions:
  contents: read

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Check out the candidate
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.14"

      - name: Install pinned Dunnit
        run: python -m pip install "dunnit==1.0.0a1"

      - name: Verify the pull request
        env:
          DUNNIT_BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: >-
          dunnit verify --ci
          --base "$DUNNIT_BASE_SHA"
          --policy-ref "$DUNNIT_BASE_SHA"
          --format github
          --report dunnit-report.json

      - name: Retain the machine-readable report
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: dunnit-report
          path: dunnit-report.json
          if-no-files-found: error
          retention-days: 14
```

The full-history checkout is intentional. In CI mode Dunnit resolves the target,
merge base, and candidate to full SHAs and loads the policy from the trusted base
SHA. Missing shallow history is an error, never a skipped warning.

Proof tools are not magically present on every hosted runner. Supply them in a
reviewed runner image/environment, or make dependency setup the first command in
the trusted contract so Dunnit captures the candidate before setup executes. For
example, a Python repository with a reviewed requirements file can materialize:

```yaml
checks:
  - name: dependencies
    argv: ["python", "-m", "pip", "install", "-r", "requirements-dev.txt"]
    timeout: 600
    writes: []
  - name: tests
    argv: ["python", "-m", "pytest", "-q"]
    timeout: 600
    writes: []
```

Keep `writes` empty when setup should not touch the repository; otherwise list
only its necessary generated paths. A contract command still executes untrusted
repository code and is not sandboxed, but its starting diff is already retained
and its worktree mutations are checked. Any candidate-controlled install, build,
hook, or script run before `dunnit verify` is outside Dunnit's captured evidence.

This repository-local workflow is a deployment template, not an immutable
enforcement boundary by itself. An ordinary `pull_request` can propose changes
to the workflow that invokes Dunnit; requiring only a familiar job name does
not prove that the trusted workflow definition ran. Where available, use a
target-branch or organization-owned required-workflow ruleset. Otherwise,
require CODEOWNERS review for `dod.yaml` and `.github/workflows/**`, require the
`dunnit / verify` status check, and disallow rule bypass. Dunnit's protected-path
finding can report a workflow edit only after a trusted workflow has invoked
Dunnit; it cannot defend an invocation that the candidate disabled or replaced.

After one or more shadow runs, configure those controls in the repository or
organization rules. Start with the [shadow workflow](https://github.com/palasht75/dunnit/blob/main/examples/github/dunnit-shadow.yml)
if you need to measure noise before blocking merges; the
[required workflow](https://github.com/palasht75/dunnit/blob/main/examples/github/dunnit-required.yml) is the copyable version
above.

## Contract v2

Presets only generate YAML. A package upgrade does not silently change a
materialized contract. Generation protects the concrete configuration,
manifest, workspace, and package-manager lockfiles used as detection evidence;
malformed or unmatched workspace declarations stop with guidance instead of
silently producing a partial monorepo contract.

```yaml
version: 2
base: origin/main

checks:
  - name: tests
    argv: ["python", "-m", "pytest", "-q"]
    timeout: 600
    dir: "."
    env: {CI: "1"}
    writes: []

protected:
  - dod.yaml
  - .github/**
  - pytest.ini

test_globs:
  - tests/**
  - "**/test_*.py"

require:
  non_empty_diff: true

tamper: true
stubs: true
strict: false
```

Each check uses exactly one execution form:

- `argv`: a non-empty list of non-empty strings, executed directly without a
  shell. This is the portable default.
- `run`: a non-empty string interpreted by the operating system's native shell.
  Use it only when pipes, redirects, expansion, or another shell feature is
  genuinely required.

`timeout` must be a positive integer number of seconds. `dir` and every `writes` glob
must stay inside the repository root. `writes` declares generated paths a proof
command may change; undeclared command mutations fail verification. Check names
are unique, slug-like identifiers and environment values are strings. Duplicate
YAML keys, unknown keys, unsupported versions, and no-op policies are errors.

See the [complete contract](https://github.com/palasht75/dunnit/blob/main/examples/dod.yaml),
the [ecosystem examples](https://github.com/palasht75/dunnit/tree/main/examples),
the packaged [contract v2 JSON Schema](https://github.com/palasht75/dunnit/blob/main/src/dunnit/dod-v2.schema.json),
and the [v1 migration guide](https://github.com/palasht75/dunnit/blob/main/docs/migration-v2.md).

## Verification model

Dunnit performs these operations in order:

1. Discover the Git worktree root and resolve the candidate and comparison base.
2. Load the policy from committed `HEAD` in local mode or the trusted target SHA
   in CI mode.
3. Capture and evaluate the candidate diff before any proof command runs.
4. Run proof commands with bounded output and process-tree timeouts.
5. Capture the diff again, retain all original findings, and reject undeclared
   command mutations.
6. Emit evidence and scan-completeness metadata.

The pre-command snapshot means a test command cannot hide an original skip or
deleted test by restoring files before inspection. Git/base failures, missing
policy in CI, incomplete path enumeration, unreadable required content, and
empty evidence produce an `error` outcome rather than a pass.

### What it checks

| Area | Examples | Default effect |
|---|---|---|
| Proof commands | Tests, lint, type checks, builds declared by the repository | Fail |
| Protected paths | Contract, workflow, runner configuration, team-selected paths | Fail |
| Test tampering | Deleted or renamed-away tests, added skips/focus, weakened or trivial assertions | Fail |
| Test configuration | Clear deselection/ignore/omit additions and broad-to-path test-command narrowing | Fail |
| Ambiguous test-config edits | Other changes requiring human review | Warning; fail in strict mode |
| Positive requirements | Required changed paths and non-empty candidate change | Fail |
| Stubs and suppression | TODO/FIXME, unimplemented code, swallowed errors, lint/type/coverage suppression | Warning; fail in strict mode |
| Command mutation | Tracked or relevant untracked changes outside declared `writes` | Fail |
| Verification completeness | Git resolution and path/content scan coverage | Error |

Patterns are scoped by file type across Python, JavaScript/TypeScript, Go,
Rust, JVM languages, Ruby, C#, and PHP. They are heuristics: use them as a
review signal, not a semantic proof engine.

### Outcomes and exit codes

| Outcome | Meaning | Exit code |
|---|---|---:|
| `pass` | Complete verification with no failures or warnings | 0 |
| `pass_with_warnings` | Complete verification with review findings only | 0 |
| `fail` | The contract or integrity policy was not satisfied | 1 |
| `error` | Contract, Git, execution, or scan infrastructure was incomplete | 2 |

`strict: true` promotes warnings to failures. `Verdict.passed` is false for
`fail`, `error`, and every incomplete verification.

## CLI

```text
dunnit init [--preset auto|python|node|go|rust|mixed] [--dry-run] [--force]
dunnit doctor [--json] [-c PATH] [-b REF] [--ci] [--policy-ref REF]
dunnit migrate --dry-run|--write [-c PATH]
dunnit verify [-c PATH] [-b REF] [--ci] [--policy-ref REF]
              [--format text|json|github|junit] [--report PATH]
              [--bootstrap] [--strict] [--allow CHECK] [-q]
dunnit snippet github [--mode shadow|required]
dunnit snippet claude|cursor|codex|pre-commit
```

`--json` remains as a compatibility alias for `--format json`. `--allow` is a
human-controlled, repeatable downgrade for reviewed `check` identifiers; do not put it in
agent instructions or the required CI command. While adopting the workflow,
resolve the trusted target to a full SHA and run both readiness checks against
that same identity:

```bash
BASE_SHA="$(git rev-parse origin/main)"
dunnit doctor --ci --policy-ref "$BASE_SHA" --base "$BASE_SHA"
```

Reports contain a schema version, tool version, policy origin and digest,
resolved Git SHAs, OS/Python/Git context, command results, waivers, duration,
and scan completeness. They do not include telemetry or the repository remote.
Treat command output as potentially sensitive before publishing an artifact.
See the [report and formatter contract](https://github.com/palasht75/dunnit/blob/main/docs/reporting.md) for compatibility and
privacy guidance.

## Python API

Existing call patterns remain valid; v1.0 adds explicit local/CI policy modes.

```python
from dunnit import Outcome, verify

base_sha = "0123456789abcdef0123456789abcdef01234567"  # trusted CI event value
verdict = verify(
    "dod.yaml",
    base=base_sha,
    mode="ci",
    policy_ref=base_sha,
    strict=True,
)

if verdict.outcome in {Outcome.FAIL, Outcome.ERROR}:
    for item in verdict.evidence:
        print(item.rule_id, item.status.value, item.path, item.line, item.detail)
```

Evidence may include a rule ID, path, line, stable fingerprint, severity,
duration, command exit code, and scan-completeness fields. Consumers should
ignore unknown report fields so additive schema revisions remain compatible.

## Trust boundary and limitations

CI mode assumes the target SHA supplied by the CI platform is trusted and that
the base branch protects its workflow and contract. Proof commands execute
repository code with the current user's permissions; Dunnit is not a sandbox.
Anyone who controls the trusted base, runner, Git executable, dependencies, or
required CI settings can control the result.

Dunnit cannot establish unstated requirements, review architecture, find every
security issue, eliminate flaky tests, or reliably infer whether an arbitrary
code change is malicious. Binary or oversized content that cannot be inspected
must be reported through scan-completeness evidence. Local mode is useful for
feedback but is not equivalent to a protected CI gate.

Read the complete [threat model](https://github.com/palasht75/dunnit/blob/main/docs/threat-model.md),
[support matrix and known limitations](https://github.com/palasht75/dunnit/blob/main/docs/support.md),
and [security policy](https://github.com/palasht75/dunnit/blob/main/SECURITY.md).

## False positives and reviewed exceptions

Diff heuristics trade recall against noise. Stubs and ambiguous configuration
changes are warnings by default, while clear test deselection and broad-to-path
test-command narrowing fail. Findings are aggregated and string literals are
guarded where practical. For a legitimate exception, a human can run:

```bash
dunnit verify --allow tamper:deleted-tests
```

The report records the waiver. Never bake it into the agent-facing or required
CI command. Please use the [false-positive issue form](https://github.com/palasht75/dunnit/issues/new?template=false-positive.yml)
with a minimized, non-sensitive reproduction.

## Validation status

No qualifying external-reliance result or “normal CI miss” is claimed until the
public protocol's gates are met. The project will publish seeded benchmark
results separately from real incidents, including false positives, errors, and
confidence intervals. The stable `1.0.0` release is withheld until the external
reliance gate is complete.

- [External pilot and evidence rules](https://github.com/palasht75/dunnit/blob/main/docs/validation.md)
- [Preregistered benchmark protocol](https://github.com/palasht75/dunnit/blob/main/benchmarks/PROTOCOL.md)
- [Release gates from alpha to stable](https://github.com/palasht75/dunnit/blob/main/docs/release-gates.md)

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
dunnit verify
```

See [CONTRIBUTING.md](https://github.com/palasht75/dunnit/blob/main/CONTRIBUTING.md)
before sending a change. Security reports belong in the private channel described
in [SECURITY.md](https://github.com/palasht75/dunnit/blob/main/SECURITY.md).

## License

[MIT](https://github.com/palasht75/dunnit/blob/main/LICENSE)
