# Security policy

Dunnit runs repository-defined commands and makes merge-gating decisions from
Git and filesystem evidence. A fail-open bug can matter even when it is not a
traditional remote-code-execution vulnerability.

## Supported versions

Until `1.0.0a1` is published, the latest `0.3.x` release receives security
fixes. During the v1 prerelease series, only the newest published prerelease is
supported. After stable v1.0, the latest v1.x patch receives fixes; older
versions may be asked to upgrade before a report is investigated.

The exact supported package version is the one pinned by the current release
workflow and identified on the repository's Releases page. Python 3.9 support
is legacy compatibility and does not extend upstream Python security support.

## Report privately

Use GitHub's **Report a vulnerability** form for this repository:

<https://github.com/palasht75/dunnit/security/advisories/new>

Do not open a public issue for an unpatched bypass. If private vulnerability
reporting is unavailable, contact the repository owner through their GitHub
profile and ask for a private reporting channel without including exploit
details in the first public message.

Include, when safe:

- affected Dunnit version and installation source;
- operating system, Python version, and Git version;
- local or CI mode and relevant repository topology;
- a minimal synthetic repository or exact reproduction steps;
- expected and actual outcome/report fields;
- whether an untrusted candidate can obtain `pass`, escape a contained path, or
  conceal incomplete evidence; and
- any suggested embargo constraints.

Remove secrets, private source, repository remotes, access tokens, and personal
data. A synthetic reproducer is strongly preferred.

## What counts as security-sensitive

Please report privately if an untrusted candidate can plausibly:

- replace or weaken the policy used to judge itself in CI;
- cause an invalid Git/base or incomplete scan state to report `pass`;
- hide a protected, tracked, or untracked path from enumeration;
- erase a pre-command finding by mutating the worktree;
- escape the repository through a validated `dir` or `writes` path;
- inject terminal controls or unbounded output into a consuming system despite
  documented sanitization/bounds; or
- leave descendants running after an enforced timeout in a way that changes
  the verdict or later trusted steps.

False positives, missing heuristic patterns, documentation errors, and crashes
that reliably produce `error` rather than `pass` can usually use public issue
forms unless they expose sensitive data or enable a bypass.

## Expected behavior that is not a sandbox guarantee

Proof commands intentionally execute repository code with the invoking user's
permissions. A trusted contract's `run` string intentionally invokes the native
shell. Malicious code doing what the runner account permits is not, by itself,
a Dunnit sandbox escape: Dunnit is not a sandbox.

Use disposable, least-privilege CI runners; `contents: read`; no production
credentials; `persist-credentials: false` at checkout; pinned actions and
package versions; and the `pull_request` event.
Do not use `pull_request_target` to execute candidate code with repository
secrets. Review report artifacts before making command output public.

## Disclosure process

Maintainers aim to acknowledge a complete report within five business days and
provide a triage update within ten. Complex fixes may take longer. The reporter
and maintainers should agree on disclosure timing, attribution, affected
versions, and whether a CVE is appropriate before publication.

There is currently no bug bounty. Good-faith research that avoids privacy harm,
service disruption, and access beyond the researcher's own repositories is
welcome.
