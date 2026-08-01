# Migrating a contract from v1 to v2

Contract v1 remains readable, with a deprecation warning, throughout Dunnit
1.x. Migrate deliberately so the repository—not a future preset update—owns the
exact commands that gate it.

## Recommended rollout

1. Pin the Dunnit version on a branch and run `dunnit doctor` against the v1
   policy.
2. Preview the mechanical rewrite:

   ```bash
   dunnit migrate --dry-run
   ```

3. Review command tokenization. Convert simple commands to `argv`; retain `run`
   only for intentional shell features.
4. Materialize the change:

   ```bash
   dunnit migrate --write
   dunnit doctor
   dunnit verify
   ```

5. Commit the policy change through the repository's normal protected-file
   review. Run the GitHub check in shadow mode before making it required.

`--dry-run` never writes a file. `--write` updates the selected contract and
refuses an invalid result. Review the diff; migration does not infer new proof
requirements or silently apply a preset.

## Schema changes

| v1 | v2 | Action |
|---|---|---|
| `version: 1` | `version: 2` | Required for new behavior |
| `checks[].run` | `checks[].argv` or `checks[].run` | Prefer `argv` when no shell feature is needed |
| Implicit shell command | Explicit shell-free or native-shell form | Check quoting on every supported OS |
| No command write declaration | `checks[].writes` | Declare only expected generated paths |
| Permissive scalar coercion in older releases | Exact types | Quote environment values and fix booleans/integers used as strings |
| Some no-op policies accepted | Meaningful evidence required | Add a proof or integrity check |

V2 also requires unique slug-like check names, positive integer timeouts,
contained working directories, non-empty argument strings, string environment
keys and values, contained write globs, and no duplicate YAML keys.

Editors and pre-commit validators can use the packaged
[contract v2 JSON Schema](../src/dunnit/dod-v2.schema.json). Runtime validation
remains authoritative for cross-item constraints and filesystem containment.

## Example

Before:

```yaml
version: 1
checks:
  - name: tests
    run: python -m pytest -q
    timeout: 600
    env: {CI: "1"}
protected: [dod.yaml, .github/**]
tamper: true
stubs: true
```

After:

```yaml
version: 2
checks:
  - name: tests
    argv: ["python", "-m", "pytest", "-q"]
    timeout: 600
    env: {CI: "1"}
    writes: []
protected: [dod.yaml, .github/**]
tamper: true
stubs: true
```

This conversion is behaviorally equivalent except that it bypasses shell
parsing. A v1 command such as `pytest -q | tee test.log` must remain a `run`
string unless it is replaced by a portable wrapper script owned by the repo.

## CI trust migration

Legacy CI often checks out shallow history and compares to a branch name from
the candidate environment. The v1.0 workflow instead:

- checks out full history or explicitly fetches the immutable base objects;
- passes the pull request base SHA as `--base`;
- passes the same trusted SHA as `--policy-ref`;
- uses `--ci` so a missing base policy is an error; and
- retains a structured report even when the gate fails.

Use the [required GitHub example](../examples/github/dunnit-required.yml) as the
starting point. Run `dunnit doctor --ci --policy-ref <base-sha>` while adapting
another CI platform.

## Rollback

If v2 exposes an adoption problem, keep the Dunnit package pinned and restore
the reviewed v1 policy. Do not weaken protected paths or add a blanket
`--allow` to keep a required check green. File a minimized report so the issue
can become a regression case.
