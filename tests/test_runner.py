import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import dunnit.runner as runner_module
from dunnit.contract import CommandCheck, Contract
from dunnit.gitdiff import GitDiffError, collect_diff_snapshot
from dunnit.runner import verify
from dunnit.verdict import Outcome, Status, Verdict


def _body(*, checks=None, protected=None, tamper=True, stubs=False, base=None):
    data = {
        "version": 2,
        "checks": checks or [],
        "protected": ["dod.yaml", "app.py"] if protected is None else protected,
        "tamper": tamper,
        "stubs": stubs,
        "strict": False,
    }
    if base is not None:
        data["base"] = base
    return yaml.safe_dump(data, sort_keys=False)


def _commit(repo: Path, body: str, message: str = "policy") -> str:
    (repo / "dod.yaml").write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_replaced_policy_cannot_execute_proposed_command(repo):
    _commit(repo, _body(tamper=False))
    sentinel = repo / "proposed-command-ran"
    proposed = _body(
        checks=[
            {
                "name": "malicious",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('proposed-command-ran').write_text('yes')",
                ],
            }
        ],
        protected=[],
        tamper=False,
    )
    (repo / "dod.yaml").write_text(proposed)

    verdict = verify(repo / "dod.yaml", cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    assert not sentinel.exists()
    assert any(item.check == "tamper:protected-path" for item in verdict.evidence)
    assert verdict.meta["policy"]["origin"].startswith("git:")


def test_candidate_symlink_cannot_redirect_trusted_policy_path(repo):
    sentinel = repo / "weakened-policy-ran"
    weak = _body(
        checks=[
            {
                "name": "weak-command",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('weakened-policy-ran').touch()",
                ],
            }
        ],
        protected=[],
        tamper=False,
    )
    (repo / "weak.yaml").write_text(weak)
    target_sha = _commit(repo, _body(tamper=False), "trusted policies")
    (repo / "dod.yaml").unlink()
    try:
        (repo / "dod.yaml").symlink_to("weak.yaml")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this test environment")

    verdict = verify(cwd=repo, mode="ci", policy_ref=target_sha, base=target_sha)

    assert verdict.outcome is Outcome.FAIL
    assert verdict.meta["policy"]["origin"] == f"git:{target_sha}:dod.yaml"
    assert any(item.check == "tamper:protected-path" for item in verdict.evidence)
    assert not sentinel.exists()


def test_pre_command_findings_survive_command_restoring_test(repo):
    test_file = repo / "tests" / "test_app.py"
    original = test_file.read_text()
    restore = (
        "from pathlib import Path; "
        f"Path('tests/test_app.py').write_text({original!r}, encoding='utf-8')"
    )
    _commit(
        repo,
        _body(
            checks=[{"name": "restore", "argv": [sys.executable, "-c", restore]}],
            tamper=True,
        ),
    )
    test_file.write_text("import pytest\n\n@pytest.mark.skip\ndef test_hidden():\n    assert True\n")

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    assert test_file.read_text() == original
    checks = {item.check for item in verdict.evidence}
    assert "tamper:added-skips" in checks
    assert "execution:workspace-mutated" in checks


def test_staged_test_weakening_cannot_hide_behind_restored_worktree(repo):
    _commit(repo, _body(tamper=True))
    test_file = repo / "tests" / "test_app.py"
    original = test_file.read_text()
    test_file.write_text(
        "import pytest\n\n@pytest.mark.skip\ndef test_hidden():\n    assert True\n"
    )
    subprocess.run(
        ["git", "add", "tests/test_app.py"], cwd=repo, check=True, capture_output=True
    )
    test_file.write_text(original)

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    assert any(item.check == "tamper:added-skips" for item in verdict.evidence)


def test_layered_report_counts_unique_paths_and_explicit_transitions(repo):
    base_sha = _commit(repo, _body(tamper=False))
    (repo / "app.py").write_text("value = 'committed'\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "candidate commit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "app.py").write_text("value = 'staged'\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("value = 'worktree'\n")

    verdict = verify(cwd=repo, base=base_sha)

    assert verdict.meta["files_changed"] == 1
    assert verdict.meta["diff_transitions"] == 3
    stage = verdict.meta["scan"]["stages"][0]
    assert stage["files"] == 1
    assert stage["paths"] == 1
    assert stage["transitions"] == 3
    scan = next(item for item in verdict.evidence if item.check == "scan:completeness")
    assert "1 unique changed paths across 3 diff transitions" in scan.detail


def test_command_writes_are_scoped_per_check(repo):
    command = "from pathlib import Path; Path('generated.txt').write_text('proof')"
    _commit(
        repo,
        _body(
            checks=[
                {
                    "name": "generate",
                    "argv": [sys.executable, "-c", command],
                    "writes": ["generated.txt"],
                }
            ],
            tamper=False,
        ),
    )

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.PASS
    assert any(item.check == "execution:writes" for item in verdict.evidence)


def test_undeclared_command_write_fails(repo):
    command = "from pathlib import Path; Path('generated.txt').write_text('proof')"
    _commit(
        repo,
        _body(
            checks=[{"name": "generate", "argv": [sys.executable, "-c", command]}],
            tamper=False,
        ),
    )

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    finding = next(item for item in verdict.evidence if item.check == "execution:workspace-mutated")
    assert "generated.txt" in finding.detail


def test_command_cannot_hide_tracked_write_inside_a_new_commit(repo):
    script = (
        "from pathlib import Path; import subprocess; "
        "Path('app.py').write_text('committed_by_proof = True\\n'); "
        "subprocess.run(['git', 'add', 'app.py'], check=True); "
        "subprocess.run(['git', 'commit', '-qm', 'proof mutation'], check=True)"
    )
    initial_head = _commit(
        repo,
        _body(checks=[{"name": "commit", "argv": [sys.executable, "-c", script]}], tamper=False),
    )

    verdict = verify(cwd=repo)

    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current_head != initial_head
    assert verdict.outcome is Outcome.FAIL
    assert any(item.check == "execution:git-state-mutated" for item in verdict.evidence)
    mutation = next(
        item for item in verdict.evidence if item.check == "execution:workspace-mutated"
    )
    assert "app.py" in mutation.detail


@pytest.mark.parametrize(
    "script, phrase",
    [
        (
            (
                "import subprocess; subprocess.run(["
                "'git', 'update-index', '--assume-unchanged', 'app.py'], check=True)"
            ),
            "index",
        ),
        (
            (
                "import subprocess; subprocess.run(["
                "'git', 'config', 'audit.command-mutated', 'true'], check=True)"
            ),
            "Git config",
        ),
        (
            (
                "import subprocess; subprocess.run(["
                "'git', 'update-ref', 'refs/heads/injected', 'HEAD'], check=True)"
            ),
            "Git refs",
        ),
    ],
)
def test_command_git_metadata_mutation_is_detected(repo, script, phrase):
    _commit(
        repo,
        _body(checks=[{"name": "metadata", "argv": [sys.executable, "-c", script]}], tamper=False),
    )

    verdict = verify(cwd=repo)

    finding = next(
        item for item in verdict.evidence if item.check == "execution:git-state-mutated"
    )
    assert verdict.outcome is Outcome.FAIL
    assert phrase in finding.detail


def test_command_global_git_config_mutation_is_detected(repo, tmp_path, monkeypatch):
    global_config = tmp_path / "isolated-global.gitconfig"
    excludes = tmp_path / "new-global-excludes"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    script = (
        "import subprocess; subprocess.run(["
        f"'git', 'config', '--global', 'core.excludesFile', {str(excludes)!r}], check=True)"
    )
    _commit(
        repo,
        _body(
            checks=[{"name": "global-config", "argv": [sys.executable, "-c", script]}],
            tamper=False,
        ),
    )

    verdict = verify(cwd=repo)

    finding = next(
        item for item in verdict.evidence if item.check == "execution:git-state-mutated"
    )
    assert verdict.outcome is Outcome.FAIL
    assert "effective Git config" in finding.detail


def test_command_cannot_hide_write_by_mutating_global_excludes(repo, tmp_path, monkeypatch):
    global_config = tmp_path / "isolated-global.gitconfig"
    excludes = tmp_path / "global-excludes"
    excludes.write_text("")
    subprocess.run(
        ["git", "config", "--file", str(global_config), "core.excludesFile", str(excludes)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    script = (
        "from pathlib import Path; "
        f"Path({str(excludes)!r}).write_text('hidden-by-command.txt\\n'); "
        "Path('hidden-by-command.txt').write_text('candidate')"
    )
    _commit(
        repo,
        _body(
            checks=[{"name": "mutate-excludes", "argv": [sys.executable, "-c", script]}],
            tamper=False,
        ),
    )

    verdict = verify(cwd=repo)

    finding = next(
        item for item in verdict.evidence if item.check == "execution:git-state-mutated"
    )
    assert verdict.outcome is Outcome.FAIL
    assert (repo / "hidden-by-command.txt").exists()
    assert "configured Git excludes/attributes" in finding.detail


def test_ignored_protected_untracked_path_is_enforced(repo):
    (repo / ".gitignore").write_text("protected.cfg\nignored-cache/**\n")
    _commit(repo, _body(protected=["dod.yaml", "protected.cfg"], tamper=False))
    (repo / "protected.cfg").write_text("candidate\n")
    cache = repo / "ignored-cache"
    cache.mkdir()
    (cache / "environment.bin").write_bytes(b"\0" * 1_000_001)

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    finding = next(item for item in verdict.evidence if item.check == "tamper:protected-path")
    assert "protected.cfg" in finding.detail
    assert not any(item.check == "scan:incomplete" for item in verdict.evidence)


def test_policy_path_is_never_a_declared_write(repo):
    command = "from pathlib import Path; Path('dod.yaml').write_text('broken')"
    _commit(
        repo,
        _body(
            checks=[
                {
                    "name": "rewrite-policy",
                    "argv": [sys.executable, "-c", command],
                    "writes": ["dod.yaml"],
                }
            ],
            tamper=False,
        ),
    )

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    finding = next(item for item in verdict.evidence if item.check == "execution:workspace-mutated")
    assert "dod.yaml" in finding.detail


def test_invalid_and_option_like_refs_are_errors(repo):
    _commit(repo, _body(tamper=False))

    missing = verify(cwd=repo, base="does-not-exist")
    option = verify(cwd=repo, base="--no-index")

    assert missing.outcome is Outcome.ERROR
    assert option.outcome is Outcome.ERROR
    assert any(item.status is Status.ERROR for item in missing.evidence)
    assert any(item.check == "git:invalid_ref" for item in option.evidence)


def test_non_git_directory_is_error(tmp_path):
    verdict = verify(cwd=tmp_path)
    assert verdict.outcome is Outcome.ERROR
    assert verdict.evidence[0].check == "git:not_worktree"


def test_ci_loads_policy_from_target_sha_not_candidate(repo):
    target_sha = _commit(repo, _body(tamper=False), "trusted policy")
    sentinel = repo / "candidate-policy-ran"
    (repo / "dod.yaml").write_text(
        _body(
            checks=[
                {
                    "name": "candidate-command",
                    "argv": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('candidate-policy-ran').write_text('yes')",
                    ],
                }
            ],
            protected=[],
            tamper=False,
        )
    )
    subprocess.run(["git", "add", "dod.yaml"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True, capture_output=True)

    verdict = verify(cwd=repo, mode="ci", policy_ref=target_sha, base=target_sha)

    assert verdict.outcome is Outcome.FAIL
    assert not sentinel.exists()
    assert verdict.meta["policy"]["origin"] == f"git:{target_sha}:dod.yaml"
    assert any(item.check == "tamper:protected-path" for item in verdict.evidence)


def test_ci_pins_mutable_policy_and_base_ref_to_resolved_sha(repo, monkeypatch):
    target_sha = _commit(repo, _body(tamper=False))
    branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    observed: list[str | None] = []
    original = runner_module.collect_diff_snapshot

    def capture_base(cwd, base, **kwargs):
        observed.append(base)
        return original(cwd, base, **kwargs)

    monkeypatch.setattr(runner_module, "collect_diff_snapshot", capture_base)

    verdict = verify(cwd=repo, mode="ci", policy_ref=branch, base=branch)

    assert verdict.outcome is Outcome.PASS
    assert observed == [target_sha]
    assert verdict.meta["git"]["target_sha"] == target_sha


def test_ci_rejects_target_without_policy(repo):
    target_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(repo, _body(tamper=False))

    verdict = verify(cwd=repo, mode="ci", policy_ref=target_sha, base=target_sha)

    assert verdict.outcome is Outcome.ERROR
    assert verdict.evidence[0].check == "git:policy_not_found"


def test_unborn_repository_uses_empty_tree_in_bootstrap(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "dod.yaml").write_text(_body(tamper=False))

    verdict = verify(cwd=tmp_path, bootstrap=True)

    assert verdict.outcome is Outcome.PASS
    assert verdict.meta["git"]["unborn"] is True
    assert verdict.meta["git"]["baseline_sha"]
    assert verdict.meta["policy"]["bootstrap"] is True


def test_incomplete_binary_scan_is_error_and_skips_commands(repo):
    sentinel = repo / "command-ran"
    _commit(
        repo,
        _body(
            checks=[
                {
                    "name": "must-not-run",
                    "argv": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('command-ran').write_text('yes')",
                    ],
                }
            ],
            tamper=False,
        ),
    )
    (repo / "asset.bin").write_bytes(b"binary\x00payload")

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.ERROR
    assert verdict.scan_complete is False
    assert not sentinel.exists()
    assert any(item.check == "scan:incomplete" for item in verdict.evidence)


def test_invalid_modes_and_programmatic_ci_policy_fail_closed(repo):
    assert verify(cwd=repo, mode="remote").outcome is Outcome.ERROR
    assert verify(cwd=repo, mode="ci", bootstrap=True).outcome is Outcome.ERROR
    assert verify(
        Contract(protected=["dod.yaml"], tamper=False, stubs=False),
        cwd=repo,
        mode="ci",
        policy_ref="HEAD",
    ).outcome is Outcome.ERROR


@pytest.mark.parametrize(
    "contract",
    [
        Contract(version=999, protected=["dod.yaml"], tamper=False, stubs=False),
        Contract(version=2, checks=[], protected=[], tamper=False, stubs=False),
        Contract(
            version=2,
            checks=[],
            protected=["dod.yaml"],
            test_globs=[],
            tamper=False,
            stubs=False,
        ),
    ],
)
def test_invalid_programmatic_contracts_cannot_pass(repo, contract):
    verdict = verify(contract, cwd=repo)
    assert verdict.outcome is Outcome.ERROR
    assert verdict.evidence[0].rule_id == "contract.invalid"


def test_programmatic_local_contract_remains_supported(repo):
    contract = Contract(
        version=2,
        protected=["app.py"],
        tamper=False,
        stubs=False,
        source=str(repo / "dod.yaml"),
    )
    verdict = verify(contract, cwd=repo)
    assert verdict.outcome is Outcome.PASS
    assert verdict.meta["policy"]["origin"] == "python-api"


def test_programmatic_v1_command_omits_v2_only_default_writes(repo):
    contract = Contract(
        version=1,
        checks=[CommandCheck(name="proof", run="git --version")],
        protected=["dod.yaml"],
        tamper=False,
        stubs=False,
    )

    verdict = verify(contract, cwd=repo)

    assert verdict.outcome is Outcome.PASS_WITH_WARNINGS
    assert any(item.check == "proof" and item.status is Status.PASS for item in verdict.evidence)
    assert any(item.check == "contract:v1-deprecated" for item in verdict.evidence)


def test_contract_path_must_be_a_file_inside_repository(repo):
    outside = verify(repo / ".." / "outside.yaml", cwd=repo)
    root_path = verify(repo, cwd=repo)
    assert outside.outcome is Outcome.ERROR
    assert root_path.outcome is Outcome.ERROR
    assert "within the repository" in outside.evidence[0].detail
    assert "must name a file" in root_path.evidence[0].detail


def test_bootstrap_missing_and_non_regular_policies_are_errors(repo):
    missing = verify(cwd=repo, bootstrap=True)
    assert missing.outcome is Outcome.ERROR
    assert "contract not found" in missing.evidence[0].detail

    (repo / "dod.yaml").mkdir()
    directory = verify(cwd=repo, bootstrap=True)
    assert directory.outcome is Outcome.ERROR
    assert "regular file" in directory.evidence[0].detail


def test_trusted_policy_must_be_utf8(repo):
    (repo / "dod.yaml").write_bytes(b"version: 2\n# \xff\n")
    subprocess.run(["git", "add", "dod.yaml"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "invalid policy"], cwd=repo, check=True)

    verdict = verify(cwd=repo)
    assert verdict.outcome is Outcome.ERROR
    assert "not valid UTF-8" in verdict.evidence[0].detail


def test_v1_policy_is_executed_with_explicit_deprecation_evidence(repo):
    _commit(
        repo,
        "version: 1\nchecks: []\nprotected: [dod.yaml, app.py]\ntamper: false\nstubs: false\n",
    )
    verdict = verify(cwd=repo)
    assert verdict.outcome is Outcome.PASS_WITH_WARNINGS
    warning = next(item for item in verdict.evidence if item.check == "contract:v1-deprecated")
    assert warning.fingerprint is not None


def test_command_fingerprint_ignores_runtime_detail():
    first = runner_module.Evidence(
        "tests",
        Status.PASS,
        "tests exited 0 in 0.1s",
        duration=0.1,
        exit_code=0,
    )
    second = runner_module.Evidence(
        "tests",
        Status.PASS,
        "tests exited 0 in 9.9s",
        duration=9.9,
        exit_code=0,
    )

    runner_module._finish_evidence(first)
    runner_module._finish_evidence(second)

    assert first.fingerprint == second.fingerprint


def test_many_undeclared_writes_are_bounded_in_evidence(repo):
    script = (
        "from pathlib import Path; "
        "[Path(f'generated-{i}.txt').write_text(str(i)) for i in range(23)]"
    )
    _commit(
        repo,
        _body(
            checks=[{"name": "many-writes", "argv": [sys.executable, "-c", script]}],
            tamper=False,
        ),
    )
    verdict = verify(cwd=repo)
    finding = next(item for item in verdict.evidence if item.check == "execution:workspace-mutated")
    assert verdict.outcome is Outcome.FAIL
    assert "(+3 more)" in finding.detail


def test_integrity_violation_stops_later_proof_commands(repo):
    first = "from pathlib import Path; Path('undeclared.txt').write_text('changed')"
    second = "from pathlib import Path; Path('later-command-ran').write_text('yes')"
    _commit(
        repo,
        _body(
            checks=[
                {"name": "mutator", "argv": [sys.executable, "-c", first]},
                {"name": "must-not-run", "argv": [sys.executable, "-c", second]},
            ],
            tamper=False,
        ),
    )

    verdict = verify(cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    assert (repo / "undeclared.txt").exists()
    assert not (repo / "later-command-ran").exists()
    assert any(item.check == "execution:workspace-mutated" for item in verdict.evidence)
    assert not any(item.check == "must-not-run" for item in verdict.evidence)


def test_declared_binary_output_still_makes_post_scan_incomplete(repo):
    script = "from pathlib import Path; Path('proof.bin').write_bytes(b'\\0proof')"
    _commit(
        repo,
        _body(
            checks=[
                {
                    "name": "binary-proof",
                    "argv": [sys.executable, "-c", script],
                    "writes": ["proof.bin"],
                }
            ],
            tamper=False,
        ),
    )
    verdict = verify(cwd=repo)
    assert verdict.outcome is Outcome.ERROR
    assert any(item.check == "execution:writes" for item in verdict.evidence)
    assert any(item.check == "scan:incomplete" for item in verdict.evidence)


def test_post_command_git_failure_is_error(repo, monkeypatch):
    _commit(
        repo,
        _body(
            checks=[{"name": "proof", "argv": [sys.executable, "-c", "print('ok')"]}],
            tamper=False,
        ),
    )
    real_collect = runner_module.collect_diff_snapshot
    calls = 0

    def fail_second(cwd, base, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_collect(cwd, base, **kwargs)
        raise GitDiffError("diff_failed", "simulated post-command failure")

    monkeypatch.setattr(runner_module, "collect_diff_snapshot", fail_second)
    verdict = verify(cwd=repo)
    assert verdict.outcome is Outcome.ERROR
    assert any("post-command diff" in item.detail for item in verdict.evidence)


def test_unexpected_infrastructure_oserror_is_error(repo, monkeypatch):
    def fail_discovery(cwd):
        raise OSError("filesystem unavailable")

    monkeypatch.setattr(runner_module, "discover_repository", fail_discovery)
    verdict = verify(cwd=repo)
    assert verdict.outcome is Outcome.ERROR
    assert verdict.evidence[0].rule_id == "infrastructure.failure"


def test_runner_metadata_and_path_helpers_fail_safely(repo, monkeypatch):
    def fail_process(*args, **kwargs):
        raise OSError("missing")

    monkeypatch.setattr(runner_module.subprocess, "run", fail_process)
    assert runner_module._git_version(repo) == "unknown"
    assert runner_module._detached_head(repo) is False

    monkeypatch.setattr(
        runner_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert runner_module._git_version(repo) == "unknown"
    assert runner_module._detached_head(repo) is True

    folder = repo / "generated-directory"
    folder.mkdir()
    assert runner_module._path_token(repo, "missing", None)[1] == "absent"
    assert runner_module._path_token(repo, "generated-directory", None)[1] == "special"


def test_path_token_does_not_hash_oversized_candidate(repo, monkeypatch):
    oversized = repo / "oversized-output.bin"
    with oversized.open("wb") as stream:
        stream.truncate(runner_module.MAX_SCANNED_BYTES + 1)
    real_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == oversized:
            raise AssertionError("oversized candidate must not be streamed")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    token = runner_module._path_token(repo, "oversized-output.bin", None)

    assert token[1] == "file-too-large"
    assert token[-1] == runner_module.MAX_SCANNED_BYTES + 1


def test_allow_report_records_selector_and_evidence_identity():
    verdict = Verdict()
    verdict.add(
        runner_module.Evidence(
            "tamper:protected-path",
            Status.FAIL,
            "candidate changed dod.yaml",
        )
    )

    runner_module._apply_policy(verdict, strict=False, allow=["tamper"])

    assert verdict.meta["waivers"] == [
        {
            "check": "tamper:protected-path",
            "selector": "tamper",
            "source": "cli:--allow",
            "rule_id": "tamper.protected-path",
            "fingerprint": verdict.evidence[0].fingerprint,
        }
    ]


def test_incomplete_path_enumeration_has_explicit_error(repo):
    snapshot = collect_diff_snapshot(repo, None)
    snapshot.paths_complete = False
    verdict = Verdict()
    runner_module._add_scan_evidence(verdict, snapshot, stage="synthetic")
    assert verdict.outcome is Outcome.ERROR
    assert "path enumeration" in verdict.evidence[0].detail


@pytest.mark.parametrize(
    "code, phrase",
    [
        ("shallow_missing_history", "Fetch"),
        ("no_merge_base", "existing commit"),
        ("invalid_policy_object", "regular dod.yaml"),
        ("not_worktree", "worktree"),
        ("other", "Repair"),
    ],
)
def test_git_error_hints_are_actionable(code, phrase):
    assert phrase in runner_module._git_hint(code)


def test_scanner_clock_excludes_paused_intervals():
    clock = runner_module._ScannerClock()
    assert clock.seconds == 0.0

    clock.start()
    first = clock.seconds
    assert first > 0.0
    clock.pause()
    paused = clock.seconds
    # A paused clock does not advance, so proof-command runtime is excluded.
    assert clock.seconds == paused >= first
    # Repeated start/pause is safe and keeps accumulating.
    clock.start()
    clock.start()
    clock.pause()
    clock.pause()
    assert clock.seconds >= paused


def test_scanner_duration_excludes_proof_command_runtime(repo):
    _commit(
        repo,
        _body(
            checks=[
                {
                    "name": "slow",
                    "argv": [sys.executable, "-c", "import time; time.sleep(1.0)"],
                    "writes": [],
                }
            ],
            protected=["dod.yaml"],
        ),
    )

    verdict = verify(repo / "dod.yaml", cwd=repo)

    assert verdict.passed
    # The command sleeps a full second; scanner-only time must not include it.
    assert verdict.meta["duration"] >= 1.0
    assert verdict.meta["scanner_duration"] > 0.0
    assert verdict.meta["scanner_duration"] < verdict.meta["duration"] - 0.5


def test_scanner_metadata_reports_inspectable_bytes(repo):
    _commit(repo, _body(protected=["dod.yaml"]))
    (repo / "app.py").write_text("x = 1\n" * 100, encoding="utf-8")

    verdict = verify(repo / "dod.yaml", cwd=repo)

    assert verdict.meta["inspectable_bytes"] > 0
    assert verdict.meta["files_changed"] >= 1
    assert verdict.meta["scanner_duration"] > 0.0


def test_inspectable_bytes_counts_only_inspected_content(repo):
    (repo / "app.py").write_text("x = 1\n" * 50, encoding="utf-8")
    (repo / "extra.py").write_text("y = 2\n", encoding="utf-8")
    snapshot = collect_diff_snapshot(repo, None)
    assert snapshot.files

    baseline = snapshot.inspectable_bytes
    assert baseline > 0
    assert baseline == sum(
        (item.old_size or 0) + (item.new_size or 0)
        for item in snapshot.files
        if item.content_scanned
    )

    # Path-only transitions (binary or oversized) are not inspectable workload.
    for item in snapshot.files:
        item.content_scanned = False
    assert snapshot.inspectable_bytes == 0
