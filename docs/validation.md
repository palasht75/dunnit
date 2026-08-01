# External validation and pilot protocol

Dunnit's problem statement is supported by independent research into agent
reward hacking. Its product claims require separate evidence. This document
defines the evidence needed before the project says that real teams rely on the
check or that it caught failures their normal CI missed.

## Current status

As of 2026-08-01:

| Evidence | Status |
|---|---|
| Benchmark protocol published before results | Specified in [`benchmarks/PROTOCOL.md`](../benchmarks/PROTOCOL.md) |
| Labeled benchmark corpus | Not yet published |
| Five qualifying independent repositories | Not yet established publicly |
| Eight-week/100-PR shadow observation | Not complete |
| Required-check reliance gate | Not complete |
| Confirmed real incidents missed by normal CI | 0 published |

These are zeros and unknowns, not implied successes. Seeded demonstrations will
always be labeled as seeded and will never be presented as customer incidents.

## Repository eligibility

A qualifying pilot repository must:

- be independently maintained and not owned by the Dunnit maintainer;
- not be a fork created only for the study;
- use pull requests and at least one pre-existing required CI check;
- allow the maintainers to adjudicate alerts and sampled non-alerts;
- consent to named publication or to an agreed anonymized aggregate; and
- keep enough event metadata to compare Dunnit with the pre-existing checks on
  the same candidate SHA.

The five-repository cohort must cover at least three ecosystems and include at
least one monorepo.

## Pilot phases

### 1. Onboarding

Start a timer before installation and stop when a reviewed contract and shadow
workflow produce their first complete report. Record auto-detected preset,
manual YAML changes, errors, documentation questions, and elapsed minutes.

Success requires median onboarding below 10 minutes and at least four of five
repositories needing no hand-authored YAML beyond confirming or removing
detected choices.

### 2. Paired seeded branches

Before observing real pull requests, each team runs agreed seeded cases against
both its existing CI and Dunnit. Record the four possible outcomes:

- existing CI only;
- Dunnit only;
- both; or
- neither.

Seeds validate installation and create reproducible product evidence. Reports
must name them as synthetic/seeded.

### 3. Shadow observation

Run Dunnit non-blocking for at least eight weeks and at least 100 eligible pull
requests overall, with at least 10 from each repository. Maintainers adjudicate:

- every Dunnit alert; and
- a random 20% of unflagged pull requests that changed tests or test
  configuration, with a minimum of five per repository where available.

Record true/false positive, false negative found by sampling, infrastructure
error, rule ID, resolution, and adjudicator role. Disagreement is preserved and
resolved by a second maintainer where possible.

### 4. Required-check reliance

Only after the pre-gating thresholds pass, make Dunnit a required check in each
repository for at least 30 days and 10 real pull requests. A repository counts
as relying on Dunnit only when its maintainer confirms the check remained
enabled and merge-blocking for that period.

## Pre-gating thresholds

- At least 90% maintainer-confirmed alert precision.
- Under 1% infrastructure-error rate.
- Zero incomplete scans reported as pass.
- Scanner-only p95 below two seconds on the published benchmark hardware.
- The onboarding targets above.
- The benchmark quality gates in the preregistered protocol.

Changing a threshold after seeing results creates a new protocol version and a
new evaluation; it does not rewrite the original result.

## Definitions

**Eligible pull request:** a non-bot or agreed agent-authored candidate that ran
the repository's existing required checks and Dunnit on the same SHA. Drafts,
dependency-only automation, and reruns without code changes are reported
separately.

**Alert:** any `fail` or rule-level warning selected for review. Contract/Git/
runner `error` outcomes count as infrastructure errors, not detector alerts.

**Normal CI miss:** all pre-existing required checks were green on the same SHA,
Dunnit uniquely failed, and a repository maintainer confirmed the underlying
issue and corrected or reverted it. A seeded case, flaky mismatch, policy error,
or warning without a confirmed defect does not qualify.

**False negative:** an adjudicated integrity failure in an eligible sampled
candidate for which Dunnit produced no applicable alert.

**Infrastructure-error rate:** error-outcome runs divided by eligible runs,
deduplicated by candidate SHA and configuration version.

## Claim gate

The project may say that five teams rely on Dunnit only after all five complete
the required-check phase. It may say Dunnit caught problems normal CI missed
only after at least three qualifying real incidents occur across at least two
repositories.

If the study observes fewer incidents, the publication will say exactly that:
“No qualifying real incident was observed,” alongside seeded and shadow results.

## Consent and data handling

Dunnit has no automatic telemetry. Pilot data is supplied knowingly by each
team. Before collection, the repository maintainer chooses named or anonymized
publication and approves:

- fields collected and retention period;
- who may inspect raw command output or diffs;
- whether excerpts may be published; and
- a withdrawal deadline before aggregate publication.

Collect the minimum needed: repository cohort ID, ecosystem, monorepo flag,
candidate SHA or study-local pseudonym, timestamps, Dunnit/report versions,
outcomes, rule IDs, existing-CI states, adjudication, and resolution. Do not
collect repository remotes, source content, secrets, author identity, or command
output unless the team explicitly opts in for a specific case. Scrub reports
before sharing them outside the repository.

Security incidents are excluded from the public corpus until coordinated
disclosure is complete.

Use the [pilot data agreement template](pilot-data-agreement-template.md) before
the first seeded run. A public issue expressing interest is not consent.

## Publication package

Publish, whether thresholds pass or fail:

- protocol version and any deviations;
- tool commit/package and immutable harness identifiers;
- cohort characteristics and consent mode;
- exact confusion-matrix counts by rule and ecosystem;
- macro sensitivity, false-alarm rate, precision, infrastructure errors, and
  Wilson confidence intervals where applicable;
- onboarding and timing distributions, including hardware context;
- every exclusion and missing-data reason; and
- anonymized qualifying incidents or an explicit statement that none occurred.

Raw private repository data is not required for reproducibility. Public seeded
cases, manifest digests, harness code, aggregate tables, and calculation steps
must be sufficient to reproduce benchmark metrics.
