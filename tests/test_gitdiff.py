import subprocess

import pytest

from dunnit.gitdiff import collect_diff, matches_any


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


def test_untracked_binary_skipped(repo):
    (repo / "img.bin").write_bytes(b"\x00\xff\xfe binary")
    assert "img.bin" not in [d.path for d in collect_diff(repo, None)]


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
