# Support matrix and known limitations

Dunnit is portable only to the extent that Git, subprocess, filesystem, and
shell behavior are exercised on each platform. The release CI matrix is the
source of truth: a platform is advertised for a release only after its jobs are
green on that release commit.

## Python and operating systems

| Runtime | Linux x64 | Windows x64 | macOS Intel | macOS ARM64 | Policy |
|---|---:|---:|---:|---:|---|
| CPython 3.9 | CI | CI | CI | — | Legacy compatibility through the v1.x line |
| CPython 3.10–3.14 | CI | CI | CI | 3.14 smoke | Supported |
| CPython 3.15 prerelease | Scheduled preview | — | — | — | Allowed to fail; not supported |
| PyPy or other interpreters | — | — | — | — | Not currently tested |

The matrix uses GitHub-hosted `ubuntu-latest`, `windows-latest`, and
`macos-15-intel` runners for CPython 3.9 through 3.14. A separate `macos-15`
job exercises CPython 3.14 on ARM64. Python 3.9 receives compatibility fixes,
but upstream Python support ended in 2025; teams should migrate to 3.10 or newer.

## Repository topology

These states are release-blocking test scenarios on every primary operating
system:

| Scenario | Intended behavior |
|---|---|
| Repository path contains spaces or Unicode | Paths remain intact; commands run from the discovered worktree root |
| LF or CRLF files | Diff rules inspect logical lines without changing the file |
| Detached HEAD | Candidate resolves normally through Git plumbing |
| Linked worktree | Root and common Git metadata resolve without assuming `.git` is a directory |
| Monorepo/nested invocation | Root policy is found first; check `dir` selects a contained workspace |
| Staged, unstaged, committed, and untracked changes | All candidate path states are represented |
| Initial repository with no commits | Local bootstrap uses the empty-tree baseline; CI rejects bootstrap |
| Shallow clone with sufficient explicit objects | Verification proceeds and records shallow state |
| Shallow clone missing the target or merge base | `error` with fetch guidance; never a skipped check |
| More than 1,000 untracked files | Every path is enumerated; there is no silent count cutoff |
| Binary, oversized, or unreadable file | Path policy still applies; content inspection is incomplete and verification errors |

Git file names cannot contain NUL or `/`. Names containing newlines or tabs are
preserved internally, although terminals and third-party report consumers may
render them differently.

## Command portability

Prefer `argv`:

```yaml
checks:
  - name: tests
    argv: ["python", "-m", "pytest", "-q"]
```

It executes without a shell and avoids quoting differences. `run` uses the
platform's native shell, so a command written for Bash may not work on Windows
and a PowerShell command may not work on Linux. Dunnit does not translate shell
syntax. On Windows, Dunnit resolves `argv[0]` through `PATH` and `PATHEXT`, so
generated `npm`, `pnpm`, `yarn`, and `bun` commands use their installed `.cmd`
shims without opting into a shell.

Proof tools must be pre-provisioned in a reviewed environment or installed by
an explicit setup check in the trusted contract. Dunnit does not infer setup at
verification time. Never run candidate-controlled setup before Dunnit takes its
initial snapshot. Set `dir` for a contained workspace, keep setup `writes`
empty unless it genuinely generates repository files, and use `env` only for
non-secret values that belong in the contract.

Timeout handling targets the command's process group/tree. Platform and child
process behavior can still delay teardown for code that escapes its process
group or delegates work to external services.

## Scanning limits

- Diff rules are deterministic heuristics, not parsers for every language.
- Binary content cannot be checked for textual skip, assertion, or stub markers.
- Content retention and command output are bounded. Truncation is represented in
  scan/report metadata; required inspection cannot silently pass.
- Rules only know configured/default `test_globs`. Generated or unconventional
  tests need explicit globs.
- `writes` authorizes command-generated paths. Keep it narrow and never use it
  to excuse candidate-authored changes.
- Sparse checkouts and pre-existing assume-unchanged/skip-worktree index flags
  are rejected because Git can hide candidate content in those states.
- Custom clean/smudge filters and hydrated Git LFS files whose worktree bytes
  differ from their index blob may be reported as candidate changes or
  incomplete content; use a clean, reviewed CI checkout and validate this in
  shadow mode before gating.
- Network filesystems, submodules, case-insensitive collisions, symlink-heavy
  repositories, and very large repositories receive best-effort support until
  dedicated corpus cases are published.
- Git LFS content is inspected only if it is present in the checkout; an LFS
  pointer is not the underlying file.

## Integrations

GitHub Actions is the first-class team integration. Other CI systems can call
the generic CLI with an immutable target SHA, but do not yet have maintained
copy-paste workflows. Claude, Cursor, Codex/`AGENTS.md`, and pre-commit snippets
are convenience feedback paths, not enforcement boundaries.

## Compatibility policy

- Contract v1 remains readable during the v1.x package series and is deprecated.
- Contract v2 is the stable policy format for the v1.x series.
- Additive report fields may appear in compatible releases. Consumers must
  ignore unknown keys and should inspect the report schema version.
- Breaking policy or report changes require a new major version and migration
  guidance.

See [Migration to contract v2](migration-v2.md) and the
[threat model](threat-model.md).
