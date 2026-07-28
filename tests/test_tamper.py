from dunnit.checks.stubs import check_stubs
from dunnit.checks.tamper import check_tamper
from dunnit.contract import DEFAULT_TEST_GLOBS
from dunnit.gitdiff import collect_diff
from dunnit.verdict import Status


def failing(evidence):
    return [e for e in evidence if e.status is Status.FAIL]


def test_clean_diff_passes(repo):
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert not failing(ev)


def test_deleted_test_file_fails(repo):
    (repo / "tests" / "test_app.py").unlink()
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:deleted-tests" for e in failing(ev))


def test_added_skip_fails(repo):
    p = repo / "tests" / "test_app.py"
    p.write_text("import pytest\n\n@pytest.mark.skip\ndef test_add():\n    assert True\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:added-skips" for e in failing(ev))


def test_removed_assertions_fail(repo):
    p = repo / "tests" / "test_app.py"
    p.write_text("from app import add\n\ndef test_add():\n    add(1, 2)\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:removed-assertions" for e in failing(ev))


def test_stub_detection_warns(repo):
    (repo / "app.py").write_text("def add(a, b):\n    # TODO implement properly\n    raise NotImplementedError\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    labels = {e.check for e in ev}
    assert "stubs:todo-left-behind" in labels and "stubs:not-implemented" in labels


def test_root_level_test_file_matched(repo):
    p = repo / "test_root.py"
    p.write_text("def test_x():\n    assert 1\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "add root test"], cwd=repo, check=True, capture_output=True)
    p.write_text("import pytest\n@pytest.mark.skip\ndef test_x():\n    pass\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:added-skips" for e in failing(ev))


def test_protected_paths_default_blocks_dod_edit(repo):
    import subprocess

    from dunnit.checks.protected import check_protected
    from dunnit.contract import DEFAULT_PROTECTED

    (repo / "dod.yaml").write_text("version: 1\nchecks: []\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "add dod"], cwd=repo, check=True, capture_output=True)
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\ntamper: false\n")
    ev = check_protected(collect_diff(repo, None), DEFAULT_PROTECTED)
    assert any(e.check == "tamper:protected-path" for e in failing(ev))


def test_protected_paths_custom_glob(repo):
    from dunnit.checks.protected import check_protected

    (repo / "ci.cfg").write_text("x=1\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "cfg"], cwd=repo, check=True, capture_output=True)
    (repo / "ci.cfg").write_text("x=2\n")
    ev = check_protected(collect_diff(repo, None), ["*.cfg"])
    assert any(e.check == "tamper:protected-path" for e in failing(ev))


def test_stub_detection_skips_docs(repo):
    (repo / "README.md").write_text("example:\n\n    raise NotImplementedError\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "docs"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("example:\n\n    raise NotImplementedError\n    # TODO more docs\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert all(e.status is not Status.WARN or "README" not in e.detail for e in ev)
