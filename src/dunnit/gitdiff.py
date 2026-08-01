"""Safe Git repository discovery and candidate-diff collection.

Diff-based checks must reason about the repository itself, not command output
formatted for humans.  This module therefore uses NUL-delimited Git plumbing
for path metadata and compares baseline blobs with worktree files directly.

``collect_diff`` remains the small, list-returning compatibility API.  Callers
that need the resolved trust metadata and scan-completeness information should
use ``collect_diff_snapshot``.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Protocol

# Git's canonical empty tree. It can be used as a diff baseline even in a
# repository which has not created its first commit yet.
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Path metadata is never capped. Only content inspection is bounded; callers
# can then fail closed using DiffSnapshot.content_complete.
MAX_SCANNED_BYTES = 1_000_000
MAX_SCANNED_LINES = 100_000
# Critical Git control files should always be tiny.  Refuse implausibly large
# replacements rather than spending unbounded time hashing attacker-controlled
# metadata before verification can fail closed.
MAX_GIT_METADATA_BYTES = 16 * 1024 * 1024
_FULL_OBJECT_ID = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def _is_full_object_id(value: str) -> bool:
    """Accept only full SHA-1 or SHA-256 Git object identities."""

    return _FULL_OBJECT_ID.fullmatch(value) is not None


class GitDiffError(RuntimeError):
    """A fail-closed repository, revision, or Git plumbing error.

    ``code`` is stable enough for the CLI/reporting layer to turn errors into
    actionable diagnostics without parsing the human-readable message.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RepositoryLayout:
    """Paths Git resolved for the current linked worktree."""

    root: Path
    git_dir: Path
    common_dir: Path


@dataclass(frozen=True)
class GitState:
    """Fully resolved comparison state used to collect a candidate diff."""

    repository: RepositoryLayout
    requested_ref: str | None
    target_sha: str | None
    head_sha: str | None
    merge_base_sha: str | None
    baseline_sha: str
    shallow: bool
    unborn: bool

    @property
    def root(self) -> Path:
        """Convenience alias for the linked worktree root."""

        return self.repository.root


@dataclass
class FileDiff:
    path: str
    status: str  # A(dded), M(odified), D(eleted), R(enamed), T(ype changed)
    old_path: str | None = None  # renames: where the file used to live
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    content_scanned: bool = True
    scan_reason: str | None = None
    old_size: int | None = None
    new_size: int | None = None
    binary: bool = False
    symlink: bool = False
    # Preserve the transition which exposed this finding. A single path can
    # legitimately appear in more than one layer (for example a weakened
    # staged test whose worktree copy was restored).
    layer: str = "worktree"


@dataclass(frozen=True)
class GitMetadata:
    """Critical mutable Git state captured around a candidate snapshot."""

    head_ref: str | None
    head_file_digest: str
    index_digest: str
    config_digest: str
    effective_config_digest: str
    configured_files_digest: str
    excludes_digest: str
    attributes_digest: str
    shallow_digest: str
    grafts_digest: str
    refs_digest: str


@dataclass
class DiffSnapshot:
    """Candidate paths, content findings, and their completeness."""

    state: GitState
    files: list[FileDiff]
    git_metadata: GitMetadata
    paths_complete: bool = True

    @property
    def content_complete(self) -> bool:
        return all(item.content_scanned for item in self.files)

    @property
    def scan_complete(self) -> bool:
        """Whether both path enumeration and content inspection completed."""

        return self.paths_complete and self.content_complete

    @property
    def incomplete_paths(self) -> list[str]:
        return list(
            dict.fromkeys(item.path for item in self.files if not item.content_scanned)
        )

    @property
    def changed_paths(self) -> list[str]:
        """Unique current and rename-source paths represented by the snapshot."""

        paths = {item.path for item in self.files}
        paths.update(item.old_path for item in self.files if item.old_path is not None)
        return sorted(paths)

    @property
    def path_count(self) -> int:
        return len(self.changed_paths)

    @property
    def transition_count(self) -> int:
        """Number of committed/index/worktree/untracked transitions inspected."""

        return len(self.files)


@dataclass(frozen=True)
class _Content:
    data: bytes | None
    size: int | None
    reason: str | None = None
    binary: bool = False
    symlink: bool = False


@dataclass(frozen=True)
class _IndexEntry:
    path: str
    mode: str
    object_id: str


@dataclass(frozen=True)
class _WorktreeIdentity:
    kind: str
    mode: str | None = None
    object_id: str | None = None
    normalized_object_id: str | None = None
    reason: str | None = None


class _Hash(Protocol):
    def update(self, value: bytes) -> None: ...

    def hexdigest(self) -> str: ...


def _display_command(args: list[str]) -> str:
    return "git " + " ".join(args)


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    error_code: str = "git_failed",
    check: bool = True,
    input_data: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run Git without a shell and retain paths as raw bytes."""

    # Replacement refs can make a caller-supplied full SHA resolve to attacker-
    # selected object contents while reports still display the original SHA.
    # Disable replacement processing for every plumbing command; explicit
    # checks below also reject repositories which contain replacement refs.
    git_env = dict(os.environ)
    git_env["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *args,
            ],
            cwd=cwd,
            capture_output=True,
            env=git_env,
            input=input_data,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitDiffError("git_not_found", "Git is not installed or is not on PATH") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitDiffError(error_code, f"{_display_command(args)} could not run: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        message = f"{_display_command(args)} failed"
        if detail:
            message += f": {detail}"
        raise GitDiffError(error_code, message)
    return result


def _git_bytes(args: list[str], cwd: Path, *, error_code: str = "git_failed") -> bytes:
    return _run_git(args, cwd, error_code=error_code).stdout


def _decode_path(value: bytes) -> str:
    # os.fsdecode uses surrogateescape on POSIX, so even non-UTF-8 names remain
    # round-trippable through pathlib and can still be protected by exact path.
    return os.fsdecode(value)


def _single_path(value: bytes, *, name: str) -> Path:
    decoded = _decode_path(value.rstrip(b"\r\n"))
    if not decoded:
        raise GitDiffError("invalid_repository", f"Git returned an empty {name}")
    return Path(decoded).resolve()


def discover_repository(cwd: Path) -> RepositoryLayout:
    """Discover the linked worktree root and Git directories for ``cwd``.

    The returned ``root`` is always Git's top level, even when the caller is in
    a nested monorepo directory. Bare repositories are intentionally rejected:
    Dunnit's checks require a candidate worktree.
    """

    location = Path(cwd)
    if not location.exists():
        raise GitDiffError("not_worktree", f"path does not exist: {location}")
    if not location.is_dir():
        raise GitDiffError("not_worktree", f"path is not a directory: {location}")

    result = _run_git(
        ["rev-parse", "--path-format=absolute", "--show-toplevel"],
        location,
        error_code="not_worktree",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        message = f"not inside a Git worktree: {location}"
        if detail:
            message += f" ({detail})"
        raise GitDiffError("not_worktree", message)

    root = _single_path(result.stdout, name="worktree root")
    try:
        location.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise GitDiffError(
            "invalid_repository",
            "Git resolved a worktree root which does not contain the requested directory",
        ) from exc
    git_dir = _single_path(
        _git_bytes(
            ["rev-parse", "--path-format=absolute", "--absolute-git-dir"],
            root,
            error_code="invalid_repository",
        ),
        name="Git directory",
    )
    common_dir = _single_path(
        _git_bytes(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            root,
            error_code="invalid_repository",
        ),
        name="Git common directory",
    )
    return RepositoryLayout(root=root, git_dir=git_dir, common_dir=common_dir)


def _is_shallow(root: Path) -> bool:
    value = _git_bytes(
        ["rev-parse", "--is-shallow-repository"],
        root,
        error_code="invalid_repository",
    ).strip()
    if value == b"true":
        return True
    if value == b"false":
        return False
    raise GitDiffError("invalid_repository", "Git returned an invalid shallow-repository state")


def _validate_ref(ref: str) -> None:
    if not isinstance(ref, str):
        raise GitDiffError("invalid_ref", "comparison ref must be a string")
    if not ref:
        raise GitDiffError("invalid_ref", "comparison ref must not be empty")
    if ref.startswith("-"):
        raise GitDiffError("invalid_ref", f"comparison ref must not start with '-': {ref!r}")
    if "\0" in ref or "\r" in ref or "\n" in ref:
        raise GitDiffError("invalid_ref", "comparison ref contains a forbidden control character")


def _resolve_commit(root: Path, ref: str, *, shallow: bool) -> str:
    _validate_ref(ref)
    result = _run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
        root,
        check=False,
    )
    if result.returncode != 0:
        code = "shallow_missing_history" if shallow else "invalid_ref"
        suffix = " (the commit may be outside this shallow clone)" if shallow else ""
        raise GitDiffError(code, f"cannot resolve comparison ref {ref!r} to a commit{suffix}")
    sha = result.stdout.strip().decode("ascii", errors="strict")
    if not _is_full_object_id(sha):
        raise GitDiffError("invalid_ref", f"Git returned an invalid object ID for {ref!r}")
    return sha.lower()


def _head_sha(root: Path) -> str | None:
    result = _run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", "HEAD^{commit}"],
        root,
        check=False,
    )
    if result.returncode != 0:
        # A valid worktree with no first commit has no resolvable HEAD, but its
        # status is still readable. Do not mistake repository corruption or a
        # missing HEAD object for that legitimate unborn state.
        status = _run_git(
            ["status", "--porcelain=v1", "-z", "--untracked-files=no"],
            root,
            check=False,
        )
        if status.returncode != 0:
            detail = status.stderr.decode("utf-8", errors="replace").strip()
            raise GitDiffError(
                "invalid_repository", detail or "Git HEAD cannot be resolved"
            )
        return None
    sha = result.stdout.strip().decode("ascii", errors="strict")
    if not _is_full_object_id(sha):
        raise GitDiffError("invalid_repository", "Git returned an invalid HEAD object ID")
    return sha.lower()


def _empty_tree_sha(root: Path) -> str:
    result = _run_git(
        ["hash-object", "-t", "tree", "--stdin"],
        root,
        error_code="invalid_repository",
        input_data=b"",
    )
    sha = result.stdout.strip().decode("ascii", errors="strict").lower()
    if not _is_full_object_id(sha):
        raise GitDiffError("invalid_repository", "Git returned an invalid empty-tree object ID")
    return sha


def _ensure_no_replace_refs(root: Path) -> None:
    refs = _git_bytes(
        ["for-each-ref", "--format=%(refname)", "refs/replace/"],
        root,
        error_code="git_metadata_failed",
    )
    if refs.strip():
        raise GitDiffError(
            "replace_refs_present",
            "Git replacement refs are present; remove refs/replace/* before verification",
        )


def _graft_paths(repository: RepositoryLayout) -> list[Path]:
    return list(
        dict.fromkeys(
            [
                repository.common_dir / "info" / "grafts",
                repository.git_dir / "info" / "grafts",
            ]
        )
    )


def _ensure_no_grafts(repository: RepositoryLayout) -> None:
    for path in _graft_paths(repository):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GitDiffError(
                "git_metadata_failed", f"cannot inspect Git graft file {path}: {exc}"
            ) from exc
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size:
            raise GitDiffError(
                "grafts_present",
                "a Git info/grafts override is present; remove it before verification",
            )


def _ensure_no_object_overrides(repository: RepositoryLayout) -> None:
    _ensure_no_replace_refs(repository.root)
    _ensure_no_grafts(repository)


def resolve_git_state(cwd: Path, base: str | None) -> GitState:
    """Resolve the exact target, HEAD, merge-base, and candidate baseline.

    With no explicit base, the committed ``HEAD`` is the target/baseline and
    the snapshot represents staged, unstaged, and untracked work. With an
    explicit base, the merge-base with ``HEAD`` is used so unrelated target
    branch changes are not attributed to the candidate. An unborn repository
    always uses the empty tree.
    """

    if base is not None:
        _validate_ref(base)
    repository = discover_repository(cwd)
    _ensure_no_object_overrides(repository)
    shallow = _is_shallow(repository.root)
    head_sha = _head_sha(repository.root)
    unborn = head_sha is None

    if base is None:
        if unborn:
            return GitState(
                repository=repository,
                requested_ref=None,
                target_sha=None,
                head_sha=None,
                merge_base_sha=None,
                baseline_sha=_empty_tree_sha(repository.root),
                shallow=shallow,
                unborn=True,
            )
        assert head_sha is not None
        return GitState(
            repository=repository,
            requested_ref=None,
            target_sha=head_sha,
            head_sha=head_sha,
            merge_base_sha=head_sha,
            baseline_sha=head_sha,
            shallow=shallow,
            unborn=False,
        )

    target_sha = _resolve_commit(repository.root, base, shallow=shallow)
    if unborn:
        return GitState(
            repository=repository,
            requested_ref=base,
            target_sha=target_sha,
            head_sha=None,
            merge_base_sha=None,
            baseline_sha=_empty_tree_sha(repository.root),
            shallow=shallow,
            unborn=True,
        )

    assert head_sha is not None
    result = _run_git(
        ["merge-base", "--", target_sha, head_sha],
        repository.root,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        if shallow:
            raise GitDiffError(
                "shallow_missing_history",
                f"cannot find merge-base for {base!r}; fetch sufficient history and retry",
            )
        raise GitDiffError("no_merge_base", f"no merge-base exists between {base!r} and HEAD")
    merge_base_sha = result.stdout.strip().decode("ascii", errors="strict").lower()
    if not _is_full_object_id(merge_base_sha):
        raise GitDiffError("invalid_repository", "Git returned an invalid merge-base object ID")
    return GitState(
        repository=repository,
        requested_ref=base,
        target_sha=target_sha,
        head_sha=head_sha,
        merge_base_sha=merge_base_sha,
        baseline_sha=merge_base_sha,
        shallow=shallow,
        unborn=False,
    )


def _resolve_pinned_state(cwd: Path, pinned: GitState) -> GitState:
    """Resolve current HEAD while retaining an earlier target and baseline.

    Proof commands are allowed to change declared worktree outputs, but they
    must not move the comparison boundary by committing or checking out a new
    HEAD.  A pinned post-command snapshot therefore re-discovers the worktree
    and current HEAD while keeping the original full target/baseline IDs.
    """

    repository = discover_repository(cwd)
    _ensure_no_object_overrides(repository)
    if (
        repository.root != pinned.repository.root
        or repository.git_dir != pinned.repository.git_dir
        or repository.common_dir != pinned.repository.common_dir
    ):
        raise GitDiffError(
            "git_metadata_changed",
            "repository or linked-worktree metadata changed after the initial snapshot",
        )
    shallow = _is_shallow(repository.root)
    result = _run_git(
        ["cat-file", "-e", f"{pinned.baseline_sha}^{{tree}}"],
        repository.root,
        check=False,
    )
    if result.returncode != 0:
        code = "shallow_missing_history" if shallow else "invalid_repository"
        raise GitDiffError(code, f"initial baseline {pinned.baseline_sha} is no longer available")
    head_sha = _head_sha(repository.root)
    return GitState(
        repository=repository,
        requested_ref=pinned.requested_ref,
        target_sha=pinned.target_sha,
        head_sha=head_sha,
        merge_base_sha=pinned.merge_base_sha,
        baseline_sha=pinned.baseline_sha,
        shallow=shallow,
        unborn=head_sha is None,
    )


def _head_ref(root: Path) -> str | None:
    result = _run_git(["symbolic-ref", "-q", "HEAD"], root, check=False)
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitDiffError("invalid_repository", detail or "Git HEAD reference is invalid")
    value = result.stdout.rstrip(b"\r\n").decode("utf-8", errors="surrogateescape")
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise GitDiffError("invalid_repository", "Git returned an invalid symbolic HEAD")
    return value


def _path_digest(path: Path) -> str:
    """Hash metadata without following a replacement symlink."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        raise GitDiffError("git_metadata_failed", f"cannot inspect Git metadata {path}: {exc}") from exc
    digest = hashlib.sha256()
    digest.update(str(stat.S_IFMT(info.st_mode)).encode("ascii"))
    digest.update(b"\0")
    if stat.S_ISLNK(info.st_mode):
        try:
            digest.update(os.fsencode(os.readlink(path)))
        except OSError as exc:
            raise GitDiffError(
                "git_metadata_failed", f"cannot read Git metadata symlink {path}: {exc}"
            ) from exc
        return "sha256:" + digest.hexdigest()
    if not stat.S_ISREG(info.st_mode):
        digest.update(str(info.st_size).encode("ascii"))
        return "sha256:" + digest.hexdigest()
    if info.st_size > MAX_GIT_METADATA_BYTES:
        raise GitDiffError(
            "git_metadata_too_large",
            f"critical Git metadata {path} exceeds {MAX_GIT_METADATA_BYTES} bytes",
        )
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise GitDiffError("git_metadata_failed", f"cannot read Git metadata {path}: {exc}") from exc
    return "sha256:" + digest.hexdigest()


def _combined_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(os.fsencode(path))
        digest.update(b"\0")
        digest.update(_path_digest(path).encode("ascii"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _index_digest(root: Path) -> str:
    # Hash logical entries and assume/skip-worktree flags, not the on-disk
    # index's stat cache. Git may legitimately refresh stat data while reading
    # a clean file; blob IDs/stages/flags are the evidence-bearing state.
    output = _git_bytes(
        ["ls-files", "--stage", "-v", "-z", "--"],
        root,
        error_code="index_scan_failed",
    )
    return "sha256:" + hashlib.sha256(output).hexdigest()


def _effective_config_digest(root: Path) -> str:
    """Hash effective repository/system/global config without exposing values."""

    output = _git_bytes(
        ["config", "--null", "--list", "--show-origin", "--includes"],
        root,
        error_code="git_metadata_failed",
    )
    if len(output) > MAX_GIT_METADATA_BYTES:
        raise GitDiffError(
            "git_metadata_too_large",
            f"effective Git config exceeds {MAX_GIT_METADATA_BYTES} bytes",
        )
    return "sha256:" + hashlib.sha256(output).hexdigest()


def _refs_digest(root: Path) -> str:
    output = _git_bytes(
        ["for-each-ref", "--format=%(refname)%00%(objectname)%00", "refs/"],
        root,
        error_code="git_metadata_failed",
    )
    if len(output) > MAX_GIT_METADATA_BYTES:
        raise GitDiffError(
            "git_metadata_too_large",
            f"Git refs metadata exceeds {MAX_GIT_METADATA_BYTES} bytes",
        )
    return "sha256:" + hashlib.sha256(output).hexdigest()


def _semantic_path_digest(path: Path) -> str:
    """Hash a configured file and, for symlinks, the file Git will consume."""

    direct = _path_digest(path)
    try:
        if not path.is_symlink():
            return direct
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return direct
    except OSError as exc:
        raise GitDiffError(
            "git_metadata_failed", f"cannot resolve configured Git metadata {path}: {exc}"
        ) from exc
    return _combined_digest([path, resolved])


def _configured_files_digest(root: Path) -> str:
    """Hash external ignore/attribute files which can alter Git plumbing."""

    digest = hashlib.sha256()
    for key in ("core.excludesFile", "core.attributesFile"):
        result = _run_git(
            ["config", "--null", "--path", "--get-all", key],
            root,
            error_code="git_metadata_failed",
            check=False,
        )
        if result.returncode not in {0, 1}:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitDiffError(
                "git_metadata_failed", detail or f"cannot resolve configured {key} paths"
            )
        values = _split_z(result.stdout)
        digest.update(key.encode("ascii"))
        digest.update(b"\0")
        for value in values:
            decoded = _decode_path(value)
            configured = Path(decoded)
            if not configured.is_absolute():
                configured = root / configured
            digest.update(os.fsencode(configured))
            digest.update(b"\0")
            digest.update(_semantic_path_digest(configured).encode("ascii"))
            digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _git_metadata(state: GitState) -> GitMetadata:
    _ensure_no_object_overrides(state.repository)
    git_dir = state.repository.git_dir
    common_dir = state.repository.common_dir
    return GitMetadata(
        head_ref=_head_ref(state.root),
        head_file_digest=_path_digest(git_dir / "HEAD"),
        index_digest=_index_digest(state.root),
        config_digest=_combined_digest(
            list(dict.fromkeys([common_dir / "config", git_dir / "config.worktree"]))
        ),
        effective_config_digest=_effective_config_digest(state.root),
        configured_files_digest=_configured_files_digest(state.root),
        excludes_digest=_combined_digest(
            list(dict.fromkeys([common_dir / "info" / "exclude", git_dir / "info" / "exclude"]))
        ),
        attributes_digest=_combined_digest(
            list(
                dict.fromkeys(
                    [common_dir / "info" / "attributes", git_dir / "info" / "attributes"]
                )
            )
        ),
        shallow_digest=_combined_digest(
            list(dict.fromkeys([common_dir / "shallow", git_dir / "shallow"]))
        ),
        grafts_digest=_combined_digest(_graft_paths(state.repository)),
        refs_digest=_refs_digest(state.root),
    )


def _ensure_index_is_merged(root: Path) -> None:
    unmerged = _git_bytes(
        ["ls-files", "--unmerged", "-z", "--"],
        root,
        error_code="index_scan_failed",
    )
    if unmerged:
        raise GitDiffError(
            "unmerged_index",
            "the Git index contains unresolved merge entries; resolve all conflicts first",
        )


def _index_entries(root: Path) -> list[_IndexEntry]:
    records = _split_z(
        _git_bytes(
            ["ls-files", "--stage", "-z", "--"],
            root,
            error_code="index_scan_failed",
        )
    )
    entries: list[_IndexEntry] = []
    object_length: int | None = None
    for record in records:
        if b"\t" not in record:
            raise GitDiffError("invalid_git_output", "Git returned invalid index metadata")
        header, raw_path = record.split(b"\t", 1)
        try:
            fields = header.decode("ascii", errors="strict").split(" ")
        except UnicodeDecodeError as exc:
            raise GitDiffError("invalid_git_output", "Git returned invalid index metadata") from exc
        if len(fields) != 3:
            raise GitDiffError("invalid_git_output", "Git returned invalid index metadata")
        mode, object_id, stage = fields
        if mode not in {"100644", "100755", "120000", "160000"}:
            raise GitDiffError("invalid_git_output", f"Git returned unsupported index mode {mode}")
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", object_id):
            raise GitDiffError("invalid_git_output", "Git returned invalid index object ID")
        if stage != "0":
            raise GitDiffError("unmerged_index", "the Git index contains unresolved entries")
        if object_length is None:
            object_length = len(object_id)
        elif len(object_id) != object_length:
            raise GitDiffError("invalid_git_output", "Git returned mixed object ID formats")
        path = _decode_path(raw_path)
        _validate_relative_path(path)
        entries.append(_IndexEntry(path=path, mode=mode, object_id=object_id.lower()))
    return entries


def _ensure_index_entries_are_visible(root: Path) -> None:
    """Reject index flags which can hide worktree changes from ``git diff``.

    ``git ls-files -v`` lower-cases the normal status tag for an
    assume-unchanged entry, while ``S`` denotes skip-worktree. Both flags make
    Git intentionally stop treating the worktree as ordinary candidate
    evidence. Dunnit cannot claim a complete scan in that state, so fail
    closed instead of trusting a potentially hidden file.
    """

    hidden: list[str] = []
    commands = (
        (["ls-files", "-v", "-z", "--"], True),
        (["ls-files", "-f", "-z", "--"], False),
    )
    for args, include_skip_worktree in commands:
        records = _split_z(_git_bytes(args, root, error_code="index_scan_failed"))
        for record in records:
            if len(record) < 3 or record[1:2] != b" ":
                raise GitDiffError(
                    "invalid_git_output", "Git returned invalid index visibility metadata"
                )
            tag = record[0]
            lower = ord("a") <= tag <= ord("z")
            if lower or (include_skip_worktree and tag == ord("S")):
                hidden.append(repr(_decode_path(record[2:])))
    hidden = list(dict.fromkeys(hidden))
    if hidden:
        rendered = ", ".join(hidden[:20])
        if len(hidden) > 20:
            rendered += f" (+{len(hidden) - 20} more)"
        raise GitDiffError(
            "hidden_index_entries",
            "the Git index uses assume-unchanged, skip-worktree, or fsmonitor-valid flags for "
            f"candidate paths: {rendered}",
        )


def read_blob_at_revision(cwd: Path, revision: str, path: str | Path) -> bytes:
    """Read a regular repository-relative policy blob from a resolved commit.

    This is intended for trust-root consumers such as CI policy loading: the
    candidate worktree cannot change bytes read from the target commit. The
    lookup uses a literal pathspec and rejects trees, symlinks, and gitlinks;
    Git blobs are returned verbatim and the worktree is never followed.
    """

    _validate_ref(revision)
    repository = discover_repository(cwd)
    _ensure_no_object_overrides(repository)
    shallow = _is_shallow(repository.root)
    sha = _resolve_commit(repository.root, revision, shallow=shallow)
    raw_path = os.fspath(path)
    normalized = raw_path.replace("\\", "/")
    if PureWindowsPath(normalized).drive:
        raise GitDiffError("invalid_path", f"path must be repository-relative: {raw_path!r}")
    _validate_relative_path(normalized, error_code="invalid_path")
    tree_result = _run_git(
        ["ls-tree", "-z", "--full-tree", sha, "--", f":(literal){normalized}"],
        repository.root,
        error_code="invalid_repository",
    )
    entries = _split_z(tree_result.stdout)
    if not entries:
        raise GitDiffError(
            "policy_not_found", f"path {normalized!r} does not exist at {sha}"
        )
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise GitDiffError("invalid_git_output", "Git returned invalid tree metadata")
    header, entry_raw_path = entries[0].split(b"\t", 1)
    entry_path = _decode_path(entry_raw_path)
    if entry_path != normalized:
        raise GitDiffError("invalid_git_output", "Git returned a non-exact tree path")
    parts = header.split(b" ")
    if len(parts) != 3:
        raise GitDiffError("invalid_git_output", "Git returned invalid tree entry metadata")
    mode, object_type, object_id = parts
    if mode not in {b"100644", b"100755"} or object_type != b"blob":
        kind = object_type.decode("ascii", errors="replace")
        raise GitDiffError(
            "invalid_policy_object",
            f"path {normalized!r} is not a regular file (mode {mode.decode()}, type {kind})",
        )
    object_id_text = object_id.decode("ascii", errors="strict")
    if not _is_full_object_id(object_id_text):
        raise GitDiffError("invalid_git_output", "Git returned an invalid policy object ID")
    result = _run_git(
        ["cat-file", "blob", object_id_text],
        repository.root,
        check=False,
    )
    if result.returncode != 0:
        raise GitDiffError("invalid_policy_object", f"cannot read policy blob {object_id_text}")
    return result.stdout


def _split_z(output: bytes) -> list[bytes]:
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if any(field == b"" for field in fields):
        raise GitDiffError("invalid_git_output", "Git returned an empty NUL-delimited field")
    return fields


def _validate_relative_path(path: str, *, error_code: str = "invalid_git_output") -> None:
    pure = PurePosixPath(path)
    unsafe_segment = any(part in {"", ".", ".."} for part in path.split("/"))
    if not path or pure.is_absolute() or unsafe_segment:
        raise GitDiffError(error_code, f"unsafe repository-relative path: {path!r}")


def _name_status_diffs(state: GitState, arguments: list[str], *, layer: str) -> list[FileDiff]:
    output = _git_bytes(
        [
            "diff",
            "--name-status",
            "-z",
            "-M",
            "--no-ext-diff",
            "--no-textconv",
            "--ita-visible-in-index",
            *arguments,
            "--",
        ],
        state.root,
        error_code="diff_failed",
    )
    fields = _split_z(output)
    files: list[FileDiff] = []
    index = 0
    while index < len(fields):
        try:
            status_field = fields[index].decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise GitDiffError("invalid_git_output", "Git returned a non-ASCII status") from exc
        index += 1
        if not re.fullmatch(r"[A-Z][0-9]*", status_field):
            raise GitDiffError("invalid_git_output", f"Git returned invalid status {status_field!r}")
        status = status_field[0]
        path_count = 2 if status in {"R", "C"} else 1
        if index + path_count > len(fields):
            raise GitDiffError("invalid_git_output", "Git returned truncated name-status data")
        if path_count == 2:
            old_path = _decode_path(fields[index])
            path = _decode_path(fields[index + 1])
            index += 2
        else:
            old_path = None
            path = _decode_path(fields[index])
            index += 1
        _validate_relative_path(path)
        if old_path is not None:
            _validate_relative_path(old_path)
        files.append(FileDiff(path=path, status=status, old_path=old_path, layer=layer))
    return files


def _ignored_pathspec(pattern: str) -> str:
    """Translate Dunnit's small glob language to a top-level Git pathspec.

    Git performs the initial narrowing so a broad ignored environment is not
    enumerated merely to find one protected file.  ``matches_any`` remains the
    authority after enumeration because its semantics are the public contract.
    """

    normalized = pattern.replace("\\", "/")
    rooted = normalized.startswith("/")
    normalized = normalized.lstrip("/")
    if normalized.endswith("/"):
        normalized += "**"

    has_wildcard = "*" in normalized or "?" in normalized
    if not has_wildcard and (rooted or "/" in normalized):
        return f":(top,literal){normalized}"

    # A slashless Dunnit pattern matches a basename at every depth.  Brackets
    # are literals in Dunnit's grammar but character classes in Git's glob
    # grammar, so quote them before asking Git to narrow the candidates.
    if not rooted and "/" not in normalized:
        normalized = "**/" + normalized
    normalized = normalized.replace("[", "\\[").replace("]", "\\]")
    return f":(top,glob){normalized}"


def _ignored_ancestor_paths(state: GitState, paths: list[str]) -> set[str]:
    """Return proper ancestor directories which Git itself considers ignored."""

    ancestors: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        ancestors.update("/".join(parts[:index]) for index in range(1, len(parts)))
    if not ancestors:
        return set()

    encoded = b"\0".join(os.fsencode(path) for path in sorted(ancestors)) + b"\0"
    result = _run_git(
        ["check-ignore", "--no-index", "-z", "--stdin"],
        state.root,
        error_code="untracked_scan_failed",
        check=False,
        input_data=encoded,
    )
    if result.returncode not in {0, 1}:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitDiffError(
            "untracked_scan_failed",
            detail or "git check-ignore failed while classifying ignored directories",
        )
    ignored = {_decode_path(value) for value in _split_z(result.stdout)}
    for path in ignored:
        _validate_relative_path(path)
    if not ignored.issubset(ancestors):
        raise GitDiffError("invalid_git_output", "Git returned an unexpected ignored ancestor")
    return ignored


def _untracked_paths(
    state: GitState,
    relevant_ignored_globs: list[str],
    always_relevant_ignored_globs: list[str],
) -> list[str]:
    output = _git_bytes(
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        state.root,
        error_code="untracked_scan_failed",
    )
    paths = [_decode_path(value) for value in _split_z(output)]
    if relevant_ignored_globs:
        pathspecs = list(dict.fromkeys(_ignored_pathspec(glob) for glob in relevant_ignored_globs))
        ignored_output = _git_bytes(
            [
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *pathspecs,
            ],
            state.root,
            error_code="untracked_scan_failed",
        )
        ignored_paths = list(
            dict.fromkeys(
                path
                for path in (_decode_path(value) for value in _split_z(ignored_output))
                if matches_any(path, relevant_ignored_globs)
            )
        )
        ignored_ancestors = _ignored_ancestor_paths(state, ignored_paths)
        paths.extend(
            path
            for path in ignored_paths
            if matches_any(path, always_relevant_ignored_globs)
            or not any(
                "/".join(PurePosixPath(path).parts[:index]) in ignored_ancestors
                for index in range(1, len(PurePosixPath(path).parts))
            )
        )
    paths = list(dict.fromkeys(paths))
    for path in paths:
        _validate_relative_path(path)
    return paths


def _repo_path(root: Path, path: str) -> Path:
    # Git emits '/' separators on every platform. Building a path from the
    # PurePosixPath parts avoids treating them as literal characters on Windows.
    return root.joinpath(*PurePosixPath(path).parts)


def _object_hasher(object_id_length: int, size: int) -> _Hash:
    if object_id_length == 40:
        digest = hashlib.sha1()
    elif object_id_length == 64:
        digest = hashlib.sha256()
    else:
        raise GitDiffError("invalid_git_output", "Git returned an unsupported object ID")
    digest.update(f"blob {size}\0".encode("ascii"))
    return digest


def _stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat.S_IFMT(info.st_mode),
        info.st_size,
        info.st_mtime_ns,
        info.st_dev,
        info.st_ino,
    )


def _normalized_piece(chunk: bytes, pending_cr: bool) -> tuple[bytes, bool]:
    if pending_cr:
        chunk = b"\r" + chunk
    if chunk.endswith(b"\r"):
        chunk = chunk[:-1]
        pending_cr = True
    else:
        pending_cr = False
    return chunk.replace(b"\r\n", b"\n"), pending_cr


def _open_regular(path: Path) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise OSError("candidate path stopped being a regular file")
    return stream


def _hash_regular_file(
    candidate: Path,
    expected: os.stat_result,
    object_id_length: int,
) -> tuple[str | None, str | None, str | None]:
    """Hash raw and CRLF-normalized worktree bytes without trusting index stats."""

    signature = _stat_signature(expected)
    raw = _object_hasher(object_id_length, expected.st_size)
    normalized_size = 0
    pending_cr = False
    has_nul = False
    total = 0
    try:
        with _open_regular(candidate) as stream:
            if _stat_signature(os.fstat(stream.fileno())) != signature:
                return None, None, "file changed before content hashing"
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                raw.update(chunk)
                has_nul = has_nul or b"\0" in chunk
                piece, pending_cr = _normalized_piece(chunk, pending_cr)
                normalized_size += len(piece)
            if pending_cr:
                normalized_size += 1
            if total != expected.st_size or _stat_signature(os.fstat(stream.fileno())) != signature:
                return None, None, "file changed while content was hashed"
        if _stat_signature(candidate.lstat()) != signature:
            return None, None, "file changed while content was hashed"
    except OSError as exc:
        return None, None, f"cannot hash file: {exc}"

    raw_object_id = raw.hexdigest()
    if has_nul or normalized_size == expected.st_size:
        return raw_object_id, None, None

    normalized = _object_hasher(object_id_length, normalized_size)
    pending_cr = False
    try:
        with _open_regular(candidate) as stream:
            if _stat_signature(os.fstat(stream.fileno())) != signature:
                return None, None, "file changed before normalized hashing"
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                piece, pending_cr = _normalized_piece(chunk, pending_cr)
                normalized.update(piece)
            if pending_cr:
                normalized.update(b"\r")
            if _stat_signature(os.fstat(stream.fileno())) != signature:
                return None, None, "file changed while normalized content was hashed"
        if _stat_signature(candidate.lstat()) != signature:
            return None, None, "file changed while normalized content was hashed"
    except OSError as exc:
        return None, None, f"cannot hash normalized file: {exc}"
    return raw_object_id, normalized.hexdigest(), None


def _worktree_identity(root: Path, entry: _IndexEntry) -> _WorktreeIdentity:
    candidate = _repo_path(root, entry.path)
    parent_reason = _unsafe_parent_reason(root, candidate)
    if parent_reason:
        return _WorktreeIdentity("unreadable", reason=parent_reason)
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return _WorktreeIdentity("absent")
    except OSError as exc:
        return _WorktreeIdentity("unreadable", reason=f"cannot inspect file: {exc}")

    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.fsencode(os.readlink(candidate))
        except OSError as exc:
            return _WorktreeIdentity("symlink", mode="120000", reason=str(exc))
        digest = _object_hasher(len(entry.object_id), len(target))
        digest.update(target)
        return _WorktreeIdentity("symlink", mode="120000", object_id=digest.hexdigest())
    if stat.S_ISREG(info.st_mode):
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            return _WorktreeIdentity("unreadable", reason=f"path escapes repository: {exc}")
        raw, normalized, reason = _hash_regular_file(candidate, info, len(entry.object_id))
        executable = bool(info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        mode = "100755" if executable and os.name != "nt" else "100644"
        return _WorktreeIdentity(
            "regular",
            mode=mode,
            object_id=raw,
            normalized_object_id=normalized,
            reason=reason,
        )
    if stat.S_ISDIR(info.st_mode):
        return _WorktreeIdentity("directory")
    return _WorktreeIdentity("special", reason="not a regular file or symlink")


def _unsafe_parent_reason(root: Path, candidate: Path) -> str | None:
    """Return why reading candidate could escape root, without following it."""

    current = root
    relative = candidate.relative_to(root)
    for part in relative.parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            return f"unreadable parent: {exc}"
        if stat.S_ISLNK(info.st_mode):
            return "symlinked parent"
        if not stat.S_ISDIR(info.st_mode):
            return "non-directory parent"
    return None


def _worktree_content(root: Path, path: str) -> _Content:
    candidate = _repo_path(root, path)
    parent_reason = _unsafe_parent_reason(root, candidate)
    if parent_reason:
        return _Content(None, None, parent_reason)
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return _Content(None, None, "file disappeared during scan")
    except OSError as exc:
        return _Content(None, None, f"cannot inspect file: {exc}")

    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.readlink(candidate)
        except OSError as exc:
            return _Content(None, info.st_size, f"cannot read symlink: {exc}", symlink=True)
        return _Content(os.fsencode(target), info.st_size, symlink=True)
    if not stat.S_ISREG(info.st_mode):
        return _Content(None, info.st_size, "not a regular file")
    if info.st_size > MAX_SCANNED_BYTES:
        return _Content(None, info.st_size, f"content exceeds {MAX_SCANNED_BYTES} bytes")

    # Resolve only after lstat/parent checks. This catches Windows junctions and
    # other platform-specific indirections without ever opening an outside file.
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        return _Content(None, info.st_size, f"path resolves outside repository: {exc}")
    try:
        return _Content(candidate.read_bytes(), info.st_size)
    except OSError as exc:
        return _Content(None, info.st_size, f"cannot read file: {exc}")


def _blob_size(root: Path, object_name: str) -> int | None:
    result = _run_git(["cat-file", "-s", object_name], root, check=False)
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise GitDiffError("invalid_git_output", "Git returned an invalid blob size") from exc


def _git_content(root: Path, object_name: str, *, missing: str) -> _Content:
    size = _blob_size(root, object_name)
    if size is None:
        return _Content(None, None, missing)
    if size > MAX_SCANNED_BYTES:
        return _Content(None, size, f"content exceeds {MAX_SCANNED_BYTES} bytes")
    result = _run_git(["cat-file", "blob", object_name], root, check=False)
    if result.returncode != 0:
        return _Content(None, size, missing)
    return _Content(result.stdout, size)


def _tree_content(state: GitState, revision: str, path: str) -> _Content:
    return _git_content(
        state.root,
        f"{revision}:{path}",
        missing=f"blob is unavailable at {revision}",
    )


def _index_content(state: GitState, path: str) -> _Content:
    return _git_content(state.root, f":{path}", missing="index blob is unavailable")


def _decode_text(content: _Content) -> tuple[list[str] | None, str | None, bool]:
    if content.data is None:
        return None, content.reason, content.binary
    if b"\0" in content.data:
        return None, "binary content", True
    try:
        value = content.data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "content is not UTF-8 text", True
    return value.splitlines(), None, False


def _populate_transition(item: FileDiff, old: _Content, new: _Content) -> None:
    item.old_size = old.size
    item.new_size = new.size
    item.symlink = old.symlink or new.symlink

    old_lines, old_reason, old_binary = _decode_text(old)
    new_lines, new_reason, new_binary = _decode_text(new)
    item.binary = old_binary or new_binary
    reasons = [reason for reason in (old_reason, new_reason) if reason]
    if old_lines is None or new_lines is None:
        item.content_scanned = False
        item.scan_reason = "; ".join(dict.fromkeys(reasons)) or "content unavailable"
        return

    if len(old_lines) > MAX_SCANNED_LINES or len(new_lines) > MAX_SCANNED_LINES:
        item.content_scanned = False
        item.scan_reason = f"content exceeds {MAX_SCANNED_LINES} lines"
        return

    # Detector rules need net line additions/removals, not an edit script.
    # Counter excesses are deterministic O(n) even for adversarial repeated or
    # entirely unique inputs; filtering in source order retains enough local
    # order for empty-test/config heuristics.
    old_counts = Counter(old_lines)
    new_counts = Counter(new_lines)
    removed = old_counts - new_counts
    added = new_counts - old_counts
    for line in old_lines:
        if removed[line] > 0:
            item.removed_lines.append(line)
            removed[line] -= 1
    for line in new_lines:
        if added[line] > 0:
            item.added_lines.append(line)
            added[line] -= 1


def _populate_layer(
    items: list[FileDiff],
    *,
    old_content: Callable[[str], _Content],
    new_content: Callable[[str], _Content],
) -> None:
    """Populate a named Git layer from two path-to-content callables."""

    for item in items:
        old = (
            _Content(b"", 0)
            if item.status == "A"
            else old_content(item.old_path or item.path)
        )
        new = (
            _Content(b"", 0)
            if item.status == "D"
            else new_content(item.path)
        )
        _populate_transition(item, old, new)


def _direct_worktree_diffs(
    state: GitState,
    represented_paths: set[str],
) -> list[FileDiff]:
    """Find index/worktree changes by bytes, independent of Git's stat cache."""

    entries = _index_entries(state.root)
    files: list[FileDiff] = []
    for entry in entries:
        identity = _worktree_identity(state.root, entry)
        index_kind = (
            "regular"
            if entry.mode in {"100644", "100755"}
            else "symlink"
            if entry.mode == "120000"
            else "directory"
        )

        # Gitlinks are delegated to Git's submodule-aware diff. Directly
        # hashing a nested repository would cross a separate trust boundary.
        if entry.mode == "160000" and identity.kind == "directory":
            continue

        status: str | None = None
        if identity.kind == "absent":
            status = "D"
        elif (
            identity.reason is not None
            or identity.kind in {"unreadable", "special"}
            or identity.kind != index_kind
        ):
            status = "T"
        elif set(entry.object_id) == {"0"}:
            status = "A"
        elif entry.object_id not in {
            identity.object_id,
            # Git's built-in text conversion may keep a CRLF worktree for an
            # LF index blob. Compare that deterministic representation too;
            # no external clean/smudge filters are executed.
            identity.normalized_object_id,
        } or identity.mode != entry.mode:
            status = "M"

        if status is None or entry.path in represented_paths:
            continue
        item = FileDiff(path=entry.path, status=status, layer="worktree")
        if status == "A" or set(entry.object_id) == {"0"}:
            old = _Content(b"", 0)
        else:
            old = _index_content(state, entry.path)
        new = _Content(b"", 0) if status == "D" else _worktree_content(state.root, entry.path)
        _populate_transition(item, old, new)
        if identity.reason and item.content_scanned:
            item.content_scanned = False
            item.scan_reason = identity.reason
        files.append(item)
    return files


def _tracked_layers(state: GitState) -> list[FileDiff]:
    files: list[FileDiff] = []
    # The empty tree is object-format dependent. ``resolve_git_state`` has
    # already asked this repository to compute the correct ID for an unborn
    # SHA-1 or SHA-256 repository.
    head_tree = state.head_sha or state.baseline_sha

    if state.head_sha is not None and state.baseline_sha != state.head_sha:
        committed = _name_status_diffs(
            state,
            [state.baseline_sha, state.head_sha],
            layer="committed",
        )
        _populate_layer(
            committed,
            old_content=lambda path: _tree_content(state, state.baseline_sha, path),
            new_content=lambda path: _tree_content(state, state.head_sha or head_tree, path),
        )
        files.extend(committed)

    staged = _name_status_diffs(state, ["--cached", head_tree], layer="staged")
    _populate_layer(
        staged,
        old_content=lambda path: _tree_content(state, head_tree, path),
        new_content=lambda path: _index_content(state, path),
    )
    files.extend(staged)

    worktree = _name_status_diffs(state, [], layer="worktree")
    _populate_layer(
        worktree,
        old_content=lambda path: _index_content(state, path),
        new_content=lambda path: _worktree_content(state.root, path),
    )
    files.extend(worktree)
    represented = {item.path for item in worktree}
    represented.update(item.old_path for item in worktree if item.old_path is not None)
    files.extend(_direct_worktree_diffs(state, represented))
    return files


def collect_diff_snapshot(
    cwd: Path,
    base: str | None,
    *,
    pinned_state: GitState | None = None,
    relevant_ignored_globs: list[str] | None = None,
    always_relevant_ignored_globs: list[str] | None = None,
) -> DiffSnapshot:
    """Collect layered candidate paths and bounded text-content transitions.

    ``pinned_state`` is used only for a post-command scan. It preserves the
    original full target and baseline IDs while re-reading current HEAD/index.
    Ignored paths are retained only when they match a trusted policy glob. A
    broad content glob (for example ``**/test_*.py``) does not pull files from
    a wholly ignored generated directory, while an ``always_relevant`` policy,
    protected, requirement, or write glob is enforced even inside one.
    """

    state = (
        resolve_git_state(Path(cwd), base)
        if pinned_state is None
        else _resolve_pinned_state(Path(cwd), pinned_state)
    )
    _ensure_index_is_merged(state.root)
    # Reject flags already present when verification starts. If a proof
    # command introduces one later, the pinned snapshot still captures the
    # changed logical index digest and the runner reports a Git-state mutation.
    # That definite failure is sufficient even if the new flag hides a
    # post-command worktree edit.
    if pinned_state is None:
        _ensure_index_entries_are_visible(state.root)
    before_metadata = _git_metadata(state)
    files = _tracked_layers(state)
    worktree_rename_targets = {
        item.path for item in files if item.layer == "worktree" and item.status in {"R", "C"}
    }
    ignored_globs = relevant_ignored_globs or []
    # Callers predating ``always_relevant_ignored_globs`` expected every
    # matching ignored path. New trust-aware callers pass the narrower set
    # explicitly so broad test globs do not sweep dependency environments.
    always_ignored = (
        ignored_globs
        if always_relevant_ignored_globs is None
        else always_relevant_ignored_globs
    )
    for path in _untracked_paths(state, ignored_globs, always_ignored):
        if path in worktree_rename_targets:
            continue
        # A path can legitimately appear in a tracked transition and still be
        # untracked now (for example, a staged deletion followed by recreating
        # the same pathname). Preserve that current untracked layer as distinct
        # evidence so its content and scan completeness are never omitted.
        item = FileDiff(path=path, status="A", layer="untracked")
        _populate_transition(item, _Content(b"", 0), _worktree_content(state.root, path))
        files.append(item)
    after_metadata = _git_metadata(state)
    if before_metadata != after_metadata:
        raise GitDiffError(
            "git_changed_during_scan",
            "HEAD, index, or critical Git metadata changed while collecting the candidate",
        )
    return DiffSnapshot(state=state, files=files, git_metadata=after_metadata)


def collect_diff(cwd: Path, base: str | None) -> list[FileDiff]:
    """Compatibility API: return candidate file diffs as a plain list."""

    return collect_diff_snapshot(cwd, base).files


@lru_cache(maxsize=1024)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into a regex over posix paths.

    ``**`` crosses directories, ``*``/``?`` do not. A pattern with no slash
    (``*.cfg``, ``dod.yaml``) matches at any depth; a leading ``/`` anchors
    it to the repo root; a trailing ``/`` means "everything under this
    directory".
    """

    pat = pattern.replace("\\", "/")
    rooted = pat.startswith("/")
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    if "/" not in pat and not rooted:
        pat = "**/" + pat
    out: list[str] = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(path: str, globs: list[str]) -> bool:
    path = path.replace("\\", "/")
    return any(_glob_regex(glob).match(path) for glob in globs)
