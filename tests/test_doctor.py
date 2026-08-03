from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import dunnit.doctor as doctor_module
from dunnit.contract import CommandCheck, Contract, Requirements
from dunnit.doctor import _check_command, doctor
from dunnit.verdict import Outcome, Status, Verdict


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_policy(repo: Path, body: str) -> str:
    (repo / "dod.yaml").write_text(body, encoding="utf-8")
    _git(repo, "add", "dod.yaml")
    _git(repo, "commit", "-qm", "add verification policy")
    return _git(repo, "rev-parse", "HEAD")


def _v2_policy(*, argv: list[str] | None = None, directory: str | None = None) -> str:
    check: dict[str, object] = {
        "name": "proof",
        "argv": argv or [sys.executable, "--version"],
    }
    if directory is not None:
        check["dir"] = directory
    return yaml.safe_dump(
        {
            "version": 2,
            "checks": [check],
            "protected": ["dod.yaml"],
            "tamper": False,
            "stubs": False,
            "strict": False,
        },
        sort_keys=False,
    )


def _evidence(verdict: Verdict, rule_id: str):
    return next(item for item in verdict.evidence if item.rule_id == rule_id)


def test_valid_v2_policy_reports_trusted_complete_readiness(repo):
    head = _commit_policy(repo, _v2_policy())

    verdict = doctor(cwd=repo)

    assert verdict.outcome is Outcome.PASS
    assert verdict.scan_complete is True
    assert verdict.meta["policy"]["origin"] == f"git:{head}:dod.yaml"
    assert verdict.meta["policy"]["version"] == 2
    assert _evidence(verdict, "doctor.policy-trust").status is Status.PASS
    assert _evidence(verdict, "doctor.schema-version").status is Status.PASS
    assert _evidence(verdict, "doctor.command-available").status is Status.PASS
    assert _evidence(verdict, "doctor.meaningful-policy").status is Status.PASS


def test_nonmatching_test_globs_are_reported_as_vacuous(repo):
    body = yaml.safe_dump(
        {
            "version": 2,
            "checks": [{"name": "proof", "argv": [sys.executable, "--version"]}],
            "protected": ["dod.yaml"],
            "test_globs": ["never-present-tests/**"],
            "tamper": True,
            "stubs": False,
            "strict": False,
        },
        sort_keys=False,
    )
    _commit_policy(repo, body)

    verdict = doctor(cwd=repo)

    finding = _evidence(verdict, "doctor.test-glob-coverage")
    assert verdict.outcome is Outcome.PASS_WITH_WARNINGS
    assert finding.status is Status.WARN
    assert "match no current" in finding.detail


def test_doctor_errors_receive_fingerprints(repo):
    verdict = doctor(cwd=repo, mode="ci")

    assert verdict.outcome is Outcome.ERROR
    assert verdict.evidence[0].fingerprint is not None


def test_valid_ci_policy_is_loaded_from_the_selected_target(repo):
    head = _commit_policy(repo, _v2_policy())

    verdict = doctor(cwd=repo, mode="ci", policy_ref=head)

    assert verdict.outcome is Outcome.PASS
    assert verdict.meta["mode"] == "ci"
    assert verdict.meta["git"]["target_sha"] == head
    assert verdict.meta["policy"]["origin"] == f"git:{head}:dod.yaml"


def test_v1_shell_policy_warns_without_running_the_command(repo):
    sentinel = repo / "doctor-must-not-run-commands"
    body = yaml.safe_dump(
        {
            "version": 1,
            "checks": [
                {
                    "name": "legacy-shell",
                    "run": f'{sys.executable} -c "open({str(sentinel)!r}, \'w\').write(\'ran\')"',
                }
            ],
            "protected": ["dod.yaml"],
            "tamper": False,
            "stubs": False,
        },
        sort_keys=False,
    )
    _commit_policy(repo, body)

    verdict = doctor(cwd=repo)

    assert verdict.outcome is Outcome.PASS_WITH_WARNINGS
    assert verdict.scan_complete is True
    assert _evidence(verdict, "doctor.schema-version").status is Status.WARN
    assert _evidence(verdict, "doctor.shell-command").status is Status.WARN
    assert not sentinel.exists()


def test_modified_trusted_policy_fails_even_when_candidate_disables_protection(repo):
    head = _commit_policy(repo, _v2_policy())
    (repo / "dod.yaml").write_text(
        "version: 2\nchecks: []\nprotected: []\ntamper: false\nstubs: true\n",
        encoding="utf-8",
    )

    verdict = doctor(cwd=repo)

    finding = _evidence(verdict, "doctor.policy-worktree")
    assert verdict.outcome is Outcome.FAIL
    assert verdict.scan_complete is True
    assert finding.status is Status.FAIL
    assert finding.path == "dod.yaml"
    assert verdict.meta["policy"]["origin"] == f"git:{head}:dod.yaml"


def test_renamed_trusted_policy_is_detected_through_its_old_path(repo):
    _commit_policy(repo, _v2_policy())
    _git(repo, "mv", "dod.yaml", "renamed-policy.yaml")

    verdict = doctor(cwd=repo)

    assert verdict.outcome is Outcome.FAIL
    assert _evidence(verdict, "doctor.policy-worktree").path == "dod.yaml"


@pytest.mark.parametrize("executable", ["dunnit-command-that-does-not-exist", "./missing/tool"])
def test_missing_command_executable_fails_readiness(repo, executable):
    _commit_policy(repo, _v2_policy(argv=[executable, "--version"]))

    verdict = doctor(cwd=repo)

    finding = _evidence(verdict, "doctor.command-available")
    assert verdict.outcome is Outcome.FAIL
    assert verdict.scan_complete is True
    assert finding.status is Status.FAIL
    assert executable in finding.detail


@pytest.mark.parametrize(
    ("directory", "rule_id", "expected_outcome", "expected_status", "detail"),
    [
        (
            "missing-package",
            "doctor.command-directory",
            Outcome.FAIL,
            Status.FAIL,
            "does not exist",
        ),
        ("app.py", "doctor.command-directory", Outcome.FAIL, Status.FAIL, "does not exist"),
        ("../outside", "doctor.contract", Outcome.ERROR, Status.ERROR, "repository root"),
    ],
)
def test_programmatic_contract_directory_is_fail_closed(
    repo, directory, rule_id, expected_outcome, expected_status, detail
):
    contract = Contract(
        version=2,
        checks=[CommandCheck("proof", argv=[sys.executable, "--version"], dir=directory)],
        protected=[],
        tamper=False,
        stubs=False,
    )

    verdict = doctor(contract, cwd=repo)

    finding = _evidence(verdict, rule_id)
    assert verdict.outcome is expected_outcome
    assert finding.status is expected_status
    assert detail in finding.detail
    assert verdict.scan_complete is (expected_outcome is not Outcome.ERROR)


def test_invalid_mode_and_ci_without_target_are_contract_errors(repo):
    invalid_mode = doctor(cwd=repo, mode="remote")
    missing_target = doctor(cwd=repo, mode="ci")

    for verdict in (invalid_mode, missing_target):
        finding = _evidence(verdict, "doctor.contract")
        assert verdict.outcome is Outcome.ERROR
        assert verdict.scan_complete is False
        assert finding.status is Status.ERROR
    assert "mode must be" in invalid_mode.evidence[0].detail
    assert "requires --policy-ref or --base" in missing_target.evidence[0].detail


def test_git_discovery_and_invalid_ref_errors_never_pass(repo, tmp_path_factory):
    _commit_policy(repo, _v2_policy())
    outside = tmp_path_factory.mktemp("not-a-repository")

    not_worktree = doctor(cwd=outside)
    invalid_ref = doctor(cwd=repo, base="--option-like-ref")

    assert not_worktree.outcome is Outcome.ERROR
    assert not_worktree.scan_complete is False
    assert not_worktree.evidence[0].check == "git:not_worktree"
    assert invalid_ref.outcome is Outcome.ERROR
    assert invalid_ref.scan_complete is False
    assert invalid_ref.evidence[0].check == "git:invalid_ref"


def test_shallow_checkout_is_explicitly_warned(repo, tmp_path_factory):
    _commit_policy(repo, _v2_policy())
    checkout = tmp_path_factory.mktemp("shallow") / "checkout"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--no-local", str(repo), str(checkout)],
        check=True,
        capture_output=True,
    )

    verdict = doctor(cwd=checkout)

    assert verdict.outcome is Outcome.PASS_WITH_WARNINGS
    assert verdict.scan_complete is True
    assert verdict.meta["git"]["shallow"] is True
    assert _evidence(verdict, "doctor.shallow-checkout").status is Status.WARN


def test_policy_inspection_oserror_is_an_incomplete_infrastructure_error(repo, monkeypatch):
    def fail_policy_load(**_kwargs):
        raise OSError("policy storage unavailable")

    monkeypatch.setattr(doctor_module, "_load_policy", fail_policy_load)

    verdict = doctor(cwd=repo)

    finding = _evidence(verdict, "doctor.infrastructure")
    assert verdict.outcome is Outcome.ERROR
    assert verdict.scan_complete is False
    assert finding.status is Status.ERROR
    assert "policy storage unavailable" in finding.detail


def test_directory_stat_oserror_is_a_readiness_failure(repo, monkeypatch):
    target = (repo / "unreadable-package").resolve()
    original_stat = Path.stat

    def fail_target_stat(path, *args, **kwargs):
        if path == target:
            raise PermissionError("directory metadata denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_target_stat)
    verdict = Verdict()

    _check_command(
        verdict,
        repo,
        CommandCheck("proof", argv=[sys.executable, "--version"], dir="unreadable-package"),
    )

    finding = _evidence(verdict, "doctor.command-directory")
    assert verdict.outcome is Outcome.FAIL
    assert finding.status is Status.FAIL
    assert "does not exist" in finding.detail


def test_repository_path_enumeration_errors_are_actionable(repo, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise subprocess.SubprocessError("git process failed")

    monkeypatch.setattr(doctor_module.subprocess, "run", unavailable)
    with pytest.raises(OSError, match="could not enumerate repository paths"):
        doctor_module._repository_paths(repo)

    monkeypatch.setattr(
        doctor_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"repository access denied\xff",
        ),
    )
    with pytest.raises(OSError, match="repository access denied"):
        doctor_module._repository_paths(repo)


def test_meaningful_policy_lists_non_command_integrity_rules(repo):
    verdict = Verdict()
    contract = Contract(
        version=2,
        checks=[CommandCheck("proof", argv=[sys.executable, "--version"])],
        protected=[],
        test_globs=[],
        tamper=False,
        stubs=True,
        require=Requirements(changed=["src/**"], non_empty_diff=True),
    )

    doctor_module._check_meaningful_policy(verdict, repo, contract, "dod.yaml")

    finding = _evidence(verdict, "doctor.meaningful-policy")
    assert finding.status is Status.PASS
    assert "1 proof command(s)" in finding.detail
    assert "stub detection" in finding.detail
    assert "positive diff requirements" in finding.detail
    assert _evidence(verdict, "doctor.protected-glob-coverage").status is Status.WARN


def test_command_checker_handles_escape_and_explicit_executable(repo):
    escaped = Verdict()
    doctor_module._check_command(
        escaped,
        repo,
        CommandCheck("escape", argv=[sys.executable], dir="../outside"),
    )
    assert _evidence(escaped, "doctor.command-directory").status is Status.ERROR

    tool_name = "proof-tool.cmd" if os.name == "nt" else "proof-tool"
    tool = repo / "tools" / tool_name
    tool.parent.mkdir()
    tool.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
    if os.name != "nt":
        tool.chmod(0o755)
    explicit = Verdict()
    _check_command(
        explicit,
        repo,
        CommandCheck("explicit", argv=[f"./tools/{tool_name}"]),
    )
    assert _evidence(explicit, "doctor.command-available").status is Status.PASS

    non_executable = repo / "tools" / "proof-data.txt"
    non_executable.write_text("not executable\n", encoding="utf-8")
    unavailable = Verdict()
    _check_command(
        unavailable,
        repo,
        CommandCheck("not-executable", argv=["./tools/proof-data.txt"]),
    )
    assert _evidence(unavailable, "doctor.command-available").status is Status.FAIL


def test_explicit_executable_runnability_follows_each_platform_rule(repo, monkeypatch):
    tools = repo / "tools"
    tools.mkdir()
    shim = tools / "proof-tool.cmd"
    shim.write_text("@exit /b 0\n", encoding="utf-8")
    data = tools / "proof-data.txt"
    data.write_text("not executable\n", encoding="utf-8")

    # Windows has no execute bit, so PATHEXT decides — identically on every
    # supported Python version, unlike shutil.which/os.access.
    monkeypatch.setattr(doctor_module, "os", SimpleNamespace(name="nt"))
    windows_env = {"PATHEXT": ".COM;.EXE;.BAT;.CMD"}
    assert doctor_module._is_executable_file(shim, windows_env) is True
    assert doctor_module._is_executable_file(data, windows_env) is False
    assert doctor_module._is_executable_file(shim, {}) is True
    assert doctor_module._is_executable_file(tools / "absent.cmd", {}) is False
    assert doctor_module._is_executable_file(tools, windows_env) is False

    monkeypatch.setattr(
        doctor_module,
        "os",
        SimpleNamespace(name="posix", X_OK=os.X_OK, access=lambda path, mode: path == shim),
    )
    assert doctor_module._is_executable_file(shim, {}) is True
    assert doctor_module._is_executable_file(data, {}) is False


def test_policy_that_only_repeats_its_trust_root_is_not_meaningful(repo):
    _commit_policy(
        repo,
        "version: 2\nchecks: []\nprotected: [dod.yaml]\ntest_globs: []\n"
        "tamper: false\nstubs: false\nstrict: false\n",
    )

    verdict = doctor(cwd=repo)

    assert verdict.outcome is Outcome.ERROR
    assert "protecting only the selected contract path" in verdict.evidence[0].detail
