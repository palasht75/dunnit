from dunnit.checks.commands import run_command
from dunnit.contract import CommandCheck
from dunnit.verdict import Status


def test_env_reaches_command(tmp_path):
    (tmp_path / "envcheck.py").write_text(
        "import os, sys\nsys.exit(0 if os.environ.get('DUNNIT_X') == '1' else 1)\n"
    )
    check = CommandCheck(name="env", run="python envcheck.py", env={"DUNNIT_X": "1"})
    assert run_command(check, tmp_path).status is Status.PASS
    assert run_command(CommandCheck(name="no-env", run="python envcheck.py"), tmp_path).status \
        is Status.FAIL


def test_dir_option_changes_cwd(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "here.py").write_text("import sys\nsys.exit(0)\n")
    check = CommandCheck(name="dir", run="python here.py", dir="sub")
    assert run_command(check, tmp_path).status is Status.PASS


def test_missing_dir_fails_gracefully(tmp_path):
    check = CommandCheck(name="ghost", run="python -c \"print(1)\"", dir="does-not-exist")
    ev = run_command(check, tmp_path)
    assert ev.status is Status.FAIL


def test_failure_captures_output_and_hint(tmp_path):
    check = CommandCheck(name="boom", run="python -c \"print('broken thing'); raise SystemExit(3)\"")
    ev = run_command(check, tmp_path)
    assert ev.status is Status.FAIL
    assert "exited 3" in ev.detail
    assert "broken thing" in ev.detail
    assert ev.hint
