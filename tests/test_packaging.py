from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


def test_sdist_excludes_private_local_state_and_keeps_release_sources(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    shutil.copytree(
        project,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "venv",
            "dist",
            "build",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "__pycache__",
            "*.pyc",
        ),
    )
    private = source / ".claude" / "settings.local.json"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text('{"private": true}\n', encoding="utf-8")
    (source / "private-local-notes.txt").write_text("do not publish\n", encoding="utf-8")
    output = tmp_path / "dist"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--no-isolation",
            "--outdir",
            str(output),
        ],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )

    archives = list(output.glob("dunnit-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], "r:gz") as archive:
        names = [member.name.split("/", 1)[-1] for member in archive.getmembers()]
    assert not any(name == ".claude" or name.startswith(".claude/") for name in names)
    assert "private-local-notes.txt" not in names
    assert "src/dunnit/dod-v2.schema.json" in names
    assert "benchmarks/aggregate.py" in names
    assert "tests/test_packaging.py" in names
