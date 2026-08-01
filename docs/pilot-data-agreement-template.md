# External pilot data agreement template

This operational template records informed participation in Dunnit's external
validation pilot. It is not legal advice and does not replace either party's
privacy, employment, or repository-governance obligations.

Complete and sign this agreement before collecting a seeded or real pull-request
record. A GitHub issue or installation alone is not consent.

## Participants

- Pilot protocol version/commit:
- Repository cohort ID:
- Repository owner organization (may remain private):
- Authorized repository maintainer and role:
- Dunnit study contact:
- Agreement date:
- Planned collection start/end dates:

The repository maintainer confirms they are authorized to approve the described
data use and that the repository is independently maintained, is not owned by
the Dunnit maintainer, and was not created only for this study.

## Publication choice

Select one:

- [ ] Named repository and organization may be published.
- [ ] Repository identity remains private; only a study-local cohort ID and
      approved aggregate characteristics may be published.

Approved public characteristics:

- [ ] Ecosystem(s)
- [ ] Monorepo status
- [ ] Approximate pull-request volume band
- [ ] Existing required-check count band
- [ ] Other (list explicitly):

No logo, testimonial, quotation, employee identity, or implication of
endorsement may be published without separate written approval.

## Data collected by default

The team knowingly supplies only:

- study-local cohort and candidate identifiers;
- ecosystem and monorepo flag;
- event timestamps and onboarding duration;
- Dunnit version, report schema version, contract digest, and consented
  non-sensitive environment versions;
- Dunnit outcome, completeness, rule IDs, severity, and duration;
- pass/fail/error state of pre-existing required checks on the same candidate;
- seeded versus real-candidate label;
- maintainer adjudication, disagreement state, and resolution category; and
- missing-data or exclusion reason.

The study does not collect repository remotes, source/diff content, commit
messages, author identity, credentials, command output, or secrets by default.
Dunnit sends no telemetry; the repository team exports approved records.

## Optional case-level material

Each optional item requires a separate case ID and approval. Approval for one
case does not apply to another.

| Case ID | Material | Purpose | Who may access | May publish? | Redaction approved by |
|---|---|---|---|---|---|
| | | | | | |

Do not transfer a suspected vulnerability until the private process in
[`SECURITY.md`](../SECURITY.md) is active. Security material remains embargoed
until coordinated disclosure is complete.

## Access, retention, and deletion

- Raw approved records are accessible only to the named study contacts and the
  repository maintainer delegates listed here:
- Store records encrypted in transit and at rest.
- Delete case-level/raw records no later than 90 days after the final study
  publication or termination, unless a shorter period is entered here:
- Aggregated, non-identifying published results and public synthetic fixtures may
  be retained indefinitely.
- Record deletion completion and notify the repository contact.

## Review and withdrawal

The repository team may stop future collection at any time. It may withdraw
unpublished records by written request until the publication freeze date below.
After approved aggregate data is public, removing it from every archive or
derived aggregate may not be feasible.

- Planned publication freeze date:
- Repository review window for its case summaries:
- Contact and procedure for correction/withdrawal:

Protocol deviations, exclusions, conflicting adjudications, and failed
thresholds remain part of the study record and may not be silently removed to
improve results.

## No guaranteed benefit

Participation does not guarantee that Dunnit will find a problem, prevent a
merge, meet its thresholds, remain enabled, or be suitable for the repository.
Seeded outcomes are synthetic evidence. Real “normal CI miss” claims require the
independent confirmation defined in [`validation.md`](validation.md).

## Approval

Repository maintainer:

- Name/role:
- Approval/signature method:
- Date:

Dunnit study contact:

- Name/role:
- Approval/signature method:
- Date:
