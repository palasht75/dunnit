import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from dunnit import __version__
from dunnit.cli import _github_snippet, _print_text, _render_migrated_contract, main
from dunnit.contract import CommandCheck, Contract
from dunnit.runner import verify
from dunnit.verdict import Evidence, Outcome, Status, Verdict


def _policy(*, checks=None, protected=None, tamper=False, stubs=False, strict=False, require=None):
    data = {
        "version": 2,
        "checks": checks or [],
        "protected": ["dod.yaml"] if protected is None else protected,
        "tamper": tamper,
        "stubs": stubs,
        "strict": strict,
    }
    if require is not None:
        data["require"] = require
    return yaml.safe_dump(data, sort_keys=False)


def _commit_policy(repo: Path, body: str, message: str = "add policy") -> None:
    (repo / "dod.yaml").write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "dod.yaml"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True, capture_output=True)


def test_verify_end_to_end_uses_committed_policy(repo, capsys):
    _commit_policy(
        repo,
        _policy(
            checks=[
                {
                    "name": "smoke",
                    "argv": [sys.executable, "-c", "print(1)"],
                }
            ]
        ),
    )
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n# comment\n")
    verdict = verify(repo / "dod.yaml", cwd=repo)
    assert verdict.passed
    assert verdict.outcome is Outcome.PASS
    assert any(item.check == "smoke" for item in verdict.evidence)


def test_cli_fail_and_error_exit_codes(repo, tmp_path, monkeypatch):
    _commit_policy(
        repo,
        _policy(
            checks=[
                {
                    "name": "boom",
                    "argv": [sys.executable, "-c", "raise SystemExit(3)"],
                }
            ]
        ),
    )
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["verify", "--format", "json"]) == 2


def test_init_refuses_empty_or_ambiguous_detection(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 2
    assert not (tmp_path / "dod.yaml").exists()
    assert "no proof commands" in capsys.readouterr().err


def test_init_python_preview_and_write(tmp_path, monkeypatch, capsys):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--preset", "python", "--dry-run"]) == 0
    preview = capsys.readouterr().out
    assert "version: 2" in preview and "argv:" in preview
    assert not (tmp_path / "dod.yaml").exists()

    assert main(["init", "--preset", "python"]) == 0
    body = yaml.safe_load((tmp_path / "dod.yaml").read_text())
    assert body["checks"][0]["argv"] == ["python", "-m", "pytest", "-q"]
    assert body["checks"][0]["writes"] == []
    assert "**/test_*.py" in body["test_globs"]
    assert main(["init", "--preset", "python"]) == 1
    assert main(["init", "--preset", "python", "--force"]) == 0


def test_init_does_not_infer_pytest_from_pyproject_alone(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--preset", "python"]) == 2
    assert not (tmp_path / "dod.yaml").exists()


def test_init_detects_declared_node_script(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest"}}\n')
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--preset", "node"]) == 0
    body = yaml.safe_load((tmp_path / "dod.yaml").read_text())
    assert body["checks"][0]["argv"] == ["npm", "run", "test", "--silent"]
    assert "**/*.test.*" in body["test_globs"]


def test_uncommitted_policy_requires_explicit_bootstrap(repo, monkeypatch, capsys):
    (repo / "dod.yaml").write_text(_policy())
    monkeypatch.chdir(repo)
    assert main(["verify", "--format", "json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["outcome"] == "error"
    assert "--bootstrap" in error["evidence"][0]["detail"]

    assert main(["verify", "--bootstrap"]) == 0
    assert "contract satisfied" in capsys.readouterr().out


def test_verify_base_json_and_report(repo, monkeypatch, capsys):
    _commit_policy(repo, _policy())
    monkeypatch.chdir(repo)
    report = repo / "artifacts" / "dunnit.json"
    assert main(["verify", "--base", "HEAD", "--json", "--report", str(report)]) == 0
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(report.read_text())
    assert payload["outcome"] == "pass"
    assert saved["schema_version"] == 1
    assert saved["meta"]["policy"]["origin"].startswith("git:")
    assert "remote" not in saved["meta"]["git"]


def test_github_and_junit_formats(repo, monkeypatch, capsys):
    _commit_policy(repo, _policy())
    monkeypatch.chdir(repo)

    assert main(["verify", "--format", "github"]) == 0
    assert "Dunnit outcome: pass" in capsys.readouterr().out
    assert main(["verify", "--format", "junit"]) == 0
    junit = capsys.readouterr().out
    assert "<testsuite" in junit and 'name="dunnit"' in junit


def test_conflicting_json_format_is_contract_error(capsys):
    assert main(["verify", "--json", "--format", "junit"]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_report_and_github_summary_write_errors_fail_closed(
    repo, monkeypatch, capsys
):
    _commit_policy(repo, _policy())
    monkeypatch.chdir(repo)

    def fail_report(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("dunnit.cli.write_report", fail_report)
    assert main(["verify", "--report", "report.json", "--format", "json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "error"
    assert any(item["rule_id"] == "report.write" for item in payload["evidence"])

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr("dunnit.cli.write_github_summary", fail_report)
    assert main(["verify", "--format", "github"]) == 2
    assert "report.github-summary" in capsys.readouterr().out


def test_agent_editing_contract_fails_and_allow_is_recorded(repo, monkeypatch, capsys):
    _commit_policy(repo, _policy(stubs=True))
    (repo / "dod.yaml").write_text(_policy(stubs=False))
    monkeypatch.chdir(repo)
    assert main(["verify", "--format", "json"]) == 1
    capsys.readouterr()
    assert main(
        ["verify", "--allow", "tamper:protected-path", "--format", "json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "pass_with_warnings"
    waiver = payload["meta"]["waivers"][0]
    assert waiver["check"] == "tamper:protected-path"
    assert waiver["selector"] == "tamper:protected-path"
    assert waiver["source"] == "cli:--allow"
    assert waiver["rule_id"] == "tamper.protected-path"
    assert waiver["fingerprint"].startswith("sha256:")


def test_strict_promotes_stub_warning(repo, monkeypatch):
    _commit_policy(repo, _policy(stubs=True))
    (repo / "app.py").write_text("def add(a, b):\n    # TODO handle overflow\n    return a + b\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 0
    assert main(["verify", "--strict"]) == 1


def test_require_in_contract_is_enforced(repo, monkeypatch):
    _commit_policy(repo, _policy(require={"changed": ["tests/**"]}))
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1
    test_file = repo / "tests" / "test_app.py"
    test_file.write_text(test_file.read_text() + "\ndef test_mul():\n    assert add(2, 2) == 4\n")
    assert main(["verify"]) == 0


def test_quiet_hides_passing_evidence(repo, monkeypatch, capsys):
    _commit_policy(
        repo,
        _policy(
            checks=[
                {"name": "smoke", "argv": [sys.executable, "-c", "print(1)"]}
            ]
        ),
    )
    monkeypatch.chdir(repo)
    assert main(["verify", "--quiet"]) == 0
    output = capsys.readouterr().out
    assert "smoke:" not in output
    assert "contract satisfied" in output


def test_migrate_preview_then_write_preserves_shell_form(tmp_path, monkeypatch, capsys):
    policy = tmp_path / "dod.yaml"
    policy.write_text(
        "version: 1\nchecks:\n  - name: tests\n    run: python -m pytest -q\n"
        "protected: [dod.yaml]\ntamper: false\nstubs: false\n"
    )
    monkeypatch.chdir(tmp_path)
    assert main(["migrate", "--dry-run"]) == 0
    preview = yaml.safe_load(capsys.readouterr().out.split("\n", 1)[1])
    assert preview["version"] == 2
    assert preview["checks"][0]["run"] == "python -m pytest -q"
    assert policy.read_text().startswith("version: 1")

    assert main(["migrate", "--write"]) == 0
    migrated = yaml.safe_load(policy.read_text())
    assert migrated["version"] == 2
    assert migrated["checks"][0]["writes"] == []


def test_migrate_errors_already_v2_and_programmatic_argv(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["migrate", "--dry-run"]) == 2
    assert "contract not found" in capsys.readouterr().err

    (tmp_path / "dod.yaml").write_text(_policy())
    assert main(["migrate", "--write"]) == 0
    assert "already uses" in capsys.readouterr().out

    body = _render_migrated_contract(
        Contract(
            version=1,
            base="main",
            checks=[CommandCheck("tests", argv=["python", "-m", "pytest"])],
        )
    )
    materialized = yaml.safe_load(body)
    assert materialized["base"] == "main"
    assert materialized["checks"][0]["argv"] == ["python", "-m", "pytest"]


def test_doctor_and_snippets(repo, monkeypatch, capsys):
    _commit_policy(
        repo,
        _policy(checks=[{"name": "python", "argv": [sys.executable, "--version"]}]),
    )
    monkeypatch.chdir(repo)
    assert main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "pass"
    assert any(item["check"] == "doctor:command" for item in payload["evidence"])

    assert main(["doctor"]) == 0
    assert "contract satisfied" in capsys.readouterr().out

    assert main(["snippet", "claude"]) == 0
    assert "dunnit verify" in capsys.readouterr().out
    assert main(["snippet", "github", "--mode", "shadow"]) == 0
    workflow = capsys.readouterr().out
    assert "continue-on-error: true" in workflow
    assert "fetch-depth: 0" in workflow
    assert "persist-credentials: false" in workflow
    assert f"dunnit=={__version__}" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow

    assert main(["snippet", "github"]) == 0
    required = capsys.readouterr().out
    assert "continue-on-error" not in required
    assert "required-workflow ruleset" in required
    assert "job name alone" in required
    assert "docs/threat-model.md" in required


def test_github_workflows_are_parseable_and_use_current_immutable_pins():
    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Five-minute GitHub required check", 1)[1]
    readme_workflow = section.split("```yaml\n", 1)[1].split("\n```", 1)[0] + "\n"

    versioned = {
        "generated required": _github_snippet("required"),
        "generated shadow": _github_snippet("shadow"),
        "documented required": readme_workflow,
        "example required": (root / "examples/github/dunnit-required.yml").read_text(
            encoding="utf-8"
        ),
        "example shadow": (root / "examples/github/dunnit-shadow.yml").read_text(
            encoding="utf-8"
        ),
    }
    static = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in (root / ".github/workflows").glob("*.yml")
    }

    for label, workflow in {**static, **versioned}.items():
        assert isinstance(yaml.load(workflow, Loader=yaml.BaseLoader), dict), label
        action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, flags=re.MULTILINE)
        assert action_refs, label
        assert all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref) for ref in action_refs), label

    for label, workflow in versioned.items():
        package_pins = set(re.findall(r"dunnit==([^\"'\s]+)", workflow))
        assert package_pins == {__version__}, label

    documented = yaml.safe_load(readme_workflow)
    checked_in = yaml.safe_load(versioned["example required"])
    assert documented == checked_in


def test_text_renderer_bounds_multiline_details_and_can_color(capsys):
    detail = "first\n" + "\n".join(f"line-{index}" for index in range(40))
    verdict = Verdict(
        [Evidence("long", Status.FAIL, detail, hint="fix it")]
    )
    _print_text(verdict, quiet=False, color=True)
    output = capsys.readouterr().out
    assert "more lines" in output
    assert "fix: fix it" in output
    assert "\x1b[" in output
