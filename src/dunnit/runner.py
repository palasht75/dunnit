"""Fail-closed orchestration for contracts, Git evidence, and proof commands.

The policy and the candidate are deliberately separate trust domains.  Local
verification reads policy from committed ``HEAD``; CI reads it from the
resolved target commit.  Candidate integrity is evaluated before any proof
command can mutate the worktree, and each command is followed by another
snapshot so undeclared writes are visible.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from dunnit.checks import check_protected, check_require, check_stubs, check_tamper, run_command
from dunnit.contract import Contract, ContractError, parse_contract
from dunnit.gitdiff import (
    MAX_SCANNED_BYTES,
    DiffSnapshot,
    FileDiff,
    GitDiffError,
    collect_diff_snapshot,
    discover_repository,
    matches_any,
    read_blob_at_revision,
    resolve_git_state,
)
from dunnit.verdict import Evidence, Status, Verdict


def verify(
    contract: Contract | str | Path = "dod.yaml",
    cwd: Path | None = None,
    base: str | None = None,
    strict: bool | None = None,
    allow: Sequence[str] = (),
    mode: str = "local",
    policy_ref: str | None = None,
    bootstrap: bool = False,
) -> Verdict:
    """Verify a repository against an immutable definition-of-done policy.

    ``mode="local"`` loads a file policy from committed ``HEAD``.  When the
    repository has no committed policy, the caller must explicitly opt into
    ``bootstrap=True`` to use the worktree copy. ``mode="ci"`` instead loads
    policy from ``policy_ref`` (or the explicit ``base`` when omitted) and
    never permits bootstrap policy.

    Existing arguments remain source compatible.  Infrastructure, policy,
    and Git failures are represented by an ``error`` Verdict rather than a
    vacuous or warning-only pass.
    """

    started = time.monotonic()
    verdict = Verdict()
    materialized: Contract | None = None
    effective_strict = bool(strict)

    try:
        if mode not in {"local", "ci"}:
            raise ContractError("mode must be 'local' or 'ci'")
        if mode == "ci" and bootstrap:
            raise ContractError("CI mode rejects bootstrap policy")

        invocation_dir = Path(cwd or Path.cwd())
        repository = discover_repository(invocation_dir)
        root = repository.root

        policy_path, policy_rel = _policy_path(contract, root)
        materialized, policy_bytes, policy_origin, is_bootstrap = _load_policy(
            contract=contract,
            root=root,
            policy_path=policy_path,
            policy_rel=policy_rel,
            mode=mode,
            policy_ref=policy_ref,
            base=base,
            bootstrap=bootstrap,
            validate_programmatic=True,
        )
        _ensure_meaningful_policy(materialized, policy_rel)
        effective_strict = materialized.strict if strict is None else strict

        # In CI the trusted target is also the default candidate comparison
        # target. A caller may still provide --base explicitly; it is resolved
        # independently and recorded as a full SHA below.
        candidate_ref = base
        if mode == "ci":
            trusted_ref = policy_ref or base
            trusted_sha = policy_origin.split(":", 2)[1]
            if candidate_ref is None or candidate_ref == trusted_ref:
                # Policy and candidate comparison must use the same immutable
                # identity even if a caller supplied a mutable branch name.
                candidate_ref = trusted_sha
        elif candidate_ref is None:
            candidate_ref = materialized.base

        # The selected path is protected independently of policy contents.
        # Policy-relevant ignored files must also be enumerated, while ignored
        # dependency/cache trees outside these globs remain out of scan scope.
        protected = list(dict.fromkeys([*materialized.protected, policy_rel]))
        always_relevant_ignored = list(
            dict.fromkeys(
                [
                    policy_rel,
                    *protected,
                    *materialized.require.changed,
                    *(glob for check in materialized.checks for glob in check.writes),
                ]
            )
        )
        relevant_ignored = list(
            dict.fromkeys([*always_relevant_ignored, *materialized.test_globs])
        )
        initial = collect_diff_snapshot(
            root,
            candidate_ref,
            relevant_ignored_globs=relevant_ignored,
            always_relevant_ignored_globs=always_relevant_ignored,
        )
        _populate_meta(
            verdict,
            materialized,
            initial,
            mode=mode,
            policy_origin=policy_origin,
            policy_path=policy_rel,
            policy_bytes=policy_bytes,
            bootstrap=is_bootstrap,
        )

        # The actual selected policy is protected even if its own contents try
        # to remove it from `protected`. Only an explicitly labelled local
        # bootstrap may add that first policy file.
        _add_scan_evidence(verdict, initial, stage="before commands")
        _evaluate_candidate(
            verdict,
            initial.files,
            materialized,
            protected=protected,
            policy_path=policy_rel if is_bootstrap else None,
            baseline=initial.state.baseline_sha,
        )

        if materialized.legacy:
            verdict.add(
                Evidence(
                    "contract:v1-deprecated",
                    Status.WARN,
                    "contract version 1 is deprecated and will be removed after the 1.x series",
                    hint="Run `dunnit migrate --dry-run`, then `dunnit migrate --write`.",
                    rule_id="contract.v1-deprecated",
                )
            )

        current = initial
        # An incomplete initial scan cannot establish a trustworthy before
        # state. Do not execute repository code in that condition.
        if initial.scan_complete:
            for check in materialized.checks:
                before_tokens = _snapshot_tokens(root, current)
                command_evidence = run_command(check, root)
                _finish_evidence(command_evidence)
                verdict.add(command_evidence)

                try:
                    after = collect_diff_snapshot(
                        root,
                        candidate_ref,
                        pinned_state=initial.state,
                        relevant_ignored_globs=relevant_ignored,
                        always_relevant_ignored_globs=always_relevant_ignored,
                    )
                except GitDiffError as exc:
                    verdict.add(_git_error(exc, "post-command diff"))
                    break

                _record_scan_stage(verdict, after, stage=f"after {check.name}")
                git_mutations = _git_state_changes(current, after)
                if git_mutations:
                    verdict.add(
                        Evidence(
                            "execution:git-state-mutated",
                            Status.FAIL,
                            f"proof command {check.name!r} changed Git state: "
                            + ", ".join(git_mutations),
                            hint="Proof commands must not commit, stage, check out refs, or modify "
                            "repository Git metadata.",
                            rule_id="execution.git-state-mutated",
                            fingerprint=_fingerprint(
                                "execution.git-state-mutated", check.name, *git_mutations
                            ),
                        )
                    )
                changed = _workspace_changes(root, current, after, before_tokens)
                undeclared = sorted(
                    path
                    for path in changed
                    if path == policy_rel or not matches_any(path, check.writes)
                )
                if undeclared:
                    rendered = ", ".join(undeclared[:20])
                    if len(undeclared) > 20:
                        rendered += f" (+{len(undeclared) - 20} more)"
                    verdict.add(
                        Evidence(
                            "execution:workspace-mutated",
                            Status.FAIL,
                            f"proof command {check.name!r} changed undeclared paths: {rendered}",
                            hint="Make the proof command read-only or declare generated paths in its "
                            "v2 `writes` list.",
                            rule_id="execution.workspace-mutated",
                            fingerprint=_fingerprint(
                                "execution.workspace-mutated", check.name, *undeclared
                            ),
                        )
                    )
                elif changed:
                    verdict.add(
                        Evidence(
                            "execution:writes",
                            Status.PASS,
                            f"proof command {check.name!r} changed only declared write paths",
                            rule_id="execution.declared-writes",
                            fingerprint=_fingerprint(
                                "execution.declared-writes", check.name, *sorted(changed)
                            ),
                        )
                    )
                current = after
                if not after.scan_complete:
                    _add_incomplete_scan(verdict, after, stage=f"after {check.name}")
                    break
                if git_mutations or undeclared:
                    # Later proof commands would execute against a repository
                    # whose trust boundary is already invalid. Preserve the
                    # post-command evidence above, then stop fail closed.
                    break

        _apply_policy(verdict, bool(effective_strict), allow)
    except ContractError as exc:
        verdict.add(
            Evidence(
                "contract",
                Status.ERROR,
                str(exc),
                hint="Fix or migrate the trusted contract and run verification again.",
                rule_id="contract.invalid",
                fingerprint=_fingerprint("contract.invalid", str(exc)),
                scan_complete=False,
            )
        )
    except GitDiffError as exc:
        verdict.add(_git_error(exc))
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        verdict.add(
            Evidence(
                "infrastructure",
                Status.ERROR,
                f"verification infrastructure failed: {exc}",
                hint="Resolve the local filesystem or process error and run verification again.",
                rule_id="infrastructure.failure",
                fingerprint=_fingerprint("infrastructure.failure", type(exc).__name__, str(exc)),
                scan_complete=False,
            )
        )
    finally:
        # Every emitted item gets an identity, including warnings created by
        # orchestration itself (for example the v1 deprecation notice).
        for item in verdict.evidence:
            _finish_evidence(item)
        verdict.meta.setdefault("mode", mode)
        verdict.meta["duration"] = round(time.monotonic() - started, 6)
        if materialized is not None:
            verdict.meta.setdefault("contract_version", materialized.version)
    return verdict


def _policy_path(contract: Contract | str | Path, root: Path) -> tuple[Path, str]:
    if isinstance(contract, Contract):
        trusted_label = contract.source and contract.source.startswith(("git:", "bootstrap:"))
        raw = Path(contract.source) if contract.source and not trusted_label else Path("dod.yaml")
    else:
        raw = Path(contract)
    raw_text = os.fspath(raw)
    if "\x00" in raw_text or (os.name != "nt" and PureWindowsPath(raw_text).drive):
        raise ContractError("contract path must stay within the repository root")
    path = raw if raw.is_absolute() else root / raw
    # Do not resolve through the candidate filesystem. In CI, a candidate
    # symlink at dod.yaml must not redirect policy selection to another trusted
    # target-tree blob. abspath/normpath provide lexical containment only.
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise ContractError("contract path must stay within the repository root") from exc
    try:
        relative = lexical.relative_to(root).as_posix()
    except ValueError as exc:
        raise ContractError("contract path must stay within the repository root") from exc
    if not relative or relative == ".":
        raise ContractError("contract path must name a file inside the repository")
    return lexical, relative


def _load_policy(
    *,
    contract: Contract | str | Path,
    root: Path,
    policy_path: Path,
    policy_rel: str,
    mode: str,
    policy_ref: str | None,
    base: str | None,
    bootstrap: bool,
    validate_programmatic: bool = True,
) -> tuple[Contract, bytes, str, bool]:
    if isinstance(contract, Contract):
        if mode == "ci":
            raise ContractError("CI mode requires a contract loaded from the trusted Git target")
        try:
            material = _contract_material(contract)
            raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        except (AttributeError, TypeError, ValueError) as exc:
            raise ContractError(f"invalid programmatic contract: {exc}") from exc
        # Apply YAML-equivalent validation to Python API objects too. Otherwise
        # invalid values could bypass the same fail-closed contract boundary
        # merely because the caller constructed a dataclass directly.
        validated = _parse_policy_bytes(raw, "python-api") if validate_programmatic else contract
        return validated, raw, "python-api", False

    if mode == "ci":
        trusted_ref = policy_ref or base
        if trusted_ref is None:
            raise ContractError("CI mode requires --policy-ref or --base for the trusted target")
        policy_state = resolve_git_state(root, trusted_ref)
        if policy_state.target_sha is None:
            raise ContractError("CI mode requires a committed target policy")
        raw = read_blob_at_revision(root, policy_state.target_sha, policy_rel)
        origin = f"git:{policy_state.target_sha}:{policy_rel}"
        return _parse_policy_bytes(raw, origin), raw, origin, False

    head_state = resolve_git_state(root, None)
    if head_state.head_sha is not None:
        try:
            raw = read_blob_at_revision(root, head_state.head_sha, policy_rel)
        except GitDiffError as exc:
            if exc.code != "policy_not_found":
                raise
        else:
            origin = f"git:{head_state.head_sha}:{policy_rel}"
            return _parse_policy_bytes(raw, origin), raw, origin, False

    if not bootstrap:
        raise ContractError(
            f"no committed policy exists at {policy_rel!r}; review it, then run "
            "`dunnit verify --bootstrap` locally"
        )
    raw = _read_bootstrap_policy(policy_path, root)
    origin = f"bootstrap:{policy_rel}"
    return _parse_policy_bytes(raw, origin), raw, origin, True


def _ensure_meaningful_policy(contract: Contract, policy_path: str) -> None:
    """Reject a policy whose only rule redundantly protects its own trust root."""

    independent_protected = [path for path in contract.protected if path != policy_path]
    if (
        contract.checks
        or independent_protected
        or contract.stubs
        or (contract.tamper and contract.test_globs)
        or contract.require.changed
        or contract.require.non_empty_diff
    ):
        return
    raise ContractError(
        "contract is a no-op: protecting only the selected contract path adds no project evidence"
    )


def _read_bootstrap_policy(path: Path, root: Path) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError(f"contract not found: {path} (run `dunnit init` to create one)") from exc
    except OSError as exc:
        raise ContractError(f"could not inspect bootstrap contract {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("bootstrap contract must be a regular file, not a symlink")
    try:
        path.resolve(strict=True).relative_to(root.resolve())
        return path.read_bytes()
    except ValueError as exc:
        raise ContractError("bootstrap contract resolves outside the repository") from exc
    except OSError as exc:
        raise ContractError(f"could not read bootstrap contract {path}: {exc}") from exc


def _parse_policy_bytes(raw: bytes, origin: str) -> Contract:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"trusted contract {origin} is not valid UTF-8") from exc
    return parse_contract(text, source=origin)


def _contract_material(contract: Contract) -> dict[str, Any]:
    if type(contract.checks) is not list:
        raise ContractError("programmatic contract 'checks' must be a list")
    if type(contract.test_globs) is not list or type(contract.protected) is not list:
        raise ContractError("programmatic contract path fields must be lists")
    checks: list[dict[str, Any]] = []
    for item in contract.checks:
        if type(item.env) is not dict or type(item.writes) is not list:
            raise ContractError("programmatic command env/writes must use dict/list values")
        if not all(type(key) is str and type(value) is str for key, value in item.env.items()):
            raise ContractError("programmatic command environment names/values must be strings")
        check: dict[str, Any] = {
            "name": item.name,
            "timeout": item.timeout,
            "env": item.env,
        }
        if contract.version >= 2 or item.writes:
            check["writes"] = item.writes
        if item.run is not None:
            check["run"] = item.run
        if item.argv is not None:
            if type(item.argv) is not list:
                raise ContractError("programmatic command argv must be a list")
            check["argv"] = item.argv
        if item.dir is not None:
            check["dir"] = item.dir
        checks.append(check)
    if type(contract.require.changed) is not list:
        raise ContractError("programmatic require.changed must be a list")
    material: dict[str, Any] = {
        "version": contract.version,
        "checks": checks,
        "test_globs": contract.test_globs,
        "protected": contract.protected,
        "tamper": contract.tamper,
        "stubs": contract.stubs,
        "strict": contract.strict,
        "require": {
            "changed": contract.require.changed,
            "non_empty_diff": contract.require.non_empty_diff,
        },
    }
    if contract.base is not None:
        material["base"] = contract.base
    return material


def _populate_meta(
    verdict: Verdict,
    contract: Contract,
    snapshot: DiffSnapshot,
    *,
    mode: str,
    policy_origin: str,
    policy_path: str,
    policy_bytes: bytes,
    bootstrap: bool,
) -> None:
    state = snapshot.state
    verdict.meta.update(
        {
            "mode": mode,
            "base": state.requested_ref or "HEAD",
            "files_changed": snapshot.path_count,
            "diff_transitions": snapshot.transition_count,
            "policy": {
                "origin": policy_origin,
                "path": policy_path,
                "digest": "sha256:" + hashlib.sha256(policy_bytes).hexdigest(),
                "version": contract.version,
                "bootstrap": bootstrap,
            },
            "git": {
                "version": _git_version(snapshot.state.root),
                "requested_ref": state.requested_ref,
                "target_sha": state.target_sha,
                "head_sha": state.head_sha,
                "merge_base_sha": state.merge_base_sha,
                "baseline_sha": state.baseline_sha,
                "shallow": state.shallow,
                "unborn": state.unborn,
                "detached_head": _detached_head(state.root),
                "head_ref": snapshot.git_metadata.head_ref,
                "index_digest": snapshot.git_metadata.index_digest,
                "effective_config_digest": snapshot.git_metadata.effective_config_digest,
                "configured_files_digest": snapshot.git_metadata.configured_files_digest,
                "refs_digest": snapshot.git_metadata.refs_digest,
                "grafts_digest": snapshot.git_metadata.grafts_digest,
            },
            "environment": {
                "os": platform.system(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
            },
            "scan": {"stages": []},
            "waivers": [],
        }
    )


def _git_version(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "--version"], cwd=root, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _detached_head(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=root,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode != 0


def _add_scan_evidence(verdict: Verdict, snapshot: DiffSnapshot, *, stage: str) -> None:
    _record_scan_stage(verdict, snapshot, stage=stage)
    if snapshot.scan_complete:
        count_detail = f"{snapshot.path_count} unique changed paths"
        if snapshot.transition_count != snapshot.path_count:
            count_detail += f" across {snapshot.transition_count} diff transitions"
        verdict.add(
            Evidence(
                "scan:completeness",
                Status.PASS,
                f"{stage}: enumerated {count_detail}; content scan complete",
                rule_id="scan.completeness",
                fingerprint=_fingerprint(
                    "scan.completeness",
                    stage,
                    str(snapshot.path_count),
                    str(snapshot.transition_count),
                ),
                scan_complete=True,
            )
        )
    else:
        _add_incomplete_scan(verdict, snapshot, stage=stage)


def _record_scan_stage(verdict: Verdict, snapshot: DiffSnapshot, *, stage: str) -> None:
    scan = verdict.meta.setdefault("scan", {})
    stages = scan.setdefault("stages", [])
    stages.append(
        {
            "stage": stage,
            "paths_complete": snapshot.paths_complete,
            "content_complete": snapshot.content_complete,
            "incomplete_paths": snapshot.incomplete_paths,
            # ``files`` is retained as a report-compatibility alias, but now
            # correctly counts unique current and rename-source paths.
            "files": snapshot.path_count,
            "paths": snapshot.path_count,
            "transitions": snapshot.transition_count,
        }
    )
    scan["complete"] = all(
        item["paths_complete"] and item["content_complete"] for item in stages
    )


def _add_incomplete_scan(verdict: Verdict, snapshot: DiffSnapshot, *, stage: str) -> None:
    details = []
    for item in snapshot.files:
        if not item.content_scanned:
            details.append(f"{item.path} ({item.scan_reason or 'content unavailable'})")
    rendered = ", ".join(details[:20]) or "path enumeration did not complete"
    if len(details) > 20:
        rendered += f" (+{len(details) - 20} more)"
    verdict.add(
        Evidence(
            "scan:incomplete",
            Status.ERROR,
            f"{stage}: verification scan is incomplete: {rendered}",
            hint="Remove or reduce unscannable candidate files, or narrow the change before retrying.",
            rule_id="scan.incomplete",
            fingerprint=_fingerprint("scan.incomplete", stage, *details),
            scan_complete=False,
        )
    )


def _evaluate_candidate(
    verdict: Verdict,
    diffs: list[FileDiff],
    contract: Contract,
    *,
    protected: list[str],
    policy_path: str | None,
    baseline: str,
) -> None:
    for evidence in check_protected(diffs, protected, policy_path):
        _finish_evidence(evidence)
        verdict.add(evidence)
    if contract.tamper:
        for evidence in check_tamper(diffs, contract.test_globs):
            _finish_evidence(evidence)
            verdict.add(evidence)
    if contract.stubs:
        for evidence in check_stubs(diffs, contract.test_globs):
            _finish_evidence(evidence)
            verdict.add(evidence)
    for evidence in check_require(diffs, contract.require, baseline):
        _finish_evidence(evidence)
        verdict.add(evidence)


def _finish_evidence(evidence: Evidence) -> None:
    if evidence.rule_id is None:
        evidence.rule_id = evidence.check.replace(":", ".")
    if evidence.fingerprint is None:
        identity = [
            evidence.rule_id,
            evidence.path or "",
            str(evidence.line or ""),
        ]
        if evidence.duration is not None:
            # Command details contain elapsed time and output tails. Use the
            # check identity and exit code so an unchanged command result can
            # be deduplicated across machines and repeated runs.
            identity.extend([evidence.check, str(evidence.exit_code)])
        else:
            identity.append(evidence.detail)
        evidence.fingerprint = _fingerprint(*identity)


def _git_state_changes(before: DiffSnapshot, after: DiffSnapshot) -> list[str]:
    changes: list[str] = []
    if before.state.head_sha != after.state.head_sha:
        changes.append("HEAD commit")
    fields = (
        ("head_ref", "HEAD reference"),
        ("head_file_digest", "HEAD metadata"),
        ("index_digest", "index"),
        ("config_digest", "Git config"),
        ("effective_config_digest", "effective Git config"),
        ("configured_files_digest", "configured Git excludes/attributes"),
        ("excludes_digest", "Git excludes"),
        ("attributes_digest", "Git attributes"),
        ("shallow_digest", "shallow metadata"),
        ("grafts_digest", "Git graft metadata"),
        ("refs_digest", "Git refs"),
    )
    for field, label in fields:
        if getattr(before.git_metadata, field) != getattr(after.git_metadata, field):
            changes.append(label)
    return list(dict.fromkeys(changes))


def _diffs_by_path(snapshot: DiffSnapshot) -> dict[str, list[FileDiff]]:
    grouped: dict[str, list[FileDiff]] = {}
    for item in snapshot.files:
        grouped.setdefault(item.path, []).append(item)
    return grouped


def _snapshot_tokens(root: Path, snapshot: DiffSnapshot) -> dict[str, tuple[Any, ...]]:
    by_path = _diffs_by_path(snapshot)
    paths = set(by_path)
    paths.update(item.old_path for item in snapshot.files if item.old_path)
    return {path: _path_token(root, path, by_path.get(path)) for path in sorted(paths)}


def _workspace_changes(
    root: Path,
    before: DiffSnapshot,
    after: DiffSnapshot,
    before_tokens: dict[str, tuple[Any, ...]],
) -> set[str]:
    after_by_path = _diffs_by_path(after)
    paths = set(before_tokens) | set(after_by_path)
    paths.update(item.old_path for item in after.files if item.old_path)
    changed = set()
    for path in paths:
        old_token = before_tokens.get(path, ("candidate-clean-or-absent",))
        new_token = _path_token(root, path, after_by_path.get(path))
        if old_token != new_token:
            changed.add(path)
    return changed


def _path_token(
    root: Path,
    path: str,
    diff: FileDiff | list[FileDiff] | None,
) -> tuple[Any, ...]:
    candidate = root.joinpath(*PurePosixPath(path).parts)
    items = diff if isinstance(diff, list) else ([] if diff is None else [diff])
    metadata = None if not items else tuple(
        (
            item.layer,
            item.status,
            item.old_path,
            item.old_size,
            item.new_size,
            item.binary,
            item.symlink,
        )
        for item in items
    )
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return metadata, "absent"
    except OSError as exc:
        return metadata, "unreadable", type(exc).__name__
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.fsencode(os.readlink(candidate))
        except OSError as exc:
            return metadata, "symlink-unreadable", type(exc).__name__
        return metadata, "symlink", hashlib.sha256(target).hexdigest()
    if not stat.S_ISREG(info.st_mode):
        return metadata, "special", stat.S_IFMT(info.st_mode), info.st_size
    if info.st_size > MAX_SCANNED_BYTES:
        # The surrounding snapshot already records an incomplete scan, which
        # makes the verdict ERROR. Size/mode metadata is sufficient for
        # mutation bookkeeping without hashing an attacker-sized output.
        return metadata, "file-too-large", stat.S_IMODE(info.st_mode), info.st_size
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return metadata, "unreadable", type(exc).__name__
    return metadata, "file", stat.S_IMODE(info.st_mode), info.st_size, digest.hexdigest()


def _git_error(exc: GitDiffError, stage: str = "git") -> Evidence:
    return Evidence(
        f"git:{exc.code}",
        Status.ERROR,
        f"{stage} failed: {exc}",
        hint=_git_hint(exc.code),
        rule_id=f"git.{exc.code}",
        fingerprint=_fingerprint(f"git.{exc.code}", str(exc)),
        scan_complete=False,
    )


def _git_hint(code: str) -> str:
    if code == "shallow_missing_history":
        return "Fetch the target commit and enough history for a merge-base, then retry."
    if code in {"invalid_ref", "no_merge_base"}:
        return "Pass an existing commit/ref that shares history with HEAD."
    if code == "unmerged_index":
        return "Resolve every index conflict and stage the resolutions before retrying."
    if code == "hidden_index_entries":
        return (
            "Clear assume-unchanged/skip-worktree flags or verify from a complete, "
            "non-sparse checkout."
        )
    if code == "replace_refs_present":
        return "Remove every Git replacement ref (`git replace -d ...`) and retry."
    if code == "grafts_present":
        return "Remove the repository's info/grafts override and retry."
    if code in {"git_metadata_changed", "git_changed_during_scan"}:
        return "Stop concurrent Git operations, restore the repository state, and retry."
    if code in {"policy_not_found", "invalid_policy_object"}:
        return "Commit a regular dod.yaml on the trusted target branch."
    if code == "not_worktree":
        return "Run Dunnit inside a non-bare Git worktree."
    return "Repair the Git checkout or repository state, then retry verification."


def _apply_policy(verdict: Verdict, strict: bool, allow: Sequence[str]) -> None:
    applied: list[dict[str, str]] = []
    for evidence in verdict.evidence:
        selector = _matching_allow(evidence.check, allow)
        if evidence.status is Status.FAIL and selector is not None:
            _finish_evidence(evidence)
            evidence.status = Status.WARN
            evidence.severity = "warning"
            evidence.detail += "  [downgraded by --allow]"
            applied.append(
                {
                    "check": evidence.check,
                    "selector": selector,
                    "source": "cli:--allow",
                    "rule_id": evidence.rule_id or evidence.check.replace(":", "."),
                    "fingerprint": evidence.fingerprint or "",
                }
            )
        elif strict and evidence.status is Status.WARN:
            evidence.status = Status.FAIL
            evidence.severity = "error"
    if applied:
        verdict.meta.setdefault("waivers", []).extend(applied)


def _allowed(check: str, allow: Sequence[str]) -> bool:
    return _matching_allow(check, allow) is not None


def _matching_allow(check: str, allow: Sequence[str]) -> str | None:
    return next(
        (item for item in allow if check == item or check.startswith(item + ":")),
        None,
    )


def _fingerprint(*parts: str) -> str:
    value = "\0".join(parts).encode("utf-8", errors="surrogateescape")
    return "sha256:" + hashlib.sha256(value).hexdigest()
