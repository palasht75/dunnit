# Preregistered Dunnit benchmark protocol, version 1

**Protocol ID:** `dunnit-benchmark-v1`  
**Preregistered:** 2026-08-01  
**Results status:** not run or published

This protocol is frozen before the v1 corpus is labeled and before aggregate
results are calculated. Deviations must be published next to the original
specification; thresholds are not changed after results are known.

## Questions

1. Does Dunnit fail closed on trust-root and verification-completeness attacks?
2. How often does it identify the documented tamper/stub categories?
3. How often does it alert on benign changes?
4. Is scanner-only latency suitable for a pull-request gate?

This benchmark measures deterministic detection on controlled fixtures. It does
not measure whether teams rely on Dunnit or whether it catches real incidents.

## Frozen corpus

Create exactly 300 independently labeled cases across every advertised language
family. The four mixed cases are cross-ecosystem monorepos:

| Ecosystem | Adversarial | Benign | Total |
|---|---:|---:|---:|
| Python | 25 | 12 | 37 |
| JavaScript/TypeScript | 25 | 12 | 37 |
| Go | 25 | 12 | 37 |
| Rust | 25 | 12 | 37 |
| JVM (Java/Kotlin/Scala) | 25 | 12 | 37 |
| Ruby | 25 | 12 | 37 |
| C# | 25 | 12 | 37 |
| PHP | 25 | 12 | 37 |
| Mixed monorepo | 0 | 4 | 4 |
| **Total** | **200** | **100** | **300** |

The adversarial cross-tab is frozen as follows:

| Ecosystem | Trust/fail closed | Delete/rename | Skip/focus | Assertions | Deselection | Stub/suppress | Command integrity |
|---|---:|---:|---:|---:|---:|---:|---:|
| Python | 5 | 3 | 4 | 4 | 4 | 2 | 3 |
| JavaScript/TypeScript | 5 | 3 | 5 | 3 | 4 | 2 | 3 |
| Go | 5 | 4 | 4 | 4 | 2 | 3 | 3 |
| Rust | 5 | 4 | 3 | 4 | 2 | 4 | 3 |
| JVM | 5 | 4 | 4 | 4 | 3 | 2 | 3 |
| Ruby | 5 | 4 | 4 | 4 | 2 | 3 | 3 |
| C# | 5 | 4 | 4 | 4 | 3 | 2 | 3 |
| PHP | 5 | 4 | 4 | 3 | 3 | 3 | 3 |
| **Total** | **40** | **30** | **32** | **30** | **23** | **21** | **24** |

Required adversarial examples include candidate policy replacement, protected
path moves, invalid/option-like refs, missing shallow history, commands that
erase evidence, binary/oversized candidate content that must fail closed,
native skip/disable/focus idioms, deleted/renamed tests,
cross-file assertion offsets, constant-truth replacements, quoted JSON/TOML
deselection, broad test commands narrowed to path selectors, disabled test
commands, unimplemented/swallowed/suppressed code, undeclared writes, timeout
descendants, and non-UTF-8/large output. Clear command narrowing and disabling
are failure-level findings, not review warnings.

The benign cross-tab is:

| Ecosystem | Normal/strengthening | Marker context | Legit config | Declared writes | Unusual path | Topology/EOL |
|---|---:|---:|---:|---:|---:|---:|
| Python | 5 | 2 | 2 | 1 | 1 | 1 |
| JavaScript/TypeScript | 5 | 2 | 2 | 1 | 1 | 1 |
| Go | 5 | 2 | 1 | 1 | 1 | 2 |
| Rust | 5 | 2 | 1 | 1 | 1 | 2 |
| JVM | 5 | 2 | 2 | 1 | 1 | 1 |
| Ruby | 5 | 2 | 1 | 1 | 1 | 2 |
| C# | 5 | 2 | 2 | 1 | 1 | 1 |
| PHP | 5 | 2 | 2 | 1 | 1 | 1 |
| Mixed monorepo | 0 | 0 | 0 | 1 | 1 | 2 |
| **Total** | **40** | **16** | **13** | **9** | **9** | **13** |

Benign controls include added assertions/tests, production-only fixes, marker
text used as data, legitimate reporter/timeout changes, declared generated
outputs, unusual but fully inspectable paths, and
topology/line-ending variants.

At least one topology instance per language ecosystem must run on Windows,
macOS, and Linux. Across the corpus, include detached HEAD, linked worktree, unborn local
repository, monorepo subdirectory, staged/unstaged/committed/untracked states,
more than 1,000 untracked paths, and both sufficient and insufficient shallow
history.

Cases may exercise more than one rule but have one primary category fixed in
the manifest. Do not create near-identical cases by changing only a filename.

## Case construction and labels

Each case is a minimal Git fixture with:

- a base repository snapshot and candidate mutation;
- exact OS/topology requirements;
- a trusted v2 contract;
- expected overall outcome and expected findings, including each rule ID,
  severity, and exact repository-relative path (or an explicit `null` when the
  rule has no path);
- forbidden rule IDs for benign cases;
- fixture/content SHA-256 digests; and
- a short rationale that does not expose the label to the runner.

Store one record per line in `manifest.jsonl`, validated against
[`case.schema.json`](case.schema.json). Fixture authors assign a preliminary
label. A second reviewer adjudicates it without seeing Dunnit's output.
Disagreements are resolved before the manifest is frozen. After freeze, compute
and publish a digest of the manifest and fixture archive.

The execution runner receives fixture and contract paths but not expected
outcomes. It writes append-only raw results. Do not modify a fixture after its
digest is published; corrections create a new protocol/corpus revision.

## Execution environment

Record:

- Dunnit package version, source commit, and wheel SHA-256;
- benchmark runner commit and manifest digest;
- OS image, architecture, Python version, and Git version;
- CPU model/count, memory, filesystem, and runner provider;
- command/toolchain lock files and cache state; and
- start/end timestamps.

Run correctness cases on the OS declared by the manifest in clean, disposable
workspaces with networking disabled after dependencies are provisioned. Run the
complete corpus using CPython 3.14. Portability CI remains a separate release
gate across Python 3.9–3.14.

Proof commands must be deterministic local fixtures. A case that flakes on two
consecutive clean reruns is quarantined, reported, and not silently replaced.

## Classification

For adversarial detector cases, a true positive requires the expected rule ID
at the expected severity and path, or a stricter overall `error` specifically
expected by the manifest. An unrelated alert does not satisfy the label.

For trust-root/fail-closed cases, success requires the exact expected non-pass
outcome and complete identity/completeness metadata. Any `pass` or
`pass_with_warnings` is a miss.

For benign cases, any unexpected warning, failure, or error is a false alarm.
Expected path-only handling of binary data must remain complete for the rules
the case enables.

The runner records all extra findings so one expected alert cannot hide noisy
secondary alerts.

## Metrics and gates

Publish exact integer numerators/denominators and point estimates for:

- sensitivity by primary category and ecosystem;
- macro sensitivity: unweighted mean of primary-category sensitivities;
- benign false-alarm rate;
- alert precision on this balanced synthetic corpus (clearly labeled as
  synthetic and not used as a field-precision estimate);
- trust-root/fail-closed correctness; and
- outcome/report completeness.

For every binomial proportion publish a two-sided 95% Wilson score interval,
following the [NIST proportion-interval guidance](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
Do not replace an undefined denominator with zero or 100%; report it as `n/a`.

The preregistered quality gates are:

- 100% correctness across all trust-root/fail-closed cases;
- at least 95% macro sensitivity across documented detector categories;
- at most 5% benign false alarms; and
- zero incomplete verifications reported as pass.

Report micro-averaged metrics as secondary values only. Do not tune rules on a
failed case and rerun under the same protocol ID. A subsequent tuned evaluation
uses a new held-out corpus revision and publishes the original result too.

## Performance

Measure scanner-only latency from immediately before Git discovery/diff
collection through completion of integrity-rule evaluation. Exclude environment
setup, dependency installation, declared proof-command runtime, and report file
I/O. Use the same public instrumentation point for every case.

On one disclosed Linux x64 reference runner:

1. provision dependencies once;
2. run one unreported warm-up per fixture;
3. run each case five times in a fresh worktree with filesystem caches left in
   their natural hosted-runner state;
4. retain every duration; and
5. calculate p50, p95, and maximum across measured runs.

The gate is scanner-only p95 below 2.0 seconds. Also publish distributions by
changed-path count and total inspectable bytes; the aggregate alone is not
sufficient.

## Publication

The result package must contain:

- frozen manifest and digests;
- redistributable fixtures or deterministic fixture generators;
- benchmark runner and environment locks;
- raw per-run JSON without secrets or repository remotes;
- calculation code and generated aggregate tables;
- exclusions, quarantines, deviations, and failed gates; and
- a plain-language limitations section.

Publish failed and inconclusive results. The README may summarize only values
that link to this complete package. No benchmark result may be described as an
external team incident or production catch.
