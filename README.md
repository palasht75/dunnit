# dunnit

**Did the agent actually do it?**

[![PyPI](https://img.shields.io/pypi/v/dunnit)](https://pypi.org/project/dunnit/)
[![CI](https://github.com/palasht75/dunnit/actions/workflows/ci.yml/badge.svg)](https://github.com/palasht75/dunnit/actions)
[![Python](https://img.shields.io/pypi/pyversions/dunnit)](https://pypi.org/project/dunnit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

AI coding agents claim completion when work isn't done. They delete failing
tests, add `@pytest.mark.skip`, focus a single passing test with `.only`,
hardcode expected outputs, stub functions with `NotImplementedError`, edit the
test config so failing tests never run — and then report success. Researchers
call it [reward hacking](https://arxiv.org/html/2511.21654) and have measured
it across [frontier models](https://arxiv.org/abs/2605.02964) and
[long-horizon coding tasks](https://arxiv.org/html/2605.21384v1); developers
ranked agents "lying about task completion" among the
[top pain points of 2026](https://dev.to/hadil/why-ai-agents-fail-in-production-and-how-engineering-teams-are-fixing-it-in-2026-job).

**dunnit is the trust layer between "the agent says done" and "it is done."**
You declare a machine-checkable definition of done in `dod.yaml`. `dunnit verify`
re-runs the proof itself and inspects the git diff — including untracked
files — for gaming. It never trusts the transcript, and it won't let the agent
rewrite the contract either.

```bash
pip install dunnit
dunnit init      # writes dod.yaml, auto-detecting your toolchain
dunnit verify    # ✓ / ✗ evidence + exit code
```

Zero dependencies beyond PyYAML. No LLM, no network, deterministic.

## 60-second demo

An agent is asked to fix `mul()`. Instead it skips the failing test and stubs
the code:

```diff
--- a/test_app.py
+++ b/test_app.py
+import pytest
+@pytest.mark.skip
 def test_mul():
-    assert mul(2, 3) == 6
+    pass
--- a/app.py
+++ b/app.py
 def mul(a, b):
-    return a * b
+    # TODO fix later
+    raise NotImplementedError
```

The agent reports: *"All tests pass ✅ Task complete."* dunnit disagrees:

```text
$ dunnit verify
 ✓ smoke: `python -c "import app"` exited 0 in 0.1s
 ✓ protected: no protected files touched
 ✗ tamper:added-skips: test_app.py: skip markers added: ['@pytest.mark.skip']
   fix: Remove the skip/xfail marker and fix the code so the test passes.
 ✗ tamper:removed-assertions: net assertions removed from tests (1 removed vs 0 added)
   fix: Restore the removed assertions — weakening tests does not complete the task.
 ! stubs:not-implemented: app.py: raise NotImplementedError
   fix: Implement the function — a stub that raises is not a completed task.
 ! stubs:todo-left-behind: app.py: # TODO fix later
   fix: Finish the work the marker refers to, or drop it if it is stale.

verdict: FAIL — it did not do it (2 failed, 2 warned, 2 passed)
$ echo $?
1
```

Every failure carries a `fix:` hint, so an agent consuming the verdict is
steered toward fixing the real problem instead of the checker.

## What it checks

**Proof commands** (FAIL): every command under `checks:` must exit 0. dunnit
runs them itself — "the tests passed" in a transcript is not evidence.

**Tamper detection** (FAIL) — the diff is inspected for classic test-gaming:

| Check | Catches |
|---|---|
| `tamper:deleted-tests` | Test files deleted **or renamed to non-test paths** |
| `tamper:added-skips` | Skip/xfail markers added: `pytest.mark.skip`, `unittest.skip`, `it.skip`, `xit`, `t.Skip()`, `#[ignore]`, `@Disabled`, `markTestSkipped`, … |
| `tamper:focused-tests` | `.only` / `fit` / `fdescribe` added — focusing one test silently deselects the rest of the suite |
| `tamper:removed-assertions` | Net loss of assertion lines in test files |
| `tamper:trivial-assertions` | `assert True`, `expect(true).toBe(true)` added to tests |
| `tamper:test-config` | Deselection added to runner config: `--ignore`, `--deselect`, `collect_ignore`, `norecursedirs`, `testPathIgnorePatterns`, coverage `omit`, … |
| `tamper:test-config-changed` | Any other edit to a dedicated test config (WARN — review it) |
| `tamper:protected-path` | Protected files touched, moved, or created — **including `dod.yaml` itself** |

**Positive requirements** (FAIL): `require:` asserts the work is actually
present — e.g. "done includes a test change" or "the diff must not be empty".

**Stub detection** (WARN, or FAIL with `strict`): `TODO`/`FIXME` left in
changed code, `NotImplementedError` / `todo!()` stubs, swallowed exceptions
(`except: pass`, empty `catch {}`), and suppression comments (`# noqa`,
`# type: ignore`, `eslint-disable`, `@ts-ignore`, `# pragma: no cover`) added
in new code.

Detection patterns are scoped by file extension across Python, JS/TS, Go,
Rust, JVM (Java/Kotlin/Scala), Ruby, C#, and PHP — `fit(` is a Jasmine focus
marker in a `.ts` test but curve-fitting in a `.py` file. Markers inside
string literals are ignored, so a test suite that *tests* skip-detection
doesn't flag itself.

The meta-hack is covered too: editing the verifier's config to weaken the
definition of done. `dod.yaml` is protected by default, so that diff is
itself a FAIL. Untracked files are inspected as well — a brand-new stubbed
file the agent never staged still shows up in the verdict.

## dod.yaml reference

```yaml
version: 1
base: origin/main        # git ref to diff against; default HEAD (uncommitted work)
checks:                  # proof commands, each must exit 0
  - name: tests
    run: pytest -q
    timeout: 600         # seconds, optional (default 600)
  - name: backend-lint
    run: ruff check .
    dir: backend         # optional working directory
    env: { CI: "1" }     # optional extra environment
protected:               # touching these fails verification (default: [dod.yaml])
  - dod.yaml             # globs are gitignore-style: no slash = any depth,
  - .github/**           # leading / anchors to the repo root, ** crosses dirs
  - tests/**             # optional: freeze tests entirely for agent changes
require:                 # the work must actually be present
  changed: [tests/**]    # each glob must match >=1 changed file ("done includes a test")
  non_empty_diff: true   # fail if the diff is empty (agent did nothing)
test_globs:              # what counts as a test file (defaults cover py/js/go/rust/jvm/ruby)
  - tests/**
  - "**/test_*.py"
tamper: true             # diff-based test-gaming checks
stubs: true              # stub/suppression detection in changed code
strict: false            # true: promote warnings to failures
```

Unknown keys are contract errors — a typo can't silently disable a
protection.

## CLI

```text
dunnit init [--force]        write dod.yaml, auto-detecting pytest/npm/go/cargo
dunnit verify                verify and print ✓/✗ evidence; exit 0 pass / 1 fail / 2 contract error
  -c, --config PATH          contract file (default dod.yaml)
  -b, --base REF             override the diff base (CI: the merge base)
  --json                     machine-readable verdict
  --strict                   treat warnings as failures
  --allow CHECK              downgrade a failing check to a warning (repeatable)
  -q, --quiet                only print failures and warnings
dunnit snippet TARGET        print integration config: claude | cursor | codex | github | pre-commit
```

`--allow` is the human escape hatch: when you *reviewed* a test deletion and
it's legitimate, run `dunnit verify --allow tamper:deleted-tests` yourself.
It's a CLI flag rather than a `dod.yaml` key on purpose — the pinned command
in CI or your agent hook doesn't include it, so the agent can't grant itself
exceptions.

## Use it where agents work

### Claude Code — block "done" until it's true

`dunnit snippet claude` prints this; add it to `.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "dunnit verify >&2 || exit 2" } ] }
    ]
  }
}
```

Exit code 2 blocks Claude from stopping and feeds the failing evidence —
including the `fix:` hints — back into its context, so it fixes the real
problem instead of declaring victory.

### Cursor — make the rule explicit

`dunnit snippet cursor` → save as `.cursor/rules/dunnit.mdc`. The rule tells
the agent to run `dunnit verify` before claiming completion and never to
touch the contract, tests, or CI to make it pass.

### Codex / AGENTS.md agents

`dunnit snippet codex` prints a "Definition of done" section to append to
your `AGENTS.md` — any agent that reads `AGENTS.md` (Codex, Claude Code,
Cursor, …) will treat `dunnit verify` as the completion gate.

Belt-and-braces: even if the agent ignores the rule, running `dunnit verify`
in CI (below) catches the gamed diff before merge.

### GitHub Actions — gate the merge

`dunnit snippet github`:

```yaml
      - name: dunnit verify
        run: |
          pip install dunnit
          git fetch origin ${{ github.base_ref || 'main' }}
          dunnit verify --base origin/${{ github.base_ref || 'main' }} --json
```

### pre-commit

`dunnit snippet pre-commit` prints a local hook that runs on every commit.

### Python API

```python
from dunnit import verify

verdict = verify("dod.yaml", base="origin/main", strict=True)
if not verdict.passed:
    for e in verdict.evidence:
        print(e.check, e.status.value, e.detail, e.hint)
```

### Machine-readable verdicts

`dunnit verify --json` emits structured evidence for orchestrators and bots:

```json
{
  "tool": "dunnit",
  "version": "0.3.0",
  "verdict": "fail",
  "summary": {"pass": 2, "fail": 1, "warn": 1},
  "evidence": [
    {"check": "tests", "status": "pass", "detail": "`pytest -q` exited 0 in 3.1s"},
    {"check": "tamper:added-skips", "status": "fail", "detail": "test_app.py: ...",
     "hint": "Remove the skip/xfail marker and fix the code so the test passes."}
  ],
  "meta": {"base": "origin/main", "files_changed": 3}
}
```

## False positives

Diff heuristics are heuristics. dunnit keeps the noise down by design:

- Patterns are extension-scoped, so language idioms don't cross-contaminate.
- Markers inside string literals are ignored.
- Stub findings are aggregated per file, so a big diff can't flood the verdict.
- Stub/suppression findings are WARN by default; opt into `strict` when ready.
- For reviewed exceptions, a human runs `--allow <check>` — the agent-facing
  pinned command never includes it.

If you hit a false positive these don't cover, please open an issue — the
detection corpus is the product.

## Why not just CI?

CI runs your tests. It does not notice that the agent *deleted* the failing
test, skipped it, focused its one passing sibling, or edited the runner
config so it never executes. dunnit checks the diff for exactly those moves,
protects its own contract, and gives agents a verdict they can consume
mid-task — before the PR is ever opened.

## Design principles

1. **Never trust the transcript.** Every claim is re-verified by execution.
2. **The contract is part of the attack surface.** `dod.yaml` is protected by
   default, and unknown keys are errors.
3. **Zero LLM in the core.** Deterministic, fast, free, offline. (An optional
   LLM judge is on the roadmap as an *additional* signal, never the only one.)
4. **Model- and framework-agnostic.** Works with Cursor, Claude Code, Codex,
   Devin, or hand-written diffs — dunnit only looks at the repo.
5. **Verdicts teach.** Every failure carries a hint pointing at the honest
   fix, because the consumer of a verdict is often the agent itself.

## Roadmap

pytest plugin · coverage non-regression · hardcoded-output detection ·
optional LLM judge · MCP server (agents self-verify) · signed verdict
attestations. Issues and PRs welcome.

## License

MIT
