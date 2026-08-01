import pytest

import dunnit.checks.tamper as tamper_module
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


def test_renamed_away_test_fails(repo):
    import subprocess
    subprocess.run(
        ["git", "mv", "tests/test_app.py", "app_backup.txt"],
        cwd=repo, check=True, capture_output=True,
    )
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    deleted = [e for e in failing(ev) if e.check == "tamper:deleted-tests"]
    assert deleted and "tests/test_app.py -> app_backup.txt" in deleted[0].detail


def test_rename_inside_test_directory_must_preserve_discovery_name(repo):
    import subprocess

    subprocess.run(
        ["git", "mv", "tests/test_app.py", "tests/app.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    deleted = [e for e in failing(ev) if e.check == "tamper:deleted-tests"]
    assert deleted and "tests/test_app.py -> tests/app.py" in deleted[0].detail


def test_rename_inside_test_directory_can_preserve_discovery_name(repo):
    import subprocess

    subprocess.run(
        ["git", "mv", "tests/test_app.py", "tests/test_sum.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert not any(e.check == "tamper:deleted-tests" for e in failing(ev))


def test_focused_test_fails(repo, commit):
    p = repo / "tests" / "app.test.js"
    p.write_text("test('adds', () => { expect(1 + 1).toBe(2); });\n")
    commit()
    p.write_text("test.only('adds', () => { expect(1 + 1).toBe(2); });\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:focused-tests" for e in failing(ev))


def test_go_skip_fails(repo, commit):
    p = repo / "calc_test.go"
    p.write_text("package main\n")
    commit()
    p.write_text('package main\nfunc TestX(t *testing.T) {\n\tt.Skip("later")\n}\n')
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:added-skips" for e in failing(ev))


def test_trivial_assertion_fails(repo):
    p = repo / "tests" / "test_app.py"
    p.write_text("from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n    assert True\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:trivial-assertions" for e in failing(ev))


def test_skip_marker_inside_string_not_flagged(repo):
    p = repo / "tests" / "test_app.py"
    p.write_text("from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n    fixture = '@pytest.mark.skip'\n    assert fixture\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert not any(e.check == "tamper:added-skips" for e in failing(ev))


def test_pytest_ini_deselect_fails(repo, commit):
    p = repo / "pytest.ini"
    p.write_text("[pytest]\n")
    commit()
    p.write_text("[pytest]\naddopts = --ignore=tests/test_app.py\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:test-config" for e in failing(ev))


def test_dedicated_config_edit_warns(repo, commit):
    p = repo / "jest.config.js"
    p.write_text("module.exports = {};\n")
    commit()
    p.write_text("module.exports = { verbose: true };\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    warns = [e for e in ev if e.status is Status.WARN]
    assert any(e.check == "tamper:test-config-changed" for e in warns)
    assert not failing(ev)


def test_conftest_collect_ignore_fails(repo, commit):
    p = repo / "conftest.py"
    p.write_text("import sys\n")
    commit()
    p.write_text("import sys\ncollect_ignore = [\"tests/test_app.py\"]\n")
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert any(e.check == "tamper:test-config" for e in failing(ev))


def test_benign_shared_config_edit_not_flagged(repo, commit):
    p = repo / "pyproject.toml"
    p.write_text('[project]\nversion = "1.0.0"\n')
    commit()
    p.write_text('[project]\nversion = "1.0.1"\n')
    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert not failing(ev)
    assert not any(e.status is Status.WARN for e in ev)


def test_quoted_json_deselection_value_fails(repo, commit):
    p = repo / "package.json"
    p.write_text('{"jest": {"testPathIgnorePatterns": []}}\n')
    commit()
    p.write_text('{"jest": {"testPathIgnorePatterns": ["tests/integration"]}}\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    failures = [e for e in failing(ev) if e.check == "tamper:test-config"]
    assert failures and failures[0].path == "package.json"


def test_quoted_toml_deselection_value_fails(repo, commit):
    p = repo / "pyproject.toml"
    p.write_text('[tool.pytest.ini_options]\naddopts = "-q"\n')
    commit()
    p.write_text('[tool.pytest.ini_options]\naddopts = "-q --ignore=tests/integration"\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-config" for e in failing(ev))


def test_quoted_yaml_ignore_key_fails(repo, commit):
    p = repo / "codecov.yml"
    p.write_text("coverage:\n  precision: 2\n")
    commit()
    p.write_text('coverage:\n  precision: 2\n"ignore": ["src/critical.py"]\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-config" for e in failing(ev))


def test_deselect_text_inside_conftest_fixture_is_not_flagged(repo, commit):
    p = repo / "conftest.py"
    p.write_text("HELP = 'pytest options'\n")
    commit()
    p.write_text("HELP = 'pytest --ignore=PATH is supported'\n")

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert not any(e.check == "tamper:test-config" for e in failing(ev))


def test_deleted_dedicated_test_config_fails(repo, commit):
    p = repo / "pytest.ini"
    p.write_text("[pytest]\naddopts = -q\n")
    commit()
    p.unlink()

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    failures = [e for e in failing(ev) if e.check == "tamper:test-config-removed"]
    assert failures and failures[0].path == "pytest.ini"


def test_renamed_away_dedicated_test_config_fails(repo, commit):
    import subprocess

    p = repo / "jest.config.js"
    p.write_text("module.exports = {};\n")
    commit()
    subprocess.run(
        ["git", "mv", "jest.config.js", "retired-jest-config.txt"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    failures = [e for e in failing(ev) if e.check == "tamper:test-config-removed"]
    assert failures and failures[0].path == "jest.config.js"
    assert "outside its runner scope" in failures[0].detail


def test_root_dedicated_config_moved_to_archive_fails(repo, commit):
    import subprocess

    p = repo / "jest.config.js"
    p.write_text("module.exports = {};\n")
    commit()
    (repo / "archive").mkdir()
    subprocess.run(
        ["git", "mv", "jest.config.js", "archive/jest.config.js"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-config-removed" for e in failing(ev))


def test_same_scope_runner_config_migration_is_review_warning(repo, commit):
    import subprocess

    p = repo / "jest.config.js"
    p.write_text("export default {};\n")
    commit()
    subprocess.run(
        ["git", "mv", "jest.config.js", "vitest.config.js"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert not any(e.check == "tamper:test-config-removed" for e in failing(ev))
    assert any(e.check == "tamper:test-config-changed" for e in ev)


def test_deleted_conftest_fails_as_lost_proof_config(repo, commit):
    p = repo / "conftest.py"
    p.write_text("def pytest_configure(config):\n    config.addinivalue_line('markers', 'slow')\n")
    commit()
    p.unlink()

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-config-removed" for e in failing(ev))


def test_deleted_pyproject_with_pytest_policy_fails(repo, commit):
    p = repo / "pyproject.toml"
    p.write_text('[tool.pytest.ini_options]\naddopts = "-q"\n')
    commit()
    p.unlink()

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-config-removed" for e in failing(ev))


def test_deleted_pyproject_without_proof_policy_is_not_tamper(repo, commit):
    p = repo / "pyproject.toml"
    p.write_text('[project]\nname = "old-package"\n')
    commit()
    p.unlink()

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert not any(e.check == "tamper:test-config-removed" for e in failing(ev))


def test_root_shared_config_moved_out_of_scope_fails(repo, commit):
    import subprocess

    p = repo / "package.json"
    p.write_text('{"scripts": {"test": "jest"}}\n')
    commit()
    (repo / "archive").mkdir()
    subprocess.run(
        ["git", "mv", "package.json", "archive/package.json"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-config-removed" for e in failing(ev))


def test_assertions_added_elsewhere_do_not_offset_file_local_removal(repo, commit):
    first = repo / "tests" / "test_first.py"
    second = repo / "tests" / "test_second.py"
    first.write_text("def test_first():\n    assert 1 == 1\n    assert 2 == 2\n")
    second.write_text("def test_second():\n    assert 3 == 3\n")
    commit()
    first.write_text("def test_first():\n    assert 1 == 1\n")
    second.write_text(
        "def test_second():\n    assert 3 == 3\n    assert 4 == 4\n    assert 5 == 5\n"
    )

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    failures = [e for e in failing(ev) if e.check == "tamper:removed-assertions"]
    assert len(failures) == 1
    assert failures[0].path == "tests/test_first.py"


def test_assertion_rewrite_within_same_file_is_not_net_removal(repo, commit):
    p = repo / "tests" / "test_rewrite.py"
    p.write_text("def test_value():\n    assert value() == 1\n")
    commit()
    p.write_text("def test_value():\n    assert value() == expected_value()\n")

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert not any(e.check == "tamper:removed-assertions" for e in failing(ev))


def test_new_empty_python_test_fails(repo):
    p = repo / "tests" / "test_empty.py"
    p.write_text("def test_nothing():\n    pass\n")

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    failures = [e for e in failing(ev) if e.check == "tamper:empty-tests"]
    assert failures and failures[0].path == "tests/test_empty.py"


def test_new_empty_javascript_test_fails(repo):
    p = repo / "tests" / "empty.test.js"
    p.write_text("test('does nothing', () => {\n});\n")

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:empty-tests" for e in failing(ev))


@pytest.mark.parametrize("noop", ["pass", "...", "return None"])
def test_existing_asserting_test_replaced_by_noop_fails(repo, noop):
    p = repo / "tests" / "test_app.py"
    p.write_text(f"from app import add\n\ndef test_add():\n    {noop}\n")

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:empty-tests" for e in failing(ev))


def test_pytest_raises_loss_counts_as_assertion_and_empty_test(repo, commit):
    p = repo / "tests" / "test_errors.py"
    p.write_text(
        "import pytest\n\ndef test_error():\n"
        "    with pytest.raises(ValueError):\n        raise ValueError('expected')\n"
    )
    commit()
    p.write_text("import pytest\n\ndef test_error():\n    pass\n")

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    failures = {e.check for e in failing(ev)}

    assert "tamper:empty-tests" in failures
    assert "tamper:removed-assertions" in failures


def test_non_test_noop_function_is_not_empty_test(repo):
    p = repo / "tests" / "test_helper.py"
    p.write_text("def helper():\n    pass\n")

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert not any(e.check == "tamper:empty-tests" for e in failing(ev))


def test_path_scoped_replacement_test_command_fails(repo, commit):
    p = repo / "package.json"
    p.write_text('{"scripts": {"test": "pytest -q"}}\n')
    commit()
    p.write_text('{"scripts": {"test": "pytest -q tests/unit"}}\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-command-narrowed" for e in failing(ev))


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("pytest -q", "pytest -q -k unit"),
        ("pytest -q", "pytest -q test_app.py"),
        ("jest", "jest --runTestsByPath tests/unit.test.js"),
        ("go test ./...", "go test ./pkg/api"),
        ("cargo test --workspace", "cargo test -p api"),
        ("cargo test", "cargo test --no-run"),
    ],
)
def test_clear_cross_ecosystem_test_command_narrowing_fails(repo, commit, before, after):
    p = repo / "package.json"
    p.write_text(f'{{"scripts": {{"test": "{before}"}}}}\n')
    commit()
    p.write_text(f'{{"scripts": {{"test": "{after}"}}}}\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-command-narrowed" for e in failing(ev))


@pytest.mark.parametrize("replacement", ["true", ":", "exit 0", "echo tests disabled"])
def test_test_script_replaced_by_noop_fails(repo, commit, replacement):
    p = repo / "package.json"
    p.write_text('{"scripts": {"test": "pytest -q"}}\n')
    commit()
    p.write_text(f'{{"scripts": {{"test": "{replacement}"}}}}\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-command-disabled" for e in failing(ev))


def test_test_script_removal_fails(repo, commit):
    p = repo / "package.json"
    p.write_text('{"scripts": {"test": "pytest -q", "lint": "ruff check ."}}\n')
    commit()
    p.write_text('{"scripts": {"lint": "ruff check ."}}\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert any(e.check == "tamper:test-command-disabled" for e in failing(ev))


def test_runner_migration_in_test_script_is_not_treated_as_disablement(repo, commit):
    p = repo / "package.json"
    p.write_text('{"scripts": {"test": "pytest -q"}}\n')
    commit()
    p.write_text('{"scripts": {"test": "tox -q"}}\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    command_findings = {
        e.check for e in failing(ev) if e.check.startswith("tamper:test-command")
    }
    assert not command_findings


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("pytest -q", "pytest -q --maxfail=1"),
        ("jest", "jest --coverage"),
        ("go test ./...", "go test -race ./..."),
        ("cargo test --workspace", "cargo test --workspace --all-features"),
        ("cargo test", "cargo test --color always"),
        ("pytest --collect-only", "pytest --collect-only -q"),
    ],
)
def test_non_narrowing_runner_option_change_is_not_flagged(repo, commit, before, after):
    p = repo / "package.json"
    p.write_text(f'{{"scripts": {{"test": "{before}"}}}}\n')
    commit()
    p.write_text(f'{{"scripts": {{"test": "{after}"}}}}\n')

    ev = check_tamper(collect_diff(repo, None), DEFAULT_TEST_GLOBS)

    assert not any(e.check == "tamper:test-command-narrowed" for e in failing(ev))


def test_stub_detection_skips_docs(repo):
    (repo / "README.md").write_text("example:\n\n    raise NotImplementedError\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "docs"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("example:\n\n    raise NotImplementedError\n    # TODO more docs\n")
    ev = check_stubs(collect_diff(repo, None), DEFAULT_TEST_GLOBS)
    assert all(e.status is not Status.WARN or "README" not in e.detail for e in ev)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_unit.py", True),
        ("web/button.test.ts", True),
        ("pkg/value_test.go", True),
        ("tests/integration.rs", True),
        ("src/WidgetIT.java", True),
        ("spec/widget_spec.rb", True),
        ("tests/WidgetTests.cs", True),
        ("tests/WidgetTest.php", True),
        ("src/application.py", False),
    ],
)
def test_discoverable_test_path_covers_supported_ecosystems(path, expected):
    assert tamper_module._discoverable_test_path(path) is expected


def test_empty_test_helper_handles_inline_comments_dedent_and_unknown_language():
    lines = [
        "def test_inline(): pass",
        "def test_body():",
        "    # explanation",
        "    ...",
        "def helper():",
        "    return 1",
    ]

    empty = tamper_module._empty_added_tests(lines, "py")

    assert "def test_inline(): pass" in empty
    assert "def test_body():" in empty
    assert tamper_module._empty_added_tests(["test"], None) == []


@pytest.mark.parametrize(
    ("path", "lines", "expected"),
    [
        ("tox.ini", [], True),
        ("setup.cfg", ["[tool:pytest]"], True),
        ("setup.cfg", ["[metadata]"], False),
        ("package.json", ['"test": "vitest"'], True),
        ("unknown.cfg", ["pytest"], False),
    ],
)
def test_shared_config_proof_detection(path, lines, expected):
    assert tamper_module._shared_config_has_proof(path, lines) is expected
