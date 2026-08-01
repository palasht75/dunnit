# GitHub Actions examples

Both workflows use immutable action SHAs verified from the upstream action
repositories on 2026-08-01. Dependabot or another reviewed process should keep
the version comments and SHAs current.

The examples intentionally install only the exact Dunnit release before
verification. Do not insert a candidate project install, build, hook, or script
before `dunnit verify`: that code could change the worktree before Dunnit takes
its initial snapshot. Keep full Git history, the exact pull-request base SHA, CI
policy mode, least-privilege permissions, pinned Dunnit version, and report
retention.

Proof dependencies must instead be pre-provisioned in a reviewed runner
image/environment, or installed by a setup check materialized in the trusted
`dod.yaml`. The latter runs after the initial snapshot and is subject to
post-command mutation checks; give it empty `writes` when it should not change
the repository, or name only the generated paths it genuinely needs. GitHub's
hosted runner image is not evidence that an arbitrary project's proof tools are
available.

- [`dunnit-shadow.yml`](dunnit-shadow.yml) makes the verification step
  non-blocking. Use it while measuring onboarding, alerts, and infrastructure
  errors.
- [`dunnit-required.yml`](dunnit-required.yml) blocks on the result. After
  committing it, enforce it through a target-branch or organization-owned
  required-workflow ruleset where available. Otherwise require CODEOWNERS
  review for `dod.yaml` and `.github/workflows/**`, select the
  `dunnit / verify` check in branch protection, and disallow bypass.

An ordinary repository-local `pull_request` workflow can itself be changed by
a candidate. Requiring only its job name is not proof that the intended
workflow definition ran. Dunnit can flag a workflow path only after a trusted
workflow invokes it; it cannot defend a workflow disabled or replaced before
invocation.

The example pins the planned `1.0.0a1` package release. It is not an assertion
that the package has already been published. Update the pin only after reviewing
the target release and its migration notes.
