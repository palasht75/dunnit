# Threat model

This document defines what Dunnit v1.0 is designed to protect, which systems it
trusts, and what a successful verdict means. It is a product boundary, not a
security certification.

## Security objective

Given a repository-controlled definition of done and a candidate Git change,
Dunnit should:

1. execute the declared proof commands;
2. evaluate the complete candidate path set for supported integrity rules;
3. prevent the candidate from weakening or replacing the policy used to judge
   that same candidate;
4. preserve pre-command findings if a command mutates the worktree; and
5. fail closed when policy, Git, execution, or scan evidence is incomplete.

The resulting statement is “the trusted contract was satisfied by this
candidate under these recorded conditions.” It is not “the implementation is
correct” or “the code is safe.”

## Assets

- The policy selected for a verification run.
- The identity of the target, merge-base, and candidate commits.
- The candidate's tracked and untracked path set and inspectable content.
- Proof-command exit status, duration, and bounded output.
- Integrity findings, waivers, and scan-completeness metadata.
- The required-check setting that prevents an unsatisfied candidate from merge.

## Adversary and failure model

The candidate may contain accidental mistakes or deliberately reward-seeking
changes. It may edit tests, application code, test configuration, CI files, or
the working-tree copy of `dod.yaml`. Repository code run by a proof command may
also modify files, spawn child processes, emit hostile terminal sequences, or
produce unbounded/non-UTF-8 output.

Dunnit also treats ordinary infrastructure faults as relevant: invalid refs,
missing shallow history, detached HEAD, an unborn repository, linked worktrees,
unreadable files, subprocess timeouts, and command-start failures must not turn
into a passing result.

Dunnit cannot recover evidence that another step erased before the verifier
process started. A CI workflow that installs or builds the candidate project
before `dunnit verify` therefore moves that code outside the snapshot boundary
and is not a supported trust-preserving deployment.

## Trust boundaries

| Trusted input or component | Why it is trusted | Required control |
|---|---|---|
| Target SHA supplied in CI | Identifies the policy owner and comparison target | Use the CI event's immutable base SHA, not candidate-controlled text |
| Contract at the target SHA | Defines allowed proof and integrity policy | Protect the default branch and require review for policy changes |
| Verification workflow at the target branch | Installs and invokes Dunnit | Protect workflow files, pin actions and Dunnit, grant least privilege |
| Pre-verification environment | Exists before Dunnit's first snapshot | Use a reviewed image/environment; do not run candidate-controlled setup first |
| Git executable and runner OS | Supplies repository and process facts | Use maintained, isolated runners and record versions |
| Project dependencies | Proof commands execute them | Apply the repository's normal dependency and supply-chain controls |
| Human waivers | Resolve known heuristic exceptions | Keep `--allow` out of agent/required commands and retain waiver provenance |

The candidate worktree, agent transcript, candidate `dod.yaml`, test output
reported by an agent, and untrusted command output are not trusted.

Anyone who can change the protected base branch, required-check rules, runner,
Git binary, or installed Dunnit artifact is outside this adversary model. They
can redefine or bypass the result.

## Controls

### Policy integrity

- Dunnit discovers the Git worktree root before resolving the config path.
- CI mode reads policy content from the trusted target SHA. The candidate's
  working-tree policy is never used to judge that candidate.
- The selected contract path is protected independently of `protected:` entries
  inside the contract.
- Local mode reads committed `HEAD` policy. A repository without commits may use
  an explicitly labeled local bootstrap flow; CI cannot.
- Unknown or duplicate keys, unsupported schema versions, invalid exact types,
  and no-op policies are contract errors.

### Git and diff integrity

- Option-like refs are rejected and explicit refs resolve to full object IDs.
- Reports record requested ref, target SHA, merge-base SHA, candidate SHA, and
  the baseline used for comparison.
- NUL-delimited Git metadata preserves spaces, Unicode, and unusual path names.
- Worktree content and executable mode are compared directly with index object
  IDs, so Git stat-cache, fsmonitor, `core.fileMode`, and restored-timestamp
  shortcuts cannot conceal a tracked candidate edit.
- Replacement refs and graft files are rejected, replacement processing is
  disabled for every Git command, and the refs namespace is hashed across
  proof-command execution.
- Every non-ignored Git-untracked path is enumerated. Ignored paths explicitly
  named by protected, requirement, or declared-write policy are enumerated too;
  broad ignored dependency/generated trees remain outside the default scope.
  Binary, oversized, or unreadable candidate content produces explicit
  completeness evidence rather than disappearing.
- Unborn repositories compare against Git's empty-tree object. Detached HEAD and
  linked worktrees use Git plumbing rather than `.git` directory assumptions.
- A shallow checkout that lacks required history is an error with remediation.

### Command integrity

- The candidate diff is captured and evaluated before proof commands start.
- It is captured again afterward. Original findings are retained even if a
  command restores or deletes evidence.
- A command may mutate only paths authorized by its trusted `writes` globs.
  Those globs do not erase findings from the original snapshot.
- `argv` checks bypass a shell. `run` checks intentionally use the native shell
  and therefore inherit shell parsing risks.
- Output is decoded with replacement, terminal controls are sanitized, retained
  output is bounded, and a timeout terminates the process tree.

### Result integrity

- Outcomes distinguish `pass`, `pass_with_warnings`, `fail`, and `error`.
- Empty evidence, incomplete scans, contract errors, invalid Git state, and
  infrastructure errors cannot report `pass`.
- Machine reports include policy identity, Git identity, command facts, waiver
  provenance, and completeness so downstream systems can reject partial data.

## Deployment modes

### CI mode

CI mode is the enforcement boundary. Use a full checkout (or fetch the exact
base history), pass the event's base SHA as both `--base` and `--policy-ref`,
pin dependencies and actions, grant only `contents: read`, disable persisted
checkout credentials, retain the report, and configure the job as a required
check.

A repository-local `pull_request` workflow is not immutable merely because its
job name is required. The candidate can propose changes to workflow files, and
a required status name alone does not establish which workflow definition
produced it. Prefer a target-branch or organization-controlled required-workflow
ruleset where the hosting plan supports one. Otherwise require CODEOWNERS review
for the contract and workflow, protect the target branch, and disallow bypass
of both reviews and required checks. Dunnit's protected-path rule detects a
candidate workflow edit only when a trusted invocation is already running; it
cannot restore or protect a workflow that was disabled or replaced before the
verifier started.

After checkout and trusted runtime setup, the first candidate-aware operation
must be the pinned Dunnit invocation. Do not run `pip install -e .`, package
manager lifecycle hooks, builds, or repository scripts before it. Pre-provision
proof tools from a reviewed image/environment, or put setup in the trusted
contract as its first check with narrow `writes`; then Dunnit retains the
pre-setup candidate evidence and evaluates the resulting worktree mutation.

For pull requests from forks, do not use `pull_request_target` to execute
candidate code with base-repository secrets. The documented workflow uses
`pull_request` and no write token.

### Local mode

Local mode gives fast feedback against committed policy. A developer who owns
their machine and Git history can alter the inputs or bypass the command, so a
local pass is not equivalent to an independently enforced CI result.

## Out of scope and residual risk

Dunnit does not:

- infer unstated product intent or prove semantic correctness;
- detect every test weakness, hardcoded result, malicious implementation, or
  security vulnerability;
- sandbox repository code or make an unsafe proof command safe;
- make flaky, non-hermetic, or compromised tests trustworthy;
- protect a repository whose base policy or required-check settings are under
  the candidate author's control;
- make a candidate-editable CI workflow immutable or prove that a matching
  required-check name came from the intended workflow definition;
- reconstruct candidate evidence changed before the Dunnit process started;
- attest artifacts, sign verdicts, or provide a hosted policy service; or
- guarantee that every language construct is recognized by heuristic rules.

False positives and false negatives remain possible. Teams should review rule
findings, keep their ordinary CI/security controls, and report minimized cases
for the public corpus. See [Support and limitations](support.md) and
[SECURITY.md](../SECURITY.md).

## Reporting a threat-model gap

If a gap could let an untrusted candidate receive `pass` or conceal incomplete
verification, use the private process in [SECURITY.md](../SECURITY.md). For a
non-sensitive false positive, use the repository's public issue form.
