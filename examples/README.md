# Contract examples

These files show materialized contract v2 policies. They are starting points,
not dynamic presets: copy one to the repository root as `dod.yaml`, remove
irrelevant checks, and review every command and protected path.

| Example | Intended repository evidence |
|---|---|
| [`dod.yaml`](dod.yaml) | Every major contract option, including an intentional shell command |
| [`python/dod.yaml`](python/dod.yaml) | Declared pytest and Ruff configuration |
| [`node/dod.yaml`](node/dod.yaml) | `package.json` test/lint scripts and a lockfile |
| [`go/dod.yaml`](go/dod.yaml) | `go.mod` with tests and vet |
| [`rust/dod.yaml`](rust/dod.yaml) | Cargo tests and clippy |
| [`jvm/dod.yaml`](jvm/dod.yaml) | Maven tests for Java/Kotlin projects |
| [`ruby/dod.yaml`](ruby/dod.yaml) | Bundler and RSpec |
| [`dotnet/dod.yaml`](dotnet/dod.yaml) | .NET test projects |
| [`php/dod.yaml`](php/dod.yaml) | Composer and PHPUnit |
| [`mixed-monorepo/dod.yaml`](mixed-monorepo/dod.yaml) | Python service plus Node application in contained workspaces |
| [`github/dunnit-required.yml`](github/dunnit-required.yml) | Required GitHub pull-request check for a Python project |
| [`github/dunnit-shadow.yml`](github/dunnit-shadow.yml) | Non-blocking GitHub evaluation with a retained report |

The ecosystem examples assume the named tools already exist in the project.
`dunnit init --preset auto --dry-run` only proposes checks supported by explicit
repository evidence such as test-tool configuration, declared package scripts,
lockfiles, or workspace manifests. It does not install dependencies.

Prefer `argv`. Keep `writes` empty unless a proof command necessarily generates
files, and declare those paths relative to the repository root even when the
check has a `dir`.

Validate a selected policy before gating:

```bash
dunnit doctor -c path/to/example/dod.yaml
```
