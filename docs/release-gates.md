# Dunnit v1 release gates

Version numbers describe evidence maturity as well as code maturity. A release
must not skip its gate because a calendar date arrived.

## `1.0.0a1`: trust hardening

Required before publishing the first alpha:

- trusted committed policy loading in local and CI modes;
- full SHA/base/merge-base resolution and fail-closed Git errors;
- pre-command candidate evaluation and post-command mutation detection;
- path-complete tracked/untracked enumeration with explicit content
  completeness;
- `error` outcome and exit code 2 for incomplete verification;
- focused regressions for contract self-disable, command evidence erasure,
  invalid refs, missing shallow history, detached/unborn/worktree states, and
  unusual paths; and
- alpha documentation that makes no external-reliance claim.

Alpha packages may expose contract v2 and onboarding/report previews, but those
surfaces are not beta-ready until the next gate passes.

## `1.0.0b1`: portable feature completeness

Required before beta:

- contract v2 runtime/schema parity and v1 migration;
- portable `argv`, contained `dir`/`writes`, bounded sanitized output, and
  process-tree timeout behavior;
- evidence-based non-empty presets, `doctor`, and materialized monorepo output;
- text/JSON/GitHub/JUnit output plus retained report support;
- full GitHub required/shadow workflow generation with immutable action and
  package pins;
- Linux, Windows, and macOS Intel CI across Python 3.9–3.14, macOS ARM smoke,
  and scheduled Python 3.15 preview; and
- Ruff, MyPy, 90% branch coverage, build/metadata validation, clean wheel/sdist
  installs, and self-verification green on the release commit.

## `1.0.0rc1`: published product evidence

Required before a release candidate:

- the 300-case corpus frozen and published under the preregistered protocol;
- reproducible raw benchmark results, exact counts, confidence intervals,
  deviations, and performance context published whether gates pass or fail;
- trust/fail-closed correctness 100%, macro sensitivity at least 95%, benign
  false alarms at most 5%, no incomplete pass, and scanner-only p95 below two
  seconds; and
- pilot-ready documentation/data agreement reviewed against actual onboarding
  feedback.

A failed benchmark blocks this gate until a new implementation and genuinely
held-out corpus revision are evaluated. The failed result remains public.

## `1.0.0`: independent reliance

Stable v1.0 requires the complete external gate in
[`validation.md`](validation.md): five independently maintained repositories,
the eight-week/100-PR shadow phase, pre-gating metrics, and 30 days/10 real pull
requests per repository as a required check.

The “caught problems normal CI missed” claim additionally requires at least
three qualifying real incidents across at least two repositories. Stable may
ship without that marketing claim if the reliance gate passes but the incident
threshold does not; the publication must then state that no qualifying real
incident was observed.

## Release mechanics

For every published artifact:

1. ensure the version has one source of truth and the release tag is exactly
   `v<version>`;
2. run the required CI and compare broad failures with the base branch;
3. build wheel and source distribution from the tag in GitHub Actions;
4. run `twine check` and clean-install both artifacts;
5. publish through the protected `pypi` environment and Trusted Publishing; and
6. link evidence packages and known limitations without rewriting historical
   results.
