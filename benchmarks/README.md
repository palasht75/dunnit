# Dunnit benchmark

This directory is the public home for Dunnit's detector-quality and
scanner-performance evidence. **It contains no benchmark result yet.**

- [`PROTOCOL.md`](PROTOCOL.md) preregisters the v1 corpus, metrics, thresholds,
  execution rules, and publication requirements.
- [`case.schema.json`](case.schema.json) defines one JSONL manifest record.
- [`run.py`](run.py) executes fixtures blind and appends raw JSONL results.
- [`aggregate.py`](aggregate.py) validates and scores manifest + raw results.
- [`adjudicate.py`](adjudicate.py) verifies independent authorship/adjudication,
  fixture digests, and freezes a canonical manifest plus SHA-256 audit record.
- [`make_dev_sample.py`](make_dev_sample.py) generates a three-case harness
  sample that is explicitly **not** the corpus and **not** evidence.
- [`results/README.md`](results/README.md) reserves the result layout and states
  the current status.

The fixture corpus, immutable manifest, environment lock files, and raw
results must be added before any benchmark claim is made. A green unit-test
suite is not a substitute for this benchmark, and the benchmark is not a
substitute for the independent repository pilot in
[`docs/validation.md`](../docs/validation.md).

## What is built and what is not

The execution harness is complete; the labeled corpus is not, and cannot be
produced by the same party that writes the detectors.

| Component | Status |
|---|---|
| Preregistered protocol | Published before any result |
| Raw-record schema and aggregator | Implemented, tested |
| Blind execution runner | Implemented, tested |
| Independent label finalizer | Implemented, tested; requires human author/reviewer input |
| Scanner-only instrumentation point | `meta.scanner_duration` in Dunnit |
| Deterministic fixture format | Implemented (`base/`, `candidate/`, `contract.yaml`, `delete.txt`) |
| 300-case labeled corpus | **Not started** — needs independent authors and a second adjudicator |
| Linux reference performance run | **Not run** — needs the frozen corpus |
| Published result package | **Not published** |

### Why the corpus is not generated here

The protocol requires that a second reviewer adjudicate every label "without
seeing Dunnit's output", and it forbids tuning rules on a failed case and
rerunning under the same protocol ID. A corpus written by whoever just wrote or
modified the detectors is teaching to the test: it would report high sensitivity
and mean nothing. Corpus authorship and adjudication are therefore human,
independent work items, not automation gaps.

## Independent label handoff

Keep label work separate from execution output. An author-label JSONL record is:

```json
{"author_id":"author-a","authored_at":"2026-08-06T12:00:00Z","case":{"id":"python-skip-001","protocol":"dunnit-benchmark-v1","...":"complete case.schema.json record"}}
```

The separate reviewer records either agreement:

```json
{"case_id":"python-skip-001","adjudicator_id":"reviewer-b","adjudicated_at":"2026-08-06T13:00:00Z","decision":"agree"}
```

or a resolved disagreement with `decision: "resolved"`, a complete
`final_case`, non-empty `resolution`, and `resolver_id`. IDs may be study
pseudonyms, but the author and adjudicator IDs for a case must differ. Neither
file accepts Dunnit result fields.

After every case has been reviewed, freeze the manifest without overwriting an
existing result:

```bash
python benchmarks/adjudicate.py \
  benchmarks/author-labels.jsonl \
  benchmarks/adjudications.jsonl \
  --fixtures benchmarks/fixtures \
  --output benchmarks/manifest.jsonl \
  --audit-output benchmarks/manifest-audit.json
```

The finalizer validates every manifest record and recomputes every fixture
digest. It records counts of distinct author/adjudicator IDs and resolved
disagreements, but it cannot prove that pseudonyms represent independent
people; the project maintainer must verify that evidence before publication.

The runner enforces the part that *can* be enforced mechanically: it projects
every manifest record onto `EXECUTION_KEYS` at parse time, so labels never reach
the code that decides what to run or how long it took.

## Fixture format

Each fixture directory is materialized into a fresh disposable Git repository
per run:

```
fixtures/<case-id>/
  contract.yaml            # trusted v2 policy, committed with the base snapshot
  base/                    # files committed as the base repository snapshot
  candidate/               # files written on top as the candidate mutation
  candidate-contract.yaml  # optional; candidate's replacement dod.yaml
  delete.txt               # optional; repository-relative paths it removes
```

A candidate that rewrites its own trust root uses `candidate-contract.yaml`
rather than a literal `candidate/dod.yaml`, so a checked-in fixture never looks
like a real policy file to tools scanning this repository — including Dunnit
itself, which deliberately enumerates ignored files that match protected globs.

`run.py` refuses to execute a fixture whose recomputed digest differs from the
frozen `fixture_sha256`, and refuses any topology it cannot construct
deterministically rather than silently approximating it.

## Harness development sample

```bash
python benchmarks/make_dev_sample.py
python benchmarks/run.py benchmarks/dev-sample/manifest.jsonl \
  --fixtures benchmarks/dev-sample/fixtures \
  --output benchmarks/dev-sample/raw.jsonl \
  --environment benchmarks/dev-sample/environment.json
python benchmarks/aggregate.py \
  benchmarks/dev-sample/manifest.jsonl \
  benchmarks/dev-sample/raw.jsonl \
  --output benchmarks/dev-sample/aggregate.json \
  --allow-partial
```

Because the sample is a subset, every gate stays `null` with
`gates.status: not_evaluated`. That is the intended safety property: partial
output is not publishable benchmark evidence.

Each raw execution record must include `changed_path_count`,
`inspectable_bytes`, and all five Linux scanner durations. The aggregator
publishes latency distributions grouped by both size measures as well as the
overall p50, p95, and maximum; missing or negative size metadata is rejected.

Do not submit private repository diffs. Every fixture must be synthetic,
licensed for redistribution, or contributed with explicit permission.

## Reproduce the aggregate

After the frozen manifest and raw results exist, validate and aggregate them
with only the Python standard library:

```bash
python benchmarks/aggregate.py \
  benchmarks/manifest.jsonl \
  benchmarks/results/raw.jsonl \
  --output benchmarks/results/aggregate.json
```

The command exits `0` after writing a structurally valid aggregate, including
when a measured quality gate fails; inspect `gates.status`. It exits `2` for an
invalid, mismatched, incomplete, or unreadable input set. During harness
development only, `--allow-partial` accepts a subset and forces every gate to
`null` with `gates.status: not_evaluated`; partial output is not publishable
benchmark evidence.
