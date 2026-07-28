# dunnit

**Did the agent actually do it?**

AI coding agents claim completion when work isn't done: they delete failing
tests, add `skip` markers, hardcode expected outputs, stub functions and
declare victory. Research calls it reward hacking. You call it Tuesday.

`dunnit` is a tamper-evident verifier for agent work. You declare your
definition of done in `dod.yaml`; `dunnit verify` re-runs the proof itself
and inspects the diff for test-gaming. **It never trusts the transcript.**

```bash
pip install dunnit
dunnit init      # write dod.yaml
dunnit verify    # ✓ or ✗, exit code for CI
```

## What it checks (v0.1)

- **Commands** — declared proof commands (tests, lint, build) must exit 0,
  executed by dunnit, not quoted from the agent's chat log.
- **Tamper** — git diff of test files: deleted tests, added skip markers,
  net-removed assertions → hard FAIL.
- **Stubs** — changed code scanned for `TODO`/`FIXME`, `NotImplementedError`,
  swallowed exceptions → WARN.

## dod.yaml

```yaml
version: 1
base: origin/main        # diff against this ref (default: HEAD, i.e. uncommitted work)
checks:
  - name: tests
    run: pytest -q
  - name: lint
    run: ruff check .
tamper: true
stubs: true
```

## Use it everywhere agents work

- **CI** — `dunnit verify` exits non-zero on FAIL; `--json` for machines.
- **Claude Code stop-hook / Cursor rules** — make the agent run
  `dunnit verify` before it's allowed to say "done".
- **Python** — `from dunnit import verify; verify("dod.yaml").passed`

## Roadmap

pytest plugin · coverage non-regression · hardcoded-output detection ·
optional LLM judge · MCP server (agents self-verify) · signed verdict
attestations · JS/Go check packs.

MIT licensed.
