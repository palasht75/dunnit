from dunnit.checks.require import check_require
from dunnit.contract import Requirements
from dunnit.gitdiff import collect_diff
from dunnit.verdict import Status


def test_require_changed_missing_fails(repo):
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    require = Requirements(changed=["tests/**"])
    ev = check_require(collect_diff(repo, None), require, "HEAD")
    fails = [e for e in ev if e.status is Status.FAIL]
    assert any(e.check == "require:changed" and "tests/**" in e.detail for e in fails)


def test_require_changed_satisfied(repo):
    p = repo / "tests" / "test_app.py"
    p.write_text(p.read_text() + "\ndef test_add_negative():\n    assert add(-1, 1) == 0\n")
    require = Requirements(changed=["tests/**"])
    ev = check_require(collect_diff(repo, None), require, "HEAD")
    assert all(e.status is not Status.FAIL for e in ev)
    assert any(e.check == "require" and e.status is Status.PASS for e in ev)


def test_deleted_file_does_not_satisfy_require(repo):
    (repo / "tests" / "test_app.py").unlink()
    require = Requirements(changed=["tests/**"])
    ev = check_require(collect_diff(repo, None), require, "HEAD")
    assert any(e.check == "require:changed" for e in ev if e.status is Status.FAIL)


def test_non_empty_diff_fails_on_clean_tree(repo):
    require = Requirements(non_empty_diff=True)
    ev = check_require(collect_diff(repo, None), require, "HEAD")
    assert any(e.check == "require:non-empty-diff" for e in ev if e.status is Status.FAIL)


def test_no_requirements_produces_no_evidence(repo):
    ev = check_require(collect_diff(repo, None), Requirements(), "HEAD")
    assert ev == []
