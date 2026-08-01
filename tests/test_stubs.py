from dunnit.checks.stubs import check_stubs
from dunnit.contract import DEFAULT_TEST_GLOBS
from dunnit.gitdiff import collect_diff
from dunnit.verdict import Status


def warns(evidence):
    return [e for e in evidence if e.status is Status.WARN]


def test_multiline_except_pass_warns(repo):
    (repo / "app.py").write_text(
        "def add(a, b):\n    try:\n        return a + b\n    except ValueError:\n        pass\n"
    )
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "stubs:swallowed-exception" for e in warns(ev))


def test_empty_js_catch_warns(repo):
    (repo / "util.js").write_text("try { risky(); } catch (e) {}\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "stubs:swallowed-exception" for e in warns(ev))


def test_suppression_comment_warns(repo):
    (repo / "app.py").write_text("import os  # noqa\n\ndef add(a, b):\n    return a + b\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "stubs:suppressed-checker" for e in warns(ev))


def test_coverage_pragma_warns(repo):
    (repo / "app.py").write_text("def add(a, b):  # pragma: no cover\n    return a + b\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "stubs:coverage-excluded" for e in warns(ev))


def test_rust_todo_macro_warns(repo):
    (repo / "lib.rs").write_text("fn add(a: i32, b: i32) -> i32 {\n    todo!()\n}\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "stubs:not-implemented" for e in warns(ev))


def test_findings_aggregated_per_file(repo):
    (repo / "app.py").write_text(
        "def add(a, b):\n    # TODO one\n    # TODO two\n    # TODO three\n    return a + b\n"
    )
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    todos = [e for e in warns(ev) if e.check == "stubs:todo-left-behind"]
    assert len(todos) == 1
    assert "(+2 more)" in todos[0].detail


def test_marker_inside_string_not_flagged(repo):
    (repo / "app.py").write_text(
        "def add(a, b):\n    msg = \"TODO: user-facing text, not a stub\"\n    return a + b\n"
    )
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert not any(e.check == "stubs:todo-left-behind" for e in warns(ev))


def test_untracked_file_with_stub_caught(repo):
    (repo / "new_module.py").write_text("def helper():\n    raise NotImplementedError\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    finding = next(e for e in warns(ev) if e.check == "stubs:not-implemented")
    assert finding.path == "new_module.py"
