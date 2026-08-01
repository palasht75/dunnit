import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import dunnit.gitdiff as gitdiff_module
from dunnit.gitdiff import (
    EMPTY_TREE_SHA,
    GitDiffError,
    collect_diff,
    collect_diff_snapshot,
    discover_repository,
    matches_any,
    read_blob_at_revision,
    resolve_git_state,
)


def _completed(
    *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _changed_stat(info):
    return SimpleNamespace(
        st_mode=info.st_mode,
        st_size=info.st_size,
        st_mtime_ns=info.st_mtime_ns + 1,
        st_dev=info.st_dev,
        st_ino=info.st_ino,
    )


@pytest.mark.parametrize(
    ("path", "glob", "expected"),
    [
        ("tests/a/b.py", "tests/**", True),
        ("tests/a.py", "tests/**", True),
        ("src/tests.py", "tests/**", False),
        ("test_root.py", "**/test_*.py", True),
        ("pkg/sub/test_x.py", "**/test_*.py", True),
        ("pkg/xtest_x.py", "**/test_*.py", False),
        ("a/b/c.cfg", "*.cfg", True),
        (".github/workflows/ci.yml", ".github/**", True),
        ("dod.yaml", "dod.yaml", True),
        ("sub/dod.yaml", "dod.yaml", True),
        ("mydod.yaml", "dod.yaml", False),
        ("pkg/calc_test.go", "**/*_test.go", True),
        ("docs/index.md", "docs/", True),
        ("app.test.ts", "**/*.test.*", True),
        ("dod.yaml", "/dod.yaml", True),
        ("examples/dod.yaml", "/dod.yaml", False),
    ],
)
def test_matches_any(path, glob, expected):
    assert matches_any(path, [glob]) is expected


def test_untracked_files_included(repo):
    (repo / "brand_new.py").write_text("x = 1\n")
    diffs = {d.path: d for d in collect_diff(repo, None)}
    assert diffs["brand_new.py"].status == "A"
    assert "x = 1" in diffs["brand_new.py"].added_lines


def test_untracked_binary_path_is_included_and_content_is_incomplete(repo):
    (repo / "img.bin").write_bytes(b"\x00\xff\xfe binary")
    snapshot = collect_diff_snapshot(repo, None)
    diff = {item.path: item for item in snapshot.files}["img.bin"]
    assert diff.status == "A"
    assert diff.binary is True
    assert diff.content_scanned is False
    assert diff.scan_reason == "binary content"
    assert snapshot.paths_complete is True
    assert snapshot.content_complete is False
    assert snapshot.incomplete_paths == ["img.bin"]


def test_tracked_binary_path_is_included_and_content_is_incomplete(repo):
    (repo / "app.py").write_bytes(b"\x00compiled candidate")

    snapshot = collect_diff_snapshot(repo, None)
    diff = {item.path: item for item in snapshot.files}["app.py"]

    assert diff.status == "M"
    assert diff.binary is True
    assert diff.content_scanned is False
    assert "app.py" in snapshot.incomplete_paths


def test_rename_records_old_path(repo):
    subprocess.run(["git", "mv", "app.py", "core.py"], cwd=repo, check=True, capture_output=True)
    diffs = {d.path: d for d in collect_diff(repo, None)}
    assert diffs["core.py"].status == "R"
    assert diffs["core.py"].old_path == "app.py"


def test_modified_file_lines_collected(repo):
    (repo / "app.py").write_text("def add(a, b):\n    return a + b + 0\n")
    diffs = {d.path: d for d in collect_diff(repo, None)}
    assert "    return a + b + 0" in diffs["app.py"].added_lines
    assert "    return a + b" in diffs["app.py"].removed_lines


def test_committed_staged_and_worktree_transitions_are_preserved(repo):
    base_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("value = 'committed'\n")
    _run_git(repo, "add", "app.py")
    _run_git(repo, "commit", "-qm", "candidate commit")
    (repo / "app.py").write_text("value = 'staged'\n")
    _run_git(repo, "add", "app.py")
    (repo / "app.py").write_text("value = 'worktree'\n")

    records = [
        item for item in collect_diff_snapshot(repo, base_sha).files if item.path == "app.py"
    ]

    assert [item.layer for item in records] == ["committed", "staged", "worktree"]
    assert records[0].added_lines == ["value = 'committed'"]
    assert records[1].added_lines == ["value = 'staged'"]
    assert records[2].added_lines == ["value = 'worktree'"]


def test_staged_weakening_is_visible_when_worktree_is_restored(repo):
    test_path = repo / "tests" / "test_app.py"
    original = test_path.read_text()
    weakened = original.replace("def test_add():", "@pytest.mark.skip\ndef test_add():")
    test_path.write_text("import pytest\n" + weakened)
    _run_git(repo, "add", "tests/test_app.py")
    test_path.write_text(original)

    records = [
        item for item in collect_diff_snapshot(repo, None).files if item.path == "tests/test_app.py"
    ]

    assert [item.layer for item in records] == ["staged", "worktree"]
    assert "@pytest.mark.skip" in records[0].added_lines
    assert "@pytest.mark.skip" in records[1].removed_lines


def test_recreated_path_after_staged_deletion_keeps_untracked_scan_evidence(repo):
    _run_git(repo, "rm", "app.py")
    (repo / "app.py").write_bytes(b"x" * (gitdiff_module.MAX_SCANNED_BYTES + 1))

    snapshot = collect_diff_snapshot(repo, None)
    records = [item for item in snapshot.files if item.path == "app.py"]

    assert [item.layer for item in records] == ["staged", "untracked"]
    assert records[0].status == "D"
    assert records[1].status == "A"
    assert records[1].content_scanned is False
    assert snapshot.content_complete is False
    assert snapshot.incomplete_paths == ["app.py"]


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_flags_cannot_hide_modified_candidate_files(repo, flag):
    _run_git(repo, "update-index", flag, "tests/test_app.py")
    (repo / "tests" / "test_app.py").write_text("def test_hidden():\n    pass\n")

    with pytest.raises(GitDiffError) as exc_info:
        collect_diff_snapshot(repo, None)

    assert exc_info.value.code == "hidden_index_entries"
    assert "tests/test_app.py" in str(exc_info.value)


def test_discovers_worktree_root_from_nested_monorepo_directory(repo):
    nested = repo / "packages" / "service" / "src"
    nested.mkdir(parents=True)
    (nested / "new.py").write_text("created = True\n")

    layout = discover_repository(nested)
    snapshot = collect_diff_snapshot(nested, None)

    assert layout.root == repo.resolve()
    assert "packages/service/src/new.py" in {item.path for item in snapshot.files}


def test_ref_resolution_records_full_target_head_and_merge_base(repo, commit):
    base_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n")
    commit("candidate")
    head_sha = _git(repo, "rev-parse", "HEAD")

    state = resolve_git_state(repo, base_sha)

    assert state.target_sha == base_sha
    assert state.head_sha == head_sha
    assert state.merge_base_sha == base_sha
    assert state.baseline_sha == base_sha
    assert state.requested_ref == base_sha
    assert all(len(sha) == 40 for sha in (state.target_sha, state.head_sha, state.merge_base_sha))


def test_blob_reader_uses_committed_revision_not_candidate_worktree(repo):
    trusted_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "dod.yaml").write_text("candidate: weakened\n")
    _run_git(repo, "add", "dod.yaml")

    # The path does not exist at the trusted revision, even though the
    # candidate has staged it.
    with pytest.raises(GitDiffError) as exc_info:
        read_blob_at_revision(repo, trusted_sha, "dod.yaml")
    assert exc_info.value.code == "policy_not_found"

    trusted_app = read_blob_at_revision(repo, trusted_sha, "app.py")
    (repo / "app.py").write_text("candidate = 'different'\n")
    assert read_blob_at_revision(repo, trusted_sha, "app.py") == trusted_app
    assert b"return a + b" in trusted_app


@pytest.mark.parametrize(
    "path", ["../dod.yaml", "tests/../dod.yaml", "/tmp/dod.yaml", "C:\\tmp\\dod.yaml", ".", ""]
)
def test_blob_reader_rejects_paths_outside_repository(repo, path):
    with pytest.raises(GitDiffError) as exc_info:
        read_blob_at_revision(repo, "HEAD", path)
    assert exc_info.value.code == "invalid_path"


def test_blob_reader_uses_literal_exact_pathspec(repo):
    path = "[policy].yaml"
    (repo / path).write_text("version: 2\n")
    _run_git(repo, "add", path)
    _run_git(repo, "commit", "-qm", "add literal policy path")

    assert read_blob_at_revision(repo, "HEAD", path) == b"version: 2\n"


def test_blob_reader_rejects_tree_policy_object(repo):
    with pytest.raises(GitDiffError) as exc_info:
        read_blob_at_revision(repo, "HEAD", "tests")
    assert exc_info.value.code == "invalid_policy_object"


def test_blob_reader_rejects_symlink_policy_object(repo, tmp_path):
    target = tmp_path / "outside-policy.yaml"
    target.write_text("version: 2\n")
    link = repo / "dod-link.yaml"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")
    _run_git(repo, "add", "dod-link.yaml")
    _run_git(repo, "commit", "-qm", "add policy symlink")

    with pytest.raises(GitDiffError) as exc_info:
        read_blob_at_revision(repo, "HEAD", "dod-link.yaml")
    assert exc_info.value.code == "invalid_policy_object"


@pytest.mark.parametrize("ref", ["--output=/tmp/pwn", "-n", "", "HEAD\n--help"])
def test_unsafe_or_empty_comparison_refs_fail_closed(repo, ref):
    with pytest.raises(GitDiffError) as exc_info:
        resolve_git_state(repo, ref)
    assert exc_info.value.code == "invalid_ref"


def test_unknown_ref_fails_closed(repo):
    with pytest.raises(GitDiffError) as exc_info:
        collect_diff(repo, "refs/heads/does-not-exist")
    assert exc_info.value.code == "invalid_ref"


def test_git_replacement_refs_are_rejected_before_object_resolution(repo):
    original = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("replacement = True\n")
    _run_git(repo, "add", "app.py")
    _run_git(repo, "commit", "-qm", "replacement object")
    replacement = _git(repo, "rev-parse", "HEAD")
    _run_git(repo, "replace", original, replacement)

    with pytest.raises(GitDiffError) as exc_info:
        collect_diff_snapshot(repo, original)

    assert exc_info.value.code == "replace_refs_present"


def test_git_grafts_are_rejected_before_merge_base_resolution(repo):
    layout = discover_repository(repo)
    grafts = layout.common_dir / "info" / "grafts"
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(_git(repo, "rev-parse", "HEAD") + "\n")

    with pytest.raises(GitDiffError) as exc_info:
        collect_diff_snapshot(repo, "HEAD")

    assert exc_info.value.code == "grafts_present"


def test_direct_worktree_hashing_does_not_trust_git_stat_cache(repo, monkeypatch):
    original_name_status = gitdiff_module._name_status_diffs

    def hide_worktree_status(state, arguments, *, layer):
        if layer == "worktree":
            return []
        return original_name_status(state, arguments, layer=layer)

    monkeypatch.setattr(gitdiff_module, "_name_status_diffs", hide_worktree_status)
    path = repo / "app.py"
    before = path.stat()
    original = path.read_bytes()
    path.write_bytes(original.replace(b"a + b", b"a - b"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    snapshot = collect_diff_snapshot(repo, None)
    records = [item for item in snapshot.files if item.path == "app.py"]

    assert len(records) == 1
    assert records[0].layer == "worktree"
    assert records[0].status == "M"
    assert "    return a - b" in records[0].added_lines


@pytest.mark.skipif(os.name == "nt", reason="Windows does not preserve executable mode bits")
def test_direct_worktree_comparison_does_not_honor_core_filemode_hiding(repo):
    script = repo / "script.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o644)
    _run_git(repo, "add", "script.sh")
    _run_git(repo, "commit", "-qm", "add script")
    _run_git(repo, "config", "core.fileMode", "false")
    script.chmod(0o755)

    snapshot = collect_diff_snapshot(repo, None)
    record = next(item for item in snapshot.files if item.path == "script.sh")

    assert record.layer == "worktree"
    assert record.status == "M"


def test_git_plumbing_explicitly_disables_fsmonitor_and_untracked_cache(repo, monkeypatch):
    observed: list[list[str]] = []
    original = gitdiff_module.subprocess.run

    def capture(args, **kwargs):
        observed.append(list(args))
        return original(args, **kwargs)

    monkeypatch.setattr(gitdiff_module.subprocess, "run", capture)

    collect_diff_snapshot(repo, None)

    assert observed
    assert all("core.fsmonitor=false" in args for args in observed)
    assert all("core.untrackedCache=false" in args for args in observed)


@pytest.mark.parametrize(
    "output",
    [
        b"missing-tab\0",
        b"\xff\tpath\0",
        b"100644 only-two\tpath\0",
        b"100600 " + b"a" * 40 + b" 0\tpath\0",
        b"100644 not-an-object 0\tpath\0",
        b"100644 " + b"a" * 40 + b" 2\tpath\0",
        (
            b"100644 "
            + b"a" * 40
            + b" 0\tone\0"
            + b"100644 "
            + b"b" * 64
            + b" 0\ttwo\0"
        ),
        b"100644 " + b"a" * 40 + b" 0\t../outside\0",
    ],
)
def test_invalid_index_metadata_fails_closed(repo, monkeypatch, output):
    monkeypatch.setattr(gitdiff_module, "_git_bytes", lambda *args, **kwargs: output)

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._index_entries(repo)

    assert exc_info.value.code in {"invalid_git_output", "unmerged_index"}


def test_object_hasher_rejects_unknown_object_format():
    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._object_hasher(48, 0)
    assert exc_info.value.code == "invalid_git_output"


def test_crlf_normalizer_handles_chunk_boundary_and_trailing_carriage_return():
    first, pending = gitdiff_module._normalized_piece(b"line\r", False)
    second, pending = gitdiff_module._normalized_piece(b"\nnext\r", pending)

    assert first == b"line"
    assert second == b"\nnext"
    assert pending is True


def test_regular_file_hashing_covers_crlf_and_trailing_carriage_return(tmp_path):
    path = tmp_path / "line-endings.txt"
    path.write_bytes(b"first\r\nlast\r")

    raw, normalized, reason = gitdiff_module._hash_regular_file(path, path.lstat(), 40)

    assert raw is not None
    assert normalized is not None
    assert raw != normalized
    assert reason is None


def test_direct_identity_hashes_tracked_symlink(repo):
    link = repo / "tracked-link"
    try:
        link.symlink_to("app.py")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")
    _run_git(repo, "add", "tracked-link")
    _run_git(repo, "commit", "-qm", "add tracked symlink")
    link.unlink()
    link.symlink_to("tests/test_app.py")

    snapshot = collect_diff_snapshot(repo, None)
    record = next(item for item in snapshot.files if item.path == "tracked-link")

    assert record.status == "M"
    assert record.symlink is True


def test_discovery_rejects_core_worktree_redirect_outside_invocation(repo):
    redirected = repo.parent / (repo.name + "-redirected")
    redirected.mkdir()
    _run_git(repo, "config", "core.worktree", str(redirected))
    git_pointer = redirected / ".git"
    git_pointer.write_text(f"gitdir: {(repo / '.git').as_posix()}\n")

    with pytest.raises(GitDiffError) as exc_info:
        discover_repository(repo)

    assert exc_info.value.code == "invalid_repository"


def test_non_repository_fails_closed(tmp_path):
    with pytest.raises(GitDiffError) as exc_info:
        collect_diff(tmp_path, None)
    assert exc_info.value.code == "not_worktree"


def test_unborn_repository_uses_empty_tree_and_includes_staged_and_untracked(tmp_path):
    _run_git(tmp_path, "init", "-q")
    (tmp_path / "staged.py").write_text("staged = True\n")
    (tmp_path / "untracked.py").write_text("untracked = True\n")
    _run_git(tmp_path, "add", "staged.py")

    snapshot = collect_diff_snapshot(tmp_path, None)
    files = {item.path: item for item in snapshot.files}

    assert snapshot.state.unborn is True
    assert snapshot.state.head_sha is None
    assert snapshot.state.target_sha is None
    assert snapshot.state.merge_base_sha is None
    assert snapshot.state.baseline_sha == EMPTY_TREE_SHA
    assert set(files) == {"staged.py", "untracked.py"}
    assert files["staged.py"].added_lines == ["staged = True"]
    assert files["untracked.py"].added_lines == ["untracked = True"]


def test_unborn_sha256_repository_uses_its_native_empty_tree(tmp_path):
    initialized = subprocess.run(
        ["git", "init", "-q", "--object-format=sha256"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
    )
    if initialized.returncode != 0:
        pytest.skip("local Git does not support SHA-256 repositories")
    (tmp_path / "staged.py").write_text("staged = True\n")
    _run_git(tmp_path, "add", "staged.py")

    snapshot = collect_diff_snapshot(tmp_path, None)

    assert snapshot.state.unborn is True
    assert len(snapshot.state.baseline_sha) == 64
    assert snapshot.state.baseline_sha != EMPTY_TREE_SHA
    assert "staged.py" in {item.path for item in snapshot.files}


def test_detached_head_is_supported(repo):
    _run_git(repo, "checkout", "--detach", "-q")
    (repo / "detached.txt").write_text("candidate\n")

    snapshot = collect_diff_snapshot(repo, None)

    assert snapshot.state.unborn is False
    assert snapshot.state.head_sha == _git(repo, "rev-parse", "HEAD")
    assert "detached.txt" in {item.path for item in snapshot.files}


def test_linked_worktree_uses_its_root_and_shared_common_dir(repo, tmp_path):
    worktree = tmp_path / "linked worktree"
    _run_git(repo, "worktree", "add", "--detach", "-q", str(worktree), "HEAD")
    try:
        (worktree / "worktree-only.txt").write_text("linked\n")
        layout = discover_repository(worktree)
        snapshot = collect_diff_snapshot(worktree, None)

        assert layout.root == worktree.resolve()
        assert layout.git_dir != layout.common_dir
        assert layout.common_dir == (repo / ".git").resolve()
        assert "worktree-only.txt" in {item.path for item in snapshot.files}
    finally:
        _run_git(repo, "worktree", "remove", "--force", str(worktree))


def test_paths_with_spaces_and_unicode_are_not_quoted_or_split(repo):
    names = ["folder with spaces/new file.py", "café-雪.py"]
    for name in names:
        path = repo.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"name = {name!r}\n", encoding="utf-8")

    files = {item.path: item for item in collect_diff(repo, None)}

    assert set(names).issubset(files)
    assert all(files[name].content_scanned for name in names)


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids tabs in file names")
def test_tab_in_path_is_preserved_by_nul_delimited_collection(repo):
    name = "tests/tab\tname.py"
    (repo / name).write_text("def test_tab():\n    assert True\n")

    files = {item.path: item for item in collect_diff(repo, None)}

    assert name in files
    assert files[name].added_lines[-1] == "    assert True"


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids tabs in file names")
def test_tab_in_tracked_rename_is_preserved_by_nul_status_parser(repo):
    old_name = "old\tname.py"
    new_name = "new\tname.py"
    (repo / old_name).write_text("value = 1\n")
    _run_git(repo, "add", old_name)
    _run_git(repo, "commit", "-qm", "add unusual path")
    _run_git(repo, "mv", old_name, new_name)

    diff = {item.path: item for item in collect_diff(repo, None)}[new_name]

    assert diff.status == "R"
    assert diff.old_path == old_name


def test_crlf_content_is_compared_as_lines(repo):
    (repo / "app.py").write_bytes(
        b"def add(a, b):\r\n    value = a + b\r\n    return value\r\n"
    )

    diff = {item.path: item for item in collect_diff(repo, None)}["app.py"]

    assert diff.content_scanned is True
    assert "    value = a + b" in diff.added_lines
    assert "    return a + b" in diff.removed_lines


def test_every_untracked_path_is_enumerated_beyond_previous_cap(repo):
    generated = repo / "generated"
    generated.mkdir()
    for index in range(1_005):
        (generated / f"item-{index:04}.txt").write_text(f"{index}\n")

    snapshot = collect_diff_snapshot(repo, None)
    generated_diffs = [item for item in snapshot.files if item.path.startswith("generated/")]

    assert len(generated_diffs) == 1_005
    assert snapshot.paths_complete is True
    assert snapshot.content_complete is True


def test_policy_relevant_ignored_untracked_path_is_not_silently_omitted(repo):
    (repo / ".gitignore").write_text("protected.cfg\nignored-cache/**\n")
    _run_git(repo, "add", ".gitignore")
    _run_git(repo, "commit", "-qm", "ignore generated files")
    (repo / "protected.cfg").write_text("candidate policy input\n")
    cache = repo / "ignored-cache"
    cache.mkdir()
    (cache / "large.bin").write_bytes(b"\0" * 1_000_001)

    snapshot = collect_diff_snapshot(
        repo,
        None,
        relevant_ignored_globs=["protected.cfg"],
    )

    assert "protected.cfg" in {item.path for item in snapshot.files}
    assert not any(item.path.startswith("ignored-cache/") for item in snapshot.files)
    assert snapshot.scan_complete is True


def test_ignored_test_file_is_scanned_without_sweeping_ignored_environment(repo):
    (repo / ".gitignore").write_text(".venv/\ntests/test_hidden.py\n")
    _run_git(repo, "add", ".gitignore")
    _run_git(repo, "commit", "-qm", "ignore generated and hidden test files")
    hidden = repo / "tests" / "test_hidden.py"
    hidden.write_text("def test_hidden():\n    assert False\n")
    dependency_test = repo / ".venv" / "lib" / "dependency" / "tests" / "test_dep.py"
    dependency_test.parent.mkdir(parents=True)
    dependency_test.write_text("def test_dependency():\n    assert True\n")

    snapshot = collect_diff_snapshot(
        repo,
        None,
        relevant_ignored_globs=["**/test_*.py"],
        always_relevant_ignored_globs=[],
    )

    paths = {item.path for item in snapshot.files}
    assert "tests/test_hidden.py" in paths
    assert not any(path.startswith(".venv/") for path in paths)


def test_exact_protected_ignored_path_survives_ignored_parent_filter(repo):
    (repo / ".gitignore").write_text("generated-policy/\n")
    _run_git(repo, "add", ".gitignore")
    _run_git(repo, "commit", "-qm", "ignore generated policy directory")
    policy = repo / "generated-policy" / "dod.yaml"
    policy.parent.mkdir()
    policy.write_text("candidate policy\n")

    snapshot = collect_diff_snapshot(
        repo,
        None,
        relevant_ignored_globs=["generated-policy/dod.yaml", "**/test_*.py"],
        always_relevant_ignored_globs=["generated-policy/dod.yaml"],
    )

    assert "generated-policy/dod.yaml" in {item.path for item in snapshot.files}


def test_unmerged_index_is_an_actionable_error(repo):
    original_branch = _git(repo, "branch", "--show-current")
    _run_git(repo, "checkout", "-qb", "conflicting-side")
    (repo / "app.py").write_text("side = True\n")
    _run_git(repo, "commit", "-qam", "side change")
    _run_git(repo, "checkout", "-q", original_branch)
    (repo / "app.py").write_text("main = True\n")
    _run_git(repo, "commit", "-qam", "main change")
    merge = subprocess.run(
        ["git", "merge", "conflicting-side"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    assert merge.returncode != 0

    with pytest.raises(GitDiffError) as exc_info:
        collect_diff_snapshot(repo, None)

    assert exc_info.value.code == "unmerged_index"


def test_large_unique_line_replacement_uses_bounded_net_accounting(repo):
    path = repo / "large-lines.txt"
    path.write_text("".join(f"old-{index:05d}\n" for index in range(10_000)))
    _run_git(repo, "add", "large-lines.txt")
    _run_git(repo, "commit", "-qm", "large baseline")
    path.write_text("".join(f"new-{index:05d}\n" for index in range(10_000)))

    diff = {item.path: item for item in collect_diff(repo, None)}["large-lines.txt"]

    assert len(diff.removed_lines) == 10_000
    assert len(diff.added_lines) == 10_000


def test_oversized_git_metadata_is_rejected_before_hashing(tmp_path, monkeypatch):
    metadata = tmp_path / "config"
    with metadata.open("wb") as stream:
        stream.truncate(gitdiff_module.MAX_GIT_METADATA_BYTES + 1)

    def must_not_open(*args, **kwargs):
        raise AssertionError("oversized metadata must not be streamed")

    monkeypatch.setattr(Path, "open", must_not_open)
    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._path_digest(metadata)

    assert exc_info.value.code == "git_metadata_too_large"


def test_large_untracked_path_is_kept_with_explicit_incomplete_content(repo):
    (repo / "large.txt").write_bytes(b"x" * 1_000_001)

    snapshot = collect_diff_snapshot(repo, None)
    diff = {item.path: item for item in snapshot.files}["large.txt"]

    assert diff.new_size == 1_000_001
    assert diff.content_scanned is False
    assert diff.scan_reason == "content exceeds 1000000 bytes"
    assert snapshot.paths_complete is True
    assert snapshot.content_complete is False


def test_symlink_content_is_not_followed_outside_repository(repo, tmp_path):
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be read\n")
    link = repo / "external-link.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")

    diff = {item.path: item for item in collect_diff(repo, None)}["external-link.txt"]

    assert diff.symlink is True
    assert "must not be read" not in diff.added_lines
    assert diff.added_lines == [str(outside)]


def test_shallow_clone_with_missing_base_has_actionable_error(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _run_git(source, "init", "-q")
    _run_git(source, "config", "user.email", "t@t.dev")
    _run_git(source, "config", "user.name", "t")
    (source / "history.txt").write_text("first\n")
    _run_git(source, "add", ".")
    _run_git(source, "commit", "-qm", "first")
    missing_sha = _git(source, "rev-parse", "HEAD")
    (source / "history.txt").write_text("second\n")
    _run_git(source, "commit", "-qam", "second")

    shallow = tmp_path / "shallow clone"
    _run_git(tmp_path, "clone", "-q", "--depth", "1", source.as_uri(), str(shallow))

    head_snapshot = collect_diff_snapshot(shallow, None)
    assert head_snapshot.state.shallow is True
    assert head_snapshot.state.head_sha == _git(shallow, "rev-parse", "HEAD")

    with pytest.raises(GitDiffError) as exc_info:
        resolve_git_state(shallow, missing_sha)
    assert exc_info.value.code == "shallow_missing_history"
    assert "shallow" in str(exc_info.value)


def test_corrupt_head_is_not_mistaken_for_an_unborn_repository(repo):
    head = repo / ".git" / "HEAD"
    original = head.read_bytes()
    try:
        head.write_text("this is not a valid HEAD\n")
        with pytest.raises(GitDiffError) as exc_info:
            resolve_git_state(repo, None)
        assert exc_info.value.code in {"invalid_repository", "not_worktree"}
    finally:
        head.write_bytes(original)


def test_snapshot_counts_unique_paths_and_layer_transitions(repo):
    state = resolve_git_state(repo, None)
    snapshot = gitdiff_module.DiffSnapshot(
        state=state,
        files=[
            gitdiff_module.FileDiff("new.py", "R", old_path="old.py", layer="staged"),
            gitdiff_module.FileDiff("new.py", "M", layer="worktree"),
        ],
        git_metadata=gitdiff_module._git_metadata(state),
    )

    assert snapshot.changed_paths == ["new.py", "old.py"]
    assert snapshot.path_count == 2
    assert snapshot.transition_count == 2


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [(FileNotFoundError("missing"), "git_not_found"), (OSError("denied"), "custom")],
)
def test_git_launch_errors_are_translated(tmp_path, monkeypatch, failure, expected_code):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(gitdiff_module.subprocess, "run", fail)

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._run_git(["status"], tmp_path, error_code="custom")

    assert exc_info.value.code == expected_code
    assert "git status" in str(exc_info.value) or expected_code == "git_not_found"


@pytest.mark.parametrize("stderr", [b"", b"fatal: malformed repository\n"])
def test_failed_git_command_includes_available_diagnostic(tmp_path, monkeypatch, stderr):
    monkeypatch.setattr(
        gitdiff_module.subprocess,
        "run",
        lambda *args, **kwargs: _completed(returncode=128, stderr=stderr),
    )

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._run_git(["rev-parse", "HEAD"], tmp_path)

    assert exc_info.value.code == "git_failed"
    assert "git rev-parse HEAD failed" in str(exc_info.value)
    assert ("malformed repository" in str(exc_info.value)) is bool(stderr)


def test_discovery_rejects_missing_and_non_directory_locations(tmp_path):
    missing = tmp_path / "missing"
    regular = tmp_path / "file"
    regular.write_text("not a directory\n")

    for location in (missing, regular):
        with pytest.raises(GitDiffError) as exc_info:
            discover_repository(location)
        assert exc_info.value.code == "not_worktree"


def test_git_path_and_shallow_metadata_must_be_well_formed(repo, monkeypatch):
    with pytest.raises(GitDiffError) as empty_path:
        gitdiff_module._single_path(b"\r\n", name="worktree root")
    assert empty_path.value.code == "invalid_repository"

    monkeypatch.setattr(gitdiff_module, "_git_bytes", lambda *args, **kwargs: b"unknown\n")
    with pytest.raises(GitDiffError) as shallow:
        gitdiff_module._is_shallow(repo)
    assert shallow.value.code == "invalid_repository"


def test_comparison_ref_must_be_a_string(repo):
    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._validate_ref(123)  # type: ignore[arg-type]
    assert exc_info.value.code == "invalid_ref"


@pytest.mark.parametrize(
    ("function_name", "stdout"),
    [
        ("_resolve_commit", b"not-an-object\n"),
        ("_resolve_commit", b"a" * 41 + b"\n"),
        ("_empty_tree_sha", b"bad-tree\n"),
        ("_empty_tree_sha", b"a" * 63 + b"\n"),
    ],
)
def test_resolved_git_object_ids_are_validated(repo, monkeypatch, function_name, stdout):
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(stdout=stdout),
    )

    with pytest.raises(GitDiffError) as exc_info:
        if function_name == "_resolve_commit":
            gitdiff_module._resolve_commit(repo, "HEAD", shallow=False)
        else:
            gitdiff_module._empty_tree_sha(repo)

    assert exc_info.value.code in {"invalid_ref", "invalid_repository"}


@pytest.mark.parametrize("status_stderr", [b"", b"fatal: corrupt HEAD\n"])
def test_unresolvable_head_requires_readable_repository_status(repo, monkeypatch, status_stderr):
    results = iter(
        [
            _completed(returncode=1),
            _completed(returncode=128, stderr=status_stderr),
        ]
    )
    monkeypatch.setattr(gitdiff_module, "_run_git", lambda *args, **kwargs: next(results))

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._head_sha(repo)

    assert exc_info.value.code == "invalid_repository"
    assert ("corrupt HEAD" in str(exc_info.value)) is bool(status_stderr)


def test_head_object_id_and_symbolic_ref_are_validated(repo, monkeypatch):
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(stdout=b"a" * 41 + b"\n"),
    )
    with pytest.raises(GitDiffError) as head:
        gitdiff_module._head_sha(repo)
    assert head.value.code == "invalid_repository"

    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(stdout=b"refs/heads/main\nembedded\n"),
    )
    with pytest.raises(GitDiffError) as symbolic:
        gitdiff_module._head_ref(repo)
    assert symbolic.value.code == "invalid_repository"


@pytest.mark.parametrize("stderr", [b"", b"fatal: bad symbolic ref\n"])
def test_symbolic_head_git_errors_are_actionable(repo, monkeypatch, stderr):
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(returncode=2, stderr=stderr),
    )

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._head_ref(repo)

    assert exc_info.value.code == "invalid_repository"
    assert ("bad symbolic ref" in str(exc_info.value)) is bool(stderr)


@pytest.mark.parametrize("shallow", [False, True])
def test_missing_merge_base_is_never_accepted(repo, monkeypatch, shallow):
    layout = discover_repository(repo)
    monkeypatch.setattr(gitdiff_module, "discover_repository", lambda cwd: layout)
    monkeypatch.setattr(gitdiff_module, "_ensure_no_object_overrides", lambda repository: None)
    monkeypatch.setattr(gitdiff_module, "_is_shallow", lambda root: shallow)
    monkeypatch.setattr(gitdiff_module, "_head_sha", lambda root: "a" * 40)
    monkeypatch.setattr(gitdiff_module, "_resolve_commit", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(returncode=1),
    )

    with pytest.raises(GitDiffError) as exc_info:
        resolve_git_state(repo, "target")

    expected = "shallow_missing_history" if shallow else "no_merge_base"
    assert exc_info.value.code == expected


def test_merge_base_object_id_is_validated(repo, monkeypatch):
    layout = discover_repository(repo)
    monkeypatch.setattr(gitdiff_module, "discover_repository", lambda cwd: layout)
    monkeypatch.setattr(gitdiff_module, "_ensure_no_object_overrides", lambda repository: None)
    monkeypatch.setattr(gitdiff_module, "_is_shallow", lambda root: False)
    monkeypatch.setattr(gitdiff_module, "_head_sha", lambda root: "a" * 40)
    monkeypatch.setattr(gitdiff_module, "_resolve_commit", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(stdout=b"a" * 63 + b"\n"),
    )

    with pytest.raises(GitDiffError) as exc_info:
        resolve_git_state(repo, "target")

    assert exc_info.value.code == "invalid_repository"


def test_unborn_repository_with_explicit_target_uses_empty_tree(repo, monkeypatch):
    layout = discover_repository(repo)
    monkeypatch.setattr(gitdiff_module, "discover_repository", lambda cwd: layout)
    monkeypatch.setattr(gitdiff_module, "_ensure_no_object_overrides", lambda repository: None)
    monkeypatch.setattr(gitdiff_module, "_is_shallow", lambda root: False)
    monkeypatch.setattr(gitdiff_module, "_head_sha", lambda root: None)
    monkeypatch.setattr(gitdiff_module, "_resolve_commit", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr(gitdiff_module, "_empty_tree_sha", lambda root: "b" * 40)

    state = resolve_git_state(repo, "target")

    assert state.unborn is True
    assert state.target_sha == "a" * 40
    assert state.baseline_sha == "b" * 40


@pytest.mark.parametrize("shallow", [False, True])
def test_pinned_baseline_must_remain_available(repo, monkeypatch, shallow):
    pinned = resolve_git_state(repo, None)
    monkeypatch.setattr(gitdiff_module, "discover_repository", lambda cwd: pinned.repository)
    monkeypatch.setattr(gitdiff_module, "_ensure_no_object_overrides", lambda repository: None)
    monkeypatch.setattr(gitdiff_module, "_is_shallow", lambda root: shallow)
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(returncode=1),
    )

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._resolve_pinned_state(repo, pinned)

    expected = "shallow_missing_history" if shallow else "invalid_repository"
    assert exc_info.value.code == expected


def test_pinned_state_rejects_repository_identity_change(repo, monkeypatch):
    pinned = resolve_git_state(repo, None)
    changed = replace(
        pinned.repository,
        git_dir=pinned.repository.git_dir.parent / "different-git-dir",
    )
    monkeypatch.setattr(gitdiff_module, "discover_repository", lambda cwd: changed)
    monkeypatch.setattr(gitdiff_module, "_ensure_no_object_overrides", lambda repository: None)

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._resolve_pinned_state(repo, pinned)

    assert exc_info.value.code == "git_metadata_changed"


def test_graft_inspection_errors_fail_closed(repo, monkeypatch):
    layout = discover_repository(repo)
    graft = layout.common_dir / "info" / "grafts"
    original_lstat = Path.lstat

    def fail_graft(path):
        if path == graft:
            raise PermissionError("graft metadata denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_graft)

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._ensure_no_grafts(layout)

    assert exc_info.value.code == "git_metadata_failed"
    assert "graft metadata denied" in str(exc_info.value)


def test_empty_graft_directory_is_rejected(repo):
    layout = discover_repository(repo)
    graft = layout.common_dir / "info" / "grafts"
    graft.mkdir(parents=True)

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._ensure_no_grafts(layout)

    assert exc_info.value.code == "grafts_present"


def test_path_digest_covers_missing_directory_and_symlink(tmp_path):
    missing = tmp_path / "missing"
    directory = tmp_path / "metadata-dir"
    directory.mkdir()

    assert gitdiff_module._path_digest(missing) == "missing"
    assert gitdiff_module._path_digest(directory).startswith("sha256:")

    link = tmp_path / "metadata-link"
    try:
        link.symlink_to("missing-target")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")
    assert gitdiff_module._path_digest(link).startswith("sha256:")


def test_path_digest_reports_inspection_readlink_and_stream_errors(tmp_path, monkeypatch):
    metadata = tmp_path / "metadata"
    metadata.write_text("value\n")
    original_lstat = Path.lstat
    original_open = Path.open

    def deny_lstat(path):
        if path == metadata:
            raise PermissionError("inspect denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_lstat)
    with pytest.raises(GitDiffError, match="inspect denied") as inspect:
        gitdiff_module._path_digest(metadata)
    assert inspect.value.code == "git_metadata_failed"

    monkeypatch.setattr(Path, "lstat", original_lstat)

    def deny_open(path, *args, **kwargs):
        if path == metadata:
            raise PermissionError("read denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_open)
    with pytest.raises(GitDiffError, match="read denied") as read:
        gitdiff_module._path_digest(metadata)
    assert read.value.code == "git_metadata_failed"

    monkeypatch.setattr(Path, "open", original_open)
    link = tmp_path / "metadata-link"
    try:
        link.symlink_to("target")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")
    original_readlink = os.readlink

    def deny_readlink(path, *args, **kwargs):
        if Path(path) == link:
            raise PermissionError("link denied")
        return original_readlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", deny_readlink)
    with pytest.raises(GitDiffError, match="link denied") as symlink:
        gitdiff_module._path_digest(link)
    assert symlink.value.code == "git_metadata_failed"


@pytest.mark.parametrize("function_name", ["_effective_config_digest", "_refs_digest"])
def test_bounded_git_metadata_rejects_oversized_plumbing_output(
    repo, monkeypatch, function_name
):
    monkeypatch.setattr(gitdiff_module, "MAX_GIT_METADATA_BYTES", 3)
    monkeypatch.setattr(gitdiff_module, "_git_bytes", lambda *args, **kwargs: b"four")

    with pytest.raises(GitDiffError) as exc_info:
        getattr(gitdiff_module, function_name)(repo)

    assert exc_info.value.code == "git_metadata_too_large"


@pytest.mark.parametrize("stderr", [b"", b"fatal: invalid configured path\n"])
def test_configured_git_file_lookup_errors_fail_closed(repo, monkeypatch, stderr):
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(returncode=2, stderr=stderr),
    )

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._configured_files_digest(repo)

    assert exc_info.value.code == "git_metadata_failed"
    assert ("invalid configured path" in str(exc_info.value)) is bool(stderr)


def test_configured_git_file_digest_resolves_relative_and_absolute_paths(
    repo, tmp_path, monkeypatch
):
    absolute = tmp_path / "global-ignore"
    absolute.write_text("*.secret\n")
    observed: list[Path] = []

    def config_result(args, *unused, **kwargs):
        key = args[-1]
        if key == "core.excludesFile":
            return _completed(stdout=b"relative-ignore\0" + os.fsencode(absolute) + b"\0")
        return _completed(returncode=1)

    def semantic_digest(path):
        observed.append(path)
        return "sha256:" + "0" * 64

    monkeypatch.setattr(gitdiff_module, "_run_git", config_result)
    monkeypatch.setattr(gitdiff_module, "_semantic_path_digest", semantic_digest)

    digest = gitdiff_module._configured_files_digest(repo)

    assert digest.startswith("sha256:")
    assert observed == [repo / "relative-ignore", absolute]


def test_semantic_digest_includes_symlink_target_and_tolerates_missing_target(
    tmp_path, monkeypatch
):
    target = tmp_path / "target"
    target.write_text("*.tmp\n")
    link = tmp_path / "link"
    broken = tmp_path / "broken"
    try:
        link.symlink_to(target)
        broken.symlink_to(tmp_path / "absent")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")

    observed: list[list[Path]] = []
    original_combined = gitdiff_module._combined_digest

    def capture(paths):
        observed.append(paths)
        return original_combined(paths)

    monkeypatch.setattr(gitdiff_module, "_combined_digest", capture)

    assert gitdiff_module._semantic_path_digest(link).startswith("sha256:")
    assert observed == [[link, target.resolve()]]
    observed.clear()
    assert gitdiff_module._semantic_path_digest(broken) == gitdiff_module._path_digest(broken)
    assert observed == []


def test_malformed_index_visibility_metadata_is_rejected(repo, monkeypatch):
    monkeypatch.setattr(gitdiff_module, "_git_bytes", lambda *args, **kwargs: b"X\0")

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._ensure_index_entries_are_visible(repo)

    assert exc_info.value.code == "invalid_git_output"


def test_hidden_index_entry_error_is_bounded(repo, monkeypatch):
    hidden = b"\0".join(f"h path-{index}".encode() for index in range(25)) + b"\0"

    def visibility(args, *unused, **kwargs):
        return hidden if "-v" in args else b""

    monkeypatch.setattr(gitdiff_module, "_git_bytes", visibility)

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._ensure_index_entries_are_visible(repo)

    assert exc_info.value.code == "hidden_index_entries"
    assert "+5 more" in str(exc_info.value)


@pytest.mark.parametrize(
    ("tree_output", "cat_returncode", "expected_code"),
    [
        (b"malformed-entry\0", 0, "invalid_git_output"),
        (b"100644 blob " + b"a" * 40 + b"\tother.yaml\0", 0, "invalid_git_output"),
        (b"100644 blob\tdod.yaml\0", 0, "invalid_git_output"),
        (b"100644 blob invalid\tdod.yaml\0", 0, "invalid_git_output"),
        (b"100644 blob " + b"a" * 41 + b"\tdod.yaml\0", 0, "invalid_git_output"),
        (b"100644 blob " + b"a" * 40 + b"\tdod.yaml\0", 1, "invalid_policy_object"),
    ],
)
def test_blob_reader_rejects_malformed_tree_and_blob_metadata(
    repo, monkeypatch, tree_output, cat_returncode, expected_code
):
    layout = discover_repository(repo)
    monkeypatch.setattr(gitdiff_module, "discover_repository", lambda cwd: layout)
    monkeypatch.setattr(gitdiff_module, "_ensure_no_object_overrides", lambda repository: None)
    monkeypatch.setattr(gitdiff_module, "_is_shallow", lambda root: False)
    monkeypatch.setattr(gitdiff_module, "_resolve_commit", lambda *args, **kwargs: "b" * 40)

    def plumbing(args, *unused, **kwargs):
        if args[0] == "ls-tree":
            return _completed(stdout=tree_output)
        assert args[0] == "cat-file"
        return _completed(returncode=cat_returncode, stdout=b"version: 2\n")

    monkeypatch.setattr(gitdiff_module, "_run_git", plumbing)

    with pytest.raises(GitDiffError) as exc_info:
        read_blob_at_revision(repo, "HEAD", "dod.yaml")

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    "output",
    [
        b"\xff\0path\0",
        b"lowercase\0path\0",
        b"R100\0only-old-path\0",
        b"M\0../outside\0",
    ],
)
def test_name_status_parser_rejects_malformed_git_metadata(repo, monkeypatch, output):
    state = resolve_git_state(repo, None)
    monkeypatch.setattr(gitdiff_module, "_git_bytes", lambda *args, **kwargs: output)

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._name_status_diffs(state, [], layer="worktree")

    assert exc_info.value.code == "invalid_git_output"


def test_nul_parser_rejects_empty_interior_fields():
    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._split_z(b"first\0\0second\0")
    assert exc_info.value.code == "invalid_git_output"


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("generated/", ":(top,glob)generated/**"),
        ("/literal/path", ":(top,literal)literal/path"),
        ("name[1].cfg", ":(top,glob)**/name\\[1\\].cfg"),
    ],
)
def test_ignored_pathspec_translation_is_explicit(pattern, expected):
    assert gitdiff_module._ignored_pathspec(pattern) == expected


def test_ignored_ancestor_classifier_handles_empty_and_git_errors(repo, monkeypatch):
    state = resolve_git_state(repo, None)
    assert gitdiff_module._ignored_ancestor_paths(state, ["root.txt"]) == set()

    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(
            returncode=2, stderr=b"fatal: check-ignore failed\n"
        ),
    )
    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._ignored_ancestor_paths(state, ["ignored/child.txt"])
    assert exc_info.value.code == "untracked_scan_failed"
    assert "check-ignore failed" in str(exc_info.value)


def test_ignored_ancestor_classifier_rejects_unexpected_git_path(repo, monkeypatch):
    state = resolve_git_state(repo, None)
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(stdout=b"not-an-ancestor\0"),
    )

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._ignored_ancestor_paths(state, ["ignored/child.txt"])

    assert exc_info.value.code == "invalid_git_output"


def test_open_regular_closes_descriptor_when_stream_creation_fails(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.write_text("content\n")
    closed: list[int] = []

    def fail_fdopen(descriptor, mode):
        raise RuntimeError("fdopen failed")

    monkeypatch.setattr(gitdiff_module.os, "fdopen", fail_fdopen)
    original_close = os.close

    def capture_close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(gitdiff_module.os, "close", capture_close)

    with pytest.raises(RuntimeError, match="fdopen failed"):
        gitdiff_module._open_regular(candidate)

    assert len(closed) == 1


def test_hash_regular_file_reports_open_error(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.write_text("content\n")
    expected = candidate.lstat()

    def deny_open(path):
        raise PermissionError("hash denied")

    monkeypatch.setattr(gitdiff_module, "_open_regular", deny_open)

    raw, normalized, reason = gitdiff_module._hash_regular_file(candidate, expected, 40)

    assert raw is normalized is None
    assert reason == "cannot hash file: hash denied"


@pytest.mark.parametrize(
    ("changed_call", "expected_reason"),
    [
        (2, "file changed before content hashing"),
        (3, "file changed while content was hashed"),
    ],
)
def test_hash_regular_file_detects_fstat_races(
    tmp_path, monkeypatch, changed_call, expected_reason
):
    candidate = tmp_path / "candidate"
    candidate.write_text("content\n")
    expected = candidate.lstat()
    calls = 0
    original_fstat = os.fstat

    def raced_fstat(descriptor):
        nonlocal calls
        calls += 1
        current = original_fstat(descriptor)
        if calls == changed_call:
            return _changed_stat(current)
        return current

    monkeypatch.setattr(gitdiff_module.os, "fstat", raced_fstat)

    raw, normalized, reason = gitdiff_module._hash_regular_file(candidate, expected, 40)

    assert raw is normalized is None
    assert reason == expected_reason


@pytest.mark.parametrize(
    ("changed_call", "expected_reason"),
    [
        (5, "file changed before normalized hashing"),
        (6, "file changed while normalized content was hashed"),
    ],
)
def test_normalized_hashing_detects_fstat_races(
    tmp_path, monkeypatch, changed_call, expected_reason
):
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"first\r\nsecond\r\n")
    expected = candidate.lstat()
    calls = 0
    original_fstat = os.fstat

    def raced_fstat(descriptor):
        nonlocal calls
        calls += 1
        current = original_fstat(descriptor)
        if calls == changed_call:
            return _changed_stat(current)
        return current

    monkeypatch.setattr(gitdiff_module.os, "fstat", raced_fstat)

    raw, normalized, reason = gitdiff_module._hash_regular_file(candidate, expected, 40)

    assert raw is normalized is None
    assert reason == expected_reason


def test_normalized_hashing_reports_second_open_error(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"first\r\nsecond\r\n")
    expected = candidate.lstat()
    original_open = gitdiff_module._open_regular
    calls = 0

    def fail_second(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("normalized denied")
        return original_open(path)

    monkeypatch.setattr(gitdiff_module, "_open_regular", fail_second)

    raw, normalized, reason = gitdiff_module._hash_regular_file(candidate, expected, 40)

    assert raw is normalized is None
    assert reason == "cannot hash normalized file: normalized denied"


def test_worktree_identity_covers_absent_directory_and_unsafe_parent(repo):
    missing = gitdiff_module._IndexEntry("missing.txt", "100644", "a" * 40)
    directory_path = repo / "directory-entry"
    directory_path.mkdir()
    directory = gitdiff_module._IndexEntry("directory-entry", "100644", "a" * 40)
    parent_link = repo / "linked-parent"
    try:
        parent_link.symlink_to(repo / "tests", target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are not available in this test environment")
    escaped = gitdiff_module._IndexEntry("linked-parent/test_app.py", "100644", "a" * 40)

    assert gitdiff_module._worktree_identity(repo, missing).kind == "absent"
    assert gitdiff_module._worktree_identity(repo, directory).kind == "directory"
    unsafe = gitdiff_module._worktree_identity(repo, escaped)
    assert unsafe.kind == "unreadable"
    assert unsafe.reason == "symlinked parent"


def test_worktree_identity_reports_inspection_and_symlink_read_errors(repo, monkeypatch):
    candidate = repo / "identity-link"
    try:
        candidate.symlink_to("app.py")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")
    entry = gitdiff_module._IndexEntry("identity-link", "120000", "a" * 40)
    original_readlink = os.readlink

    def deny_readlink(path, *args, **kwargs):
        if Path(path) == candidate:
            raise PermissionError("identity link denied")
        return original_readlink(path, *args, **kwargs)

    monkeypatch.setattr(gitdiff_module.os, "readlink", deny_readlink)
    identity = gitdiff_module._worktree_identity(repo, entry)
    assert identity.kind == "symlink"
    assert identity.reason == "identity link denied"

    monkeypatch.setattr(gitdiff_module.os, "readlink", original_readlink)
    original_lstat = Path.lstat

    def deny_lstat(path):
        if path == candidate:
            raise PermissionError("identity inspect denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_lstat)
    identity = gitdiff_module._worktree_identity(repo, entry)
    assert identity.kind == "unreadable"
    assert identity.reason == "cannot inspect file: identity inspect denied"


def test_unsafe_parent_reason_covers_missing_and_non_directory_parent(repo):
    missing = gitdiff_module._unsafe_parent_reason(repo, repo / "missing" / "child.txt")
    assert missing is not None and missing.startswith("unreadable parent:")

    parent_file = repo / "parent-file"
    parent_file.write_text("not a directory\n")
    assert (
        gitdiff_module._unsafe_parent_reason(repo, parent_file / "child.txt")
        == "non-directory parent"
    )


def test_worktree_content_covers_missing_directory_and_inspection_error(repo, monkeypatch):
    missing = gitdiff_module._worktree_content(repo, "missing.txt")
    assert missing.reason == "file disappeared during scan"

    directory = repo / "content-directory"
    directory.mkdir()
    non_file = gitdiff_module._worktree_content(repo, "content-directory")
    assert non_file.reason == "not a regular file"

    candidate = repo / "app.py"
    original_lstat = Path.lstat

    def deny_lstat(path):
        if path == candidate:
            raise PermissionError("content inspect denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_lstat)
    unreadable = gitdiff_module._worktree_content(repo, "app.py")
    assert unreadable.reason == "cannot inspect file: content inspect denied"


def test_worktree_content_reports_symlink_and_regular_file_read_errors(repo, monkeypatch):
    link = repo / "content-link"
    try:
        link.symlink_to("app.py")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")
    original_readlink = os.readlink

    def deny_readlink(path, *args, **kwargs):
        if Path(path) == link:
            raise PermissionError("content link denied")
        return original_readlink(path, *args, **kwargs)

    monkeypatch.setattr(gitdiff_module.os, "readlink", deny_readlink)
    unreadable_link = gitdiff_module._worktree_content(repo, "content-link")
    assert unreadable_link.symlink is True
    assert unreadable_link.reason == "cannot read symlink: content link denied"

    monkeypatch.setattr(gitdiff_module.os, "readlink", original_readlink)
    candidate = repo / "read-error.txt"
    candidate.write_text("content\n")
    original_read_bytes = Path.read_bytes

    def deny_read(path):
        if path == candidate:
            raise PermissionError("content read denied")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_read)
    unreadable_file = gitdiff_module._worktree_content(repo, "read-error.txt")
    assert unreadable_file.reason == "cannot read file: content read denied"


@pytest.mark.parametrize(
    ("size_result", "blob_result", "expected_reason"),
    [
        (_completed(returncode=1), None, "missing blob"),
        (_completed(stdout=b"1000001\n"), None, "content exceeds 1000000 bytes"),
        (_completed(stdout=b"4\n"), _completed(returncode=1), "missing blob"),
    ],
)
def test_git_content_reports_missing_and_oversized_blobs(
    repo, monkeypatch, size_result, blob_result, expected_reason
):
    results = iter([result for result in (size_result, blob_result) if result is not None])
    monkeypatch.setattr(gitdiff_module, "_run_git", lambda *args, **kwargs: next(results))

    content = gitdiff_module._git_content(repo, "HEAD:file", missing="missing blob")

    assert content.data is None
    assert content.reason == expected_reason


def test_invalid_blob_size_is_rejected(repo, monkeypatch):
    monkeypatch.setattr(
        gitdiff_module,
        "_run_git",
        lambda *args, **kwargs: _completed(stdout=b"not-a-size\n"),
    )

    with pytest.raises(GitDiffError) as exc_info:
        gitdiff_module._blob_size(repo, "HEAD:file")

    assert exc_info.value.code == "invalid_git_output"


def test_non_utf8_content_and_excessive_lines_are_incomplete(monkeypatch):
    lines, reason, binary = gitdiff_module._decode_text(
        gitdiff_module._Content(b"\xff\xfe", 2)
    )
    assert lines is None
    assert reason == "content is not UTF-8 text"
    assert binary is True

    monkeypatch.setattr(gitdiff_module, "MAX_SCANNED_LINES", 1)
    item = gitdiff_module.FileDiff("many-lines.txt", "M")
    gitdiff_module._populate_transition(
        item,
        gitdiff_module._Content(b"one\ntwo\n", 8),
        gitdiff_module._Content(b"three\nfour\n", 11),
    )
    assert item.content_scanned is False
    assert item.scan_reason == "content exceeds 1 lines"


@pytest.mark.parametrize(
    ("identity", "expected_status"),
    [
        (gitdiff_module._WorktreeIdentity("absent"), "D"),
        (gitdiff_module._WorktreeIdentity("special", reason="special type"), "T"),
        (
            gitdiff_module._WorktreeIdentity(
                "regular", mode="100644", object_id="b" * 40
            ),
            "A",
        ),
    ],
)
def test_direct_worktree_comparison_classifies_hidden_transitions(
    repo, monkeypatch, identity, expected_status
):
    state = resolve_git_state(repo, None)
    object_id = "0" * 40 if expected_status == "A" else "a" * 40
    entry = gitdiff_module._IndexEntry("app.py", "100644", object_id)
    monkeypatch.setattr(gitdiff_module, "_index_entries", lambda root: [entry])
    monkeypatch.setattr(gitdiff_module, "_worktree_identity", lambda root, value: identity)
    monkeypatch.setattr(
        gitdiff_module,
        "_index_content",
        lambda current, path: gitdiff_module._Content(b"old\n", 4),
    )
    monkeypatch.setattr(
        gitdiff_module,
        "_worktree_content",
        lambda root, path: gitdiff_module._Content(b"new\n", 4),
    )

    files = gitdiff_module._direct_worktree_diffs(state, set())

    assert len(files) == 1
    assert files[0].status == expected_status
    if identity.reason:
        assert files[0].content_scanned is False
        assert files[0].scan_reason == identity.reason


def test_direct_worktree_comparison_skips_gitlink_directory(repo, monkeypatch):
    state = resolve_git_state(repo, None)
    entry = gitdiff_module._IndexEntry("submodule", "160000", "a" * 40)
    monkeypatch.setattr(gitdiff_module, "_index_entries", lambda root: [entry])
    monkeypatch.setattr(
        gitdiff_module,
        "_worktree_identity",
        lambda root, value: gitdiff_module._WorktreeIdentity("directory"),
    )

    assert gitdiff_module._direct_worktree_diffs(state, set()) == []


def test_scan_rejects_git_metadata_changes_during_collection(repo, monkeypatch):
    original_metadata = gitdiff_module._git_metadata(resolve_git_state(repo, None))
    changed_metadata = replace(original_metadata, refs_digest="sha256:" + "f" * 64)
    metadata = iter([original_metadata, changed_metadata])
    monkeypatch.setattr(gitdiff_module, "_git_metadata", lambda state: next(metadata))
    monkeypatch.setattr(gitdiff_module, "_tracked_layers", lambda state: [])
    monkeypatch.setattr(gitdiff_module, "_untracked_paths", lambda *args, **kwargs: [])

    with pytest.raises(GitDiffError) as exc_info:
        collect_diff_snapshot(repo, None)

    assert exc_info.value.code == "git_changed_during_scan"


def test_untracked_rename_target_is_not_double_counted(repo, monkeypatch):
    metadata = gitdiff_module._git_metadata(resolve_git_state(repo, None))
    monkeypatch.setattr(gitdiff_module, "_git_metadata", lambda state: metadata)
    monkeypatch.setattr(
        gitdiff_module,
        "_tracked_layers",
        lambda state: [
            gitdiff_module.FileDiff("new.py", "R", old_path="old.py", layer="worktree")
        ],
    )
    monkeypatch.setattr(
        gitdiff_module, "_untracked_paths", lambda *args, **kwargs: ["new.py"]
    )

    snapshot = collect_diff_snapshot(repo, None)

    assert [(item.path, item.status) for item in snapshot.files] == [("new.py", "R")]


def test_question_mark_glob_matches_one_non_separator_character():
    assert matches_any("tests/test_a.py", ["tests/test_?.py"])
    assert not matches_any("tests/test_ab.py", ["tests/test_?.py"])


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()
