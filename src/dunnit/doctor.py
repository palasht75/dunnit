"""Read-only diagnostics for policy trust, Git state, and proof commands."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from dunnit.contract import CommandCheck, Contract, ContractError
from dunnit.gitdiff import GitDiffError, collect_diff_snapshot, matches_any
from dunnit.runner import (
    _add_scan_evidence,
    _ensure_meaningful_policy,
    _finish_evidence,
    _git_error,
    _load_policy,
    _policy_path,
    _populate_meta,
)
from dunnit.verdict import Evidence, Status, Verdict


def doctor(
    contract: Contract | str | Path = "dod.yaml",
    cwd: Path | None = None,
    base: str | None = None,
    mode: str = "local",
    policy_ref: str | None = None,
) -> Verdict:
    """Diagnose whether a repository is ready for meaningful verification.

    Doctor never runs proof commands. It validates the same immutable policy
    origin and comparison state used by :func:`dunnit.verify`, then checks
    command directories and executable availability.
    """

    verdict = Verdict()
    try:
        if mode not in {"local", "ci"}:
            raise ContractError("mode must be 'local' or 'ci'")
        # _policy_path requires the repository root. Discovering through a
        # snapshot would need policy first, so use Git's root helper indirectly.
        from dunnit.gitdiff import discover_repository

        root = discover_repository(Path(cwd or Path.cwd())).root
        policy_path, policy_rel = _policy_path(contract, root)
        materialized, raw, origin, is_bootstrap = _load_policy(
            contract=contract,
            root=root,
            policy_path=policy_path,
            policy_rel=policy_rel,
            mode=mode,
            policy_ref=policy_ref,
            base=base,
            bootstrap=False,
        )
        _ensure_meaningful_policy(materialized, policy_rel)
        candidate_ref = base
        if mode == "ci":
            trusted_ref = policy_ref or base
            trusted_sha = origin.split(":", 2)[1]
            if candidate_ref is None or candidate_ref == trusted_ref:
                candidate_ref = trusted_sha
        elif candidate_ref is None:
            candidate_ref = materialized.base
        snapshot = collect_diff_snapshot(root, candidate_ref)
        _populate_meta(
            verdict,
            materialized,
            snapshot,
            mode=mode,
            policy_origin=origin,
            policy_path=policy_rel,
            policy_bytes=raw,
            bootstrap=is_bootstrap,
        )
        _add_scan_evidence(verdict, snapshot, stage="doctor")

        verdict.add(
            Evidence(
                "doctor:policy",
                Status.PASS,
                f"policy is trusted from {origin}",
                rule_id="doctor.policy-trust",
            )
        )
        if any(
            item.path == policy_rel or item.old_path == policy_rel for item in snapshot.files
        ):
            verdict.add(
                Evidence(
                    "doctor:policy-worktree",
                    Status.FAIL,
                    f"candidate modifies the selected policy path: {policy_rel}",
                    hint="Revert the candidate policy change; update policy in a separately reviewed change.",
                    rule_id="doctor.policy-worktree",
                    path=policy_rel,
                )
            )

        state = snapshot.state
        verdict.add(
            Evidence(
                "doctor:git",
                Status.PASS,
                f"Git baseline resolved to {state.baseline_sha}",
                rule_id="doctor.git-state",
            )
        )
        if state.shallow:
            verdict.add(
                Evidence(
                    "doctor:shallow",
                    Status.WARN,
                    "checkout is shallow; the current target resolves but other bases may not",
                    hint="Use checkout fetch-depth: 0 or explicitly fetch the PR base SHA in CI.",
                    rule_id="doctor.shallow-checkout",
                )
            )
        if materialized.version == 1:
            verdict.add(
                Evidence(
                    "doctor:schema",
                    Status.WARN,
                    "contract v1 is supported during 1.x but deprecated",
                    hint="Run `dunnit migrate --dry-run`, then `dunnit migrate --write`.",
                    rule_id="doctor.schema-version",
                )
            )
        else:
            verdict.add(
                Evidence(
                    "doctor:schema",
                    Status.PASS,
                    "contract schema version 2 is supported",
                    rule_id="doctor.schema-version",
                )
            )

        for check in materialized.checks:
            _check_command(verdict, root, check)
        _check_meaningful_policy(verdict, root, materialized, policy_rel)
        for item in verdict.evidence:
            _finish_evidence(item)
    except ContractError as exc:
        verdict.add(
            Evidence(
                "doctor:contract",
                Status.ERROR,
                str(exc),
                hint="Fix or commit the policy before enabling Dunnit as a required check.",
                rule_id="doctor.contract",
                scan_complete=False,
            )
        )
    except GitDiffError as exc:
        verdict.add(_git_error(exc, "doctor"))
    except OSError as exc:
        verdict.add(
            Evidence(
                "doctor:infrastructure",
                Status.ERROR,
                f"doctor could not inspect the repository: {exc}",
                rule_id="doctor.infrastructure",
                scan_complete=False,
            )
        )
    finally:
        # Error paths must retain the same structured identity guarantees as
        # successful diagnostics.
        for item in verdict.evidence:
            _finish_evidence(item)
    return verdict


def _check_meaningful_policy(
    verdict: Verdict,
    root: Path,
    contract: Contract,
    policy_path: str,
) -> None:
    paths = _repository_paths(root)
    enabled: list[str] = []
    if contract.checks:
        enabled.append(f"{len(contract.checks)} proof command(s)")
    independent_protected = [path for path in contract.protected if path != policy_path]
    if independent_protected:
        enabled.append("protected paths")
    if contract.stubs:
        enabled.append("stub detection")
    if contract.require.changed or contract.require.non_empty_diff:
        enabled.append("positive diff requirements")
    if contract.tamper and contract.test_globs:
        enabled.append("test-integrity detection")
        matches = sorted(path for path in paths if matches_any(path, contract.test_globs))
        if not matches:
            verdict.add(
                Evidence(
                    "doctor:test-globs",
                    Status.WARN,
                    "tamper detection is enabled, but test_globs match no current repository files",
                    hint="Correct test_globs or add the repository's intended tests before gating.",
                    rule_id="doctor.test-glob-coverage",
                )
            )

    # The selected policy is independently protected by the runner, even when
    # it is not repeated in the contract's own protected list.
    protected = [*contract.protected, policy_path]
    if protected and not any(matches_any(path, protected) for path in paths):
        verdict.add(
            Evidence(
                "doctor:protected-globs",
                Status.WARN,
                "protected globs match no current repository files",
                hint="Confirm the protected paths are spelled for this repository.",
                rule_id="doctor.protected-glob-coverage",
            )
        )

    status = Status.PASS if enabled else Status.ERROR
    detail = (
        "policy enables " + ", ".join(enabled)
        if enabled
        else "policy has no effective command or integrity rule"
    )
    verdict.add(
        Evidence(
            "doctor:meaningful",
            status,
            detail,
            hint="Enable a proof command or an integrity rule." if not enabled else "",
            rule_id="doctor.meaningful-policy",
            scan_complete=status is not Status.ERROR,
        )
    )


def _repository_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
            ],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"could not enumerate repository paths: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(detail or "could not enumerate repository paths")
    return list(dict.fromkeys(os.fsdecode(value) for value in result.stdout.split(b"\0") if value))


def _check_command(verdict: Verdict, root: Path, check: CommandCheck) -> None:
    where = (root / check.dir).resolve() if check.dir else root.resolve()
    try:
        where.relative_to(root.resolve())
    except ValueError:
        verdict.add(
            Evidence(
                "doctor:directory",
                Status.ERROR,
                f"check {check.name!r} directory escapes the repository: {check.dir}",
                rule_id="doctor.command-directory",
                scan_complete=False,
            )
        )
        return
    try:
        info = where.stat()
    except OSError:
        info = None
    if info is None or not stat.S_ISDIR(info.st_mode):
        verdict.add(
            Evidence(
                "doctor:directory",
                Status.FAIL,
                f"check {check.name!r} directory does not exist: {check.dir or '.'}",
                hint="Fix the check `dir` or create the declared package directory.",
                rule_id="doctor.command-directory",
                path=check.dir,
            )
        )
        return

    if check.argv is None:
        verdict.add(
            Evidence(
                "doctor:command",
                Status.WARN,
                f"check {check.name!r} uses an OS-native shell string; executable resolution is deferred",
                hint="Prefer a v2 `argv` list for portable, shell-free execution.",
                rule_id="doctor.shell-command",
            )
        )
        return

    executable = check.argv[0]
    command_env = {**os.environ, **check.env}
    resolved = shutil.which(executable, path=command_env.get("PATH"))
    if "/" in executable or "\\" in executable:
        explicit = (where / executable).resolve()
        windows_explicit = (
            shutil.which(str(explicit), path=command_env.get("PATH")) if os.name == "nt" else None
        )
        executable_file = windows_explicit is not None if os.name == "nt" else os.access(explicit, os.X_OK)
        if explicit.is_file() and executable_file:
            resolved = str(explicit)
    if resolved:
        verdict.add(
            Evidence(
                "doctor:command",
                Status.PASS,
                f"check {check.name!r} executable is available: {executable}",
                rule_id="doctor.command-available",
            )
        )
    else:
        verdict.add(
            Evidence(
                "doctor:command",
                Status.FAIL,
                f"check {check.name!r} executable is not available: {executable}",
                hint="Install the proof tool in this environment or correct `argv[0]`.",
                rule_id="doctor.command-available",
            )
        )
