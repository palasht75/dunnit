
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


def test_new_contract_does_not_trip_protection(repo, monkeypatch):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 0


def test_strict_promotes_warnings(repo, monkeypatch):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\nprotected: []\n")
    (repo / "app.py").write_text("def add(a, b):\n    # TODO handle overflow\n    return a + b\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 0
    assert main(["verify", "--strict"]) == 1


def test_strict_in_contract(repo, monkeypatch):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\nprotected: []\nstrict: true\n")
    (repo / "app.py").write_text("def add(a, b):\n    # TODO handle overflow\n    return a + b\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1


def test_allow_downgrades_named_failure(repo, commit, monkeypatch):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\n")
    commit("add contract")
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\nstubs: false\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1
    assert main(["verify", "--allow", "tamper:protected-path"]) == 0


def test_require_in_contract_enforced(repo, monkeypatch):
    (repo / "dod.yaml").write_text(
        "version: 1\nchecks: []\nprotected: []\nrequire:\n  changed:\n    - tests/**\n"
    )
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n")
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1
    p = repo / "tests" / "test_app.py"
    p.write_text(p.read_text() + "\ndef test_mul():\n    assert add(2, 2) == 4\n")
    assert main(["verify"]) == 0


def test_quiet_hides_passes(repo, monkeypatch, capsys):
    (repo / "dod.yaml").write_text(
        'version: 1\nchecks:\n  - name: smoke\n    run: python -c "print(1)"\nprotected: []\n'
    )
    monkeypatch.chdir(repo)
    assert main(["verify", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "smoke" not in out
    assert "verdict" in out


def test_failure_prints_output_and_hint(repo, monkeypatch, capsys):
    (repo / "dod.yaml").write_text(
        "version: 1\nchecks:\n  - name: boom\n"
        '    run: python -c "print(chr(66) + chr(85) + chr(71)); raise SystemExit(2)"\n'
        "protected: []\n"
    )
    monkeypatch.chdir(repo)
    assert main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "BUG" in out
    assert "fix:" in out


def test_json_includes_summary_and_meta(repo, monkeypatch, capsys):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\nprotected: []\n")
    monkeypatch.chdir(repo)
    assert main(["verify", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"summary"' in out
    assert '"meta"' in out


def test_init_detects_node(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}\n')
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    body = (tmp_path / "dod.yaml").read_text()
    assert "npm test" in body


def test_init_detects_python(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    body = (tmp_path / "dod.yaml").read_text()
    assert "pytest" in body


def test_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert main(["init"]) == 1
    assert main(["init", "--force"]) == 0


def test_snippet_codex(capsys):
    assert main(["snippet", "codex"]) == 0
    out = capsys.readouterr().out
    assert "dunnit verify" in out and "AGENTS.md" in out
