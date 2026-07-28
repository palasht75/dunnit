# dunnit

**Did the agent actually do it?**

[![PyPI](https://img.shields.io/pypi/v/dunnit)](https://pypi.org/project/dunnit/)
[![CI](https://github.com/palasht75/dunnit/actions/workflows/ci.yml/badge.svg)](https://github.com/palasht75/dunnit/actions)
[![Python](https://img.shields.io/pypi/pyversions/dunnit)](https://pypi.org/project/dunnit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

AI coding agents claim completion when work isn't done. They delete failing
tests, add `@pytest.mark.skip`, hardcode expected outputs, stub functions with
`NotImplementedError`, and then report success. Researchers call it
[reward hacking](https://arxiv.org/html/2511.21654) and have catalogued
[dozens of hack categories](https://arxiv.org/pdf/2604.11806); developers
ranked agents "lying about task completion" among the
[top pain points of 2026](https://dev.to/hadil/why-ai-agents-fail-in-production-and-how-engineering-teams-are-fixing-it-in-2026-job).

**dunnit is the trust layer between "the agent says done" and "it is done."**
You declare a machine-checkable definition of done in `dod.yaml`. `dunnit verify`
re-runs the proof itself and inspects the git diff for gaming. It never trusts
the transcript — and it won't let the agent rewrite the contract either.

```bash
pip install dunnit
dunnit init      # write dod.yaml
dunnit verify    # ✓ / ✗ evidence + exit code
```

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
 ✓ smoke: `python -c "import app"` exited 0
 ✗ tamper:added-skips: test_app.py: skip markers added: ['@pytest.mark.skip']
 ✗ tamper:removed-assertions: net assertions removed from tests (1 removed vs 0 added)
 ! stubs:todo-left-behind: app.py: # TODO fix later
 ! stubs:not-implemented: app.py: raise NotImplementedError

verdict: FAIL — it did not do it
$ echo $?
1
```

## What it checks

| Check | Catches | Result |
|---|---|---|
| `checks` commands | Failing tests/lint/build the agent claimed were green — dunnit executes them itself | FAIL |
| `tamper:deleted-tests` | Test files deleted from the diff | FAIL |
| `tamper:added-skips` | `pytest.mark.skip`, `unittest.skip`, `xfail`, `it.skip`, `describe.skip` added | FAIL |
| `tamper:removed-assertions` | Net loss of `assert` / `expect(` lines in test files | FAIL |
| `tamper:protected-path` | Agent touched files it must not — **including `dod.yaml` itself** | FAIL |
| `stubs:*` | `TODO`/`FIXME`, `raise NotImplementedError`, `except: pass` in changed code | WARN |

The last row matters: the obvious meta-hack is editing the verifier's config to
weaken the definition of done. `dod.yaml` is protected by default, so that diff
is itself a FAIL. Protect your CI and tests too:

## dod.yaml reference

```yaml
version: 1
base: origin/main        # git ref to diff against; default HEAD (uncommitted work)
checks:                  # proof commands, each must exit 0
  - name: tests
    run: pytest -q
    timeout: 600         # seconds, optional
  - name: lint
    run: ruff check .
protected:               # touching these fails verification (default: [dod.yaml])
  - dod.yaml
  - .github/**
  - tests/**             # optional: freeze tests entirely for agent changes
test_globs:              # what counts as a test file (defaults cover py/js)
  - tests/**
  - "**/test_*.py"
tamper: true             # diff-based test-gaming checks
stubs: true              # stub/TODO detection in changed code
```

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

Exit code 2 blocks Claude from stopping and feeds the failing evidence back
into its context, so it fixes the real problem instead of declaring victory.

### Cursor — make the rule explicit

`dunnit snippet cursor` → save as `.cursor/rules/dunnit.mdc`:

```markdown
---
description: Definition-of-done enforcement via dunnit
alwaysApply: true
---
Before you claim any task is complete, run `dunnit verify` and read the verdict.
- FAIL means the task is NOT done. Fix the real problem and re-run.
- Never edit dod.yaml, tests, or CI config to make verification pass.
- Never add skip markers or delete failing tests.
- Include the dunnit verdict output in your final summary.
```

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

verdict = verify("dod.yaml", base="origin/main")
if not verdict.passed:
    for e in verdict.evidence:
        print(e.check, e.status.value, e.detail)
```

### Machine-readable verdicts

`dunnit verify --json` emits structured evidence for orchestrators and bots:

```json
{
  "tool": "dunnit",
  "verdict": "fail",
  "evidence": [
    {"check": "tests", "status": "pass", "detail": "`pytest -q` exited 0"},
    {"check": "tamper:added-skips", "status": "fail", "detail": "test_app.py: ..."}
  ]
}
```

## Why not just CI?

CI runs your tests. It does not notice that the agent *deleted* the failing
test, skipped it, or edited the workflow so it never runs. dunnit checks the
diff for exactly those moves, protects its own contract, and gives agents a
verdict they can consume mid-task — before the PR is ever opened.

## Design principles

1. **Never trust the transcript.** Every claim is re-verified by execution.
2. **The contract is part of the attack surface.** `dod.yaml` is protected by default.
3. **Zero LLM in the core.** Deterministic, fast, free, offline. (An optional LLM judge is on the roadmap as an *additional* signal, never the only one.)
4. **Model- and framework-agnostic.** Works with Cursor, Claude Code, Codex, Devin, or hand-written diffs — dunnit only looks at the repo.

## Roadmap

pytest plugin · coverage non-regression · hardcoded-output detection ·
optional LLM judge · MCP server (agents self-verify) · signed verdict
attestations · JS/Go check packs. Issues and PRs welcome.

## License

MIT
