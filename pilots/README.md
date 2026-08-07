# Dunnit independent-repository pilot records

This directory contains the deterministic aggregation tool for the external
pilot in [`docs/validation.md`](../docs/validation.md). It contains no pilot
data and makes no adoption claim.

The input uses study-local repository and candidate IDs. Do not put source,
repository remotes, author identities, secrets, diffs, or command output in
these files. Each participating maintainer must approve the data agreement
before collection.

## Cohort file

`cohort.json` has `protocol: "dunnit-pilot-v1"` and a `repositories` array.
Each repository object has exactly these fields:

- `id`: study-local identifier;
- `ecosystems`: one or more ecosystem names;
- `monorepo`, `independent`, and `existing_required_ci`: booleans;
- `consent_mode`: `named` or `anonymized`;
- `onboarding_minutes` and `hand_authored_yaml`;
- `shadow_started_at` and `shadow_observed_through` as UTC timestamps;
- `required_started_at` and `required_observed_through` as UTC timestamps, or
  both `null` before that phase; and
- `required_continuously_enabled` and `maintainer_confirmed` booleans.

The observation-through timestamps are explicit maintainer records. The tool
does not infer an eight-week or 30-day window from sparse pull-request dates.

## Event file

`events.jsonl` contains one record per repository/candidate pair. Reruns of the
same candidate are deliberately deduplicated by rejecting duplicate IDs. Every
record has:

```json
{"protocol":"dunnit-pilot-v1","repository_id":"repo-a","ecosystem":"python","candidate_id":"pr-42-sha","occurred_at":"2026-08-06T12:00:00Z","dunnit_version":"1.0.0b1","report_version":1,"configuration_id":"contract-digest","environment_id":"hosted-linux-x64","phase":"shadow","eligible":true,"changed_tests_or_config":true,"dunnit_outcome":"fail","alert":true,"rule_ids":["tamper.added-skip"],"existing_ci":"pass","adjudication":"true_positive","resolution":"corrected","incomplete_pass":false,"scanner_duration_seconds":0.031}
```

Allowed phases are `seeded`, `shadow`, and `required`. Outcomes are `pass`,
`pass_with_warnings`, `fail`, or `error`; existing CI is `pass`, `fail`, or
`not_run`. Adjudication is `true_positive`, `false_positive`, `false_negative`,
`no_issue`, or `not_reviewed`. Resolution is `corrected`, `reverted`,
`accepted`, or `none`.

`ecosystem` must be one declared for the repository. The Dunnit/report,
configuration, and environment identifiers plus scanner duration make version
mixes and timing distributions auditable without collecting repository data.

Every alert must be adjudicated. Infrastructure `error` outcomes are not
alerts. Unflagged test/config changes selected for the preregistered sample use
`no_issue` or `false_negative`; unsampled records use `not_reviewed`.

## Aggregate

Supply the published benchmark aggregate because its quality and performance
gates are prerequisites for field reliance:

```bash
python pilots/aggregate.py \
  pilots/cohort.json \
  pilots/events.jsonl \
  benchmarks/results/aggregate.json \
  --output pilots/results/aggregate.json
```

Exit code 2 means the records are malformed or contradictory. A structurally
valid study writes the aggregate and exits 0 even when gates fail; inspect
`gates.status`. The report includes repository-level shadow/reliance gates,
review sampling coverage, exact precision and infrastructure-error counts, and
the separately labeled real-incident claim gate.

Generated examples, maintainer-owned repositories, or synthetic elapsed dates
are not qualifying evidence. The aggregator calculates supplied evidence; it
does not create independence, consent, adjudication, or elapsed observation.
