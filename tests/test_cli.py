
from dunnit.cli import main
from dunnit.runner import verify


def test_verify_end_to_end(repo, capsys):
    (repo / "dod.yaml").write_text(
        'version: 1\nchecks:\n  - name: smoke\n    run: python -c "print(1)"\n'
    )
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n# comment\n")
    v = verify(repo / "dod.yaml", cwd=repo)
    assert v.passed
    assert any(e.check == "smoke" for e in v.evidence)


def test_cli_fail_exit_code(repo, monkeypatch):
    (repo / "dod.yaml").write_text(
        'version: 1\nchecks:\n  - name: boom\n    run: python -c "raise SystemExit(3)"\n'
    )
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1


def test_cli_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert (tmp_path / "dod.yaml").exists()
    assert main(["init"]) == 1  # refuses to overwrite


def test_snippet_command(capsys):
    assert main(["snippet", "claude"]) == 0
    out = capsys.readouterr().out
    assert "dunnit verify" in out and "Stop" in out


def test_verify_base_flag_overrides(repo, monkeypatch, capsys):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\nprotected: []\n")
    monkeypatch.chdir(repo)
    assert main(["verify", "--base", "HEAD", "--json"]) == 0
    assert '"verdict": "pass"' in capsys.readouterr().out


def test_agent_editing_contract_fails_verify(repo, monkeypatch):
    import subprocess
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "dod"], cwd=repo, check=True, capture_output=True)
    # agent tries to weaken its own contract
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\ntamper: false\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1
