import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t.dev")
    git("config", "user.name", "t")
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n"
    )
    git("add", "-A")
    git("commit", "-qm", "base")
    return tmp_path


@pytest.fixture
def commit(repo: Path):
    """Stage and commit everything in the repo fixture."""

    def _commit(msg: str = "wip") -> None:
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True, capture_output=True)

    return _commit
