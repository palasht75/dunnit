# Reports and output formats

Dunnit separates the semantic outcome from its presentation. `--format`
selects stdout; `--report PATH` writes the versioned JSON report for retention
regardless of the human-facing formatter.

```bash
dunnit verify --format text
dunnit verify --format json
dunnit verify --format github --report dunnit-report.json
dunnit verify --format junit --report dunnit-report.json > dunnit-junit.xml
```

`--json` remains a compatibility alias for `--format json`.

## JSON schema version 1

The stable top-level shape is:

```json
{
  "schema_version": 1,
  "tool": "dunnit",
  "version": "1.0.0b1",
  "verdict": "pass",
  "outcome": "pass_with_warnings",
  "scan_complete": true,
  "summary": {"pass": 4, "fail": 0, "warn": 1, "error": 0},
  "evidence": [
    {
      "check": "contract:v1-deprecated",
      "status": "warn",
      "detail": "contract version 1 is deprecated and will be removed after the 1.x series",
      "rule_id": "contract.v1-deprecated",
      "severity": "warning",
      "scan_complete": true
    }
  ],
  "meta": {
    "mode": "ci",
    "base": "0123456789abcdef...",
    "files_changed": 3,
    "policy": {
      "origin": "git:0123456789abcdef...:dod.yaml",
      "path": "dod.yaml",
      "digest": "sha256:...",
      "version": 1,
      "bootstrap": false
    },
    "git": {
      "version": "git version ...",
      "requested_ref": "0123456789abcdef...",
      "target_sha": "0123456789abcdef...",
      "head_sha": "fedcba9876543210...",
      "merge_base_sha": "0123456789abcdef...",
      "baseline_sha": "0123456789abcdef...",
      "shallow": false,
      "unborn": false,
      "detached_head": true
    },
    "environment": {
      "os": "Linux",
      "platform": "...",
      "python": "3.14.0",
      "implementation": "CPython"
    },
    "scan": {
      "complete": true,
      "stages": [
        {
          "stage": "before commands",
          "paths_complete": true,
          "content_complete": true,
          "incomplete_paths": [],
          "files": 3
        }
      ]
    },
    "waivers": [],
    "duration": 1.234,
    "contract_version": 1
  }
}
```

The sample uses shortened placeholder digests; real Git object IDs and SHA-256
values are complete.

`verdict` is the legacy binary compatibility field. It is `pass` for `pass` and
`pass_with_warnings`, otherwise `fail`. New integrations must use `outcome` and
`scan_complete` so an infrastructure error cannot be confused with a detector
failure.

Evidence always has `check`, `status`, `detail`, `rule_id`, `severity`, and
`scan_complete`. Depending on the rule it may also have `hint`, `path`, `line`,
`fingerprint`, `duration`, or `exit_code`. Status is one of `pass`, `warn`,
`fail`, or `error`.

## Compatibility rules

- Consumers must reject an unknown `schema_version` until they support it.
- Within schema version 1, new optional fields and new rule IDs may appear.
  Consumers must ignore unknown keys.
- Do not infer success by counting only `fail` evidence. Require an outcome of
  `pass` or `pass_with_warnings` **and** `scan_complete: true`.
- Use `rule_id` for policy and `fingerprint` for deduplication. `check` remains
  the human-compatible identifier used by `--allow`.
- Git SHA, policy digest, candidate SHA, and config version together identify
  the evaluated state; a report from another SHA is not evidence for the
  current candidate.

## Other formatters

`text` is the interactive default and prints evidence, hints, and the semantic
outcome. `github` emits workflow annotations for path/line findings and writes a
job summary. `junit` maps failures and infrastructure errors to failing/error
testcase elements so CI test-report consumers cannot show an incomplete run as
green.

Formatters do not change the exit code:

- 0 for `pass` and `pass_with_warnings`;
- 1 for `fail`; and
- 2 for `error`.

## Privacy

Dunnit does not send a report anywhere. A report intentionally omits the Git
remote and automatic user/author identity, but evidence can contain repository
paths and bounded proof-command output. That output may contain secrets emitted
by the project's own tools.

Before uploading or publishing a report:

- run proof commands with secret-safe logging;
- inspect error output and paths;
- use least-privilege artifact access and a short retention period; and
- redact or omit a report from a public issue when it contains private data.

The GitHub examples retain reports for 14 days and use `contents: read` only.
