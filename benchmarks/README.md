# Dunnit benchmark

This directory is the public home for Dunnit's detector-quality and
scanner-performance evidence. It contains no result yet.

- [`PROTOCOL.md`](PROTOCOL.md) preregisters the v1 corpus, metrics, thresholds,
  execution rules, and publication requirements.
- [`case.schema.json`](case.schema.json) defines one JSONL manifest record.
- [`results/README.md`](results/README.md) reserves the result layout and states
  the current status.

The fixture corpus, immutable manifest, runner, environment lock files, and raw
results must be added before any benchmark claim is made. A green unit-test
suite is not a substitute for this benchmark, and the benchmark is not a
substitute for the independent repository pilot in
[`docs/validation.md`](../docs/validation.md).

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
