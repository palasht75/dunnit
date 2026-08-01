import io
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

from dunnit.checks import commands
from dunnit.checks.commands import (
    _detail_with_output,
    _display_command,
    _drain_output,
    _merge_environment,
    _resolved_argv,
    _sanitize,
    _TailBuffer,
    _terminate_process_tree,
    run_command,
)
from dunnit.contract import CommandCheck
from dunnit.verdict import Status


def test_env_reaches_legacy_shell_command(tmp_path):
    (tmp_path / "envcheck.py").write_text(
        "import os, sys\nsys.exit(0 if os.environ.get('DUNNIT_X') == '1' else 1)\n"
    )
    command = f'"{sys.executable}" envcheck.py'
    check = CommandCheck(name="env", run=command, env={"DUNNIT_X": "1"})
    assert run_command(check, tmp_path).status is Status.PASS
    assert run_command(CommandCheck(name="no-env", run=command), tmp_path).status is Status.FAIL


def test_dir_option_changes_cwd(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "here.py").write_text("import sys\nsys.exit(0)\n")
    check = CommandCheck(name="dir", argv=[sys.executable, "here.py"], dir="sub")
    assert run_command(check, tmp_path).status is Status.PASS


def test_command_runs_from_repository_path_with_spaces(tmp_path):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    (repo / "proof.py").write_text("print('ok')\n")
    check = CommandCheck(name="spaces", argv=[sys.executable, "proof.py"])
    assert run_command(check, repo).status is Status.PASS


def test_missing_dir_is_an_infrastructure_error(tmp_path):
    check = CommandCheck(name="ghost", argv=[sys.executable, "-c", "print(1)"], dir="missing")
    ev = run_command(check, tmp_path)
    assert ev.status is Status.ERROR
    assert ev.scan_complete is False
    assert ev.rule_id == "command.directory-missing"


def test_directory_cannot_escape_repo(tmp_path):
    check = CommandCheck(name="escape", argv=[sys.executable, "-c", "print(1)"], dir="..")
    ev = run_command(check, tmp_path)
    assert ev.status is Status.ERROR
    assert "escapes" in ev.detail


def test_failure_captures_output_hint_and_structured_result(tmp_path):
    check = CommandCheck(
        name="boom",
        argv=[sys.executable, "-c", "print('broken thing'); raise SystemExit(3)"],
    )
    ev = run_command(check, tmp_path)
    assert ev.status is Status.FAIL
    assert "exited 3" in ev.detail
    assert "broken thing" in ev.detail
    assert ev.hint
    assert ev.rule_id == "command.exit-nonzero"
    assert ev.exit_code == 3
    assert ev.duration is not None and ev.duration >= 0
    assert ev.scan_complete is True


def test_argv_is_not_interpreted_by_a_shell(tmp_path):
    marker = tmp_path / "shell-expanded"
    argument = f"ignored; echo owned > {marker}"
    check = CommandCheck(
        name="argv",
        argv=[sys.executable, "-c", "import sys; assert ';' in sys.argv[1]", argument],
    )
    assert run_command(check, tmp_path).status is Status.PASS
    assert not marker.exists()


def test_output_is_bounded_decoded_and_terminal_controls_are_removed(tmp_path):
    script = (
        "import os; "
        "os.write(1, b'x' * 50000 + b'\\x1b[31mBAD\\x1b[0m\\x00\\xff'); "
        "raise SystemExit(4)"
    )
    ev = run_command(CommandCheck(name="output", argv=[sys.executable, "-c", script]), tmp_path)
    assert ev.status is Status.FAIL
    assert "BAD" in ev.detail
    assert "\x1b" not in ev.detail
    assert "\x00" not in ev.detail
    assert "\ufffd" in ev.detail
    assert len(ev.detail) < 4_100


def test_timeout_is_incomplete_error_and_returns_promptly(tmp_path):
    check = CommandCheck(
        name="slow",
        argv=[sys.executable, "-c", "import time; time.sleep(10)"],
        timeout=1,
    )
    start = time.monotonic()
    ev = run_command(check, tmp_path)
    assert time.monotonic() - start < 4
    assert ev.status is Status.ERROR
    assert ev.scan_complete is False
    assert "timed out" in ev.detail
    assert ev.rule_id == "command.timeout"


def test_timeout_terminates_descendant_processes(tmp_path):
    marker = tmp_path / "descendant-survived"
    child = f"import time, pathlib; time.sleep(1.25); pathlib.Path({str(marker)!r}).touch()"
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(30)"
    )
    ev = run_command(
        CommandCheck(name="tree", argv=[sys.executable, "-c", parent], timeout=1),
        tmp_path,
    )
    time.sleep(0.75)
    assert ev.status is Status.ERROR
    assert not marker.exists()


def test_invalid_programmatic_command_fails_closed(tmp_path):
    ev = run_command(CommandCheck(name="empty"), tmp_path)
    assert ev.status is Status.ERROR
    assert ev.scan_complete is False

    ev = run_command(CommandCheck(name="empty-arg", argv=[sys.executable, ""]), tmp_path)
    assert ev.status is Status.ERROR
    assert ev.scan_complete is False


def test_invalid_programmatic_environment_fails_closed(tmp_path):
    check = CommandCheck(name="env", argv=[sys.executable, "-c", "pass"], env={"CI": 1})
    ev = run_command(check, tmp_path)
    assert ev.status is Status.ERROR
    assert ev.scan_complete is False


def test_missing_executable_is_an_infrastructure_error(tmp_path):
    executable = "dunnit-command-that-does-not-exist.exe" if os.name == "nt" else "/no/such/dunnit"
    ev = run_command(CommandCheck(name="missing", argv=[executable]), tmp_path)
    assert ev.status is Status.ERROR
    assert ev.scan_complete is False


def test_programmatic_command_validation_covers_every_invalid_shape(tmp_path):
    invalid = [
        CommandCheck("both", run="echo ok", argv=["echo", "ok"]),
        CommandCheck("run-type", run=1),
        CommandCheck("run-empty", run=""),
        CommandCheck("run-nul", run="echo\x00ok"),
        CommandCheck("argv-container", argv=(sys.executable,)),
        CommandCheck("argv-empty", argv=[]),
        CommandCheck("argv-type", argv=[sys.executable, 1]),
        CommandCheck("timeout-bool", argv=[sys.executable], timeout=True),
        CommandCheck("timeout", argv=[sys.executable], timeout=0),
        CommandCheck("dir-type", argv=[sys.executable], dir=1),
        CommandCheck("dir", argv=[sys.executable], dir=""),
        CommandCheck("dir-nul", argv=[sys.executable], dir="bad\x00dir"),
        CommandCheck("env-container", argv=[sys.executable], env=[]),
        CommandCheck("env-empty-name", argv=[sys.executable], env={"": "1"}),
        CommandCheck("env-name", argv=[sys.executable], env={"BAD=KEY": "1"}),
        CommandCheck("env-name-nul", argv=[sys.executable], env={"BAD\x00KEY": "1"}),
        CommandCheck("env-nul", argv=[sys.executable], env={"CI": "bad\x00value"}),
    ]
    for check in invalid:
        evidence = run_command(check, tmp_path)
        assert evidence.status is Status.ERROR
        assert evidence.scan_complete is False


def test_output_helpers_cover_string_controls_empty_output_and_ring_overflow():
    assert _sanitize("ok\rline\x07") == "ok\nline"
    assert _detail_with_output("prefix", "") == "prefix"
    assert len(_detail_with_output("x" * 4_000, "tail")) == 4_000

    tail = _TailBuffer(4)
    tail.append(b"abcdef")
    assert tail.bytes() == b"cdef"


def test_output_drain_treats_a_pipe_close_race_as_expected():
    class ClosedPipe:
        def read(self, _size):
            raise ValueError("closed")

    tail = _TailBuffer(10)
    _drain_output(ClosedPipe(), tail)
    assert tail.bytes() == b""


def test_display_and_environment_helpers_cover_platform_independent_branches():
    assert _display_command(CommandCheck("missing")) == "<missing command>"
    assert _merge_environment(
        {"PATH": "inherited"},
        {"PATH": "declared", "CI": "1"},
        windows=False,
    ) == {"PATH": "declared", "CI": "1"}


def test_case_insensitive_environment_override_is_unambiguous_on_windows():
    merged = _merge_environment(
        {"Path": "inherited", "KEEP": "yes"},
        {"PATH": "declared"},
        windows=True,
    )
    assert merged == {"PATH": "declared", "KEEP": "yes"}


def test_child_holding_output_pipe_open_is_an_incomplete_command(tmp_path):
    marker = tmp_path / "background-descendant-survived"
    child = f"import time, pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).touch()"
    parent = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "raise SystemExit(0)"
    )
    evidence = run_command(
        CommandCheck("background-child", argv=[sys.executable, "-c", parent]),
        tmp_path,
    )
    assert evidence.status is Status.ERROR
    assert evidence.rule_id == "command.output-incomplete"
    assert evidence.scan_complete is False
    time.sleep(1.25)
    assert not marker.exists()


def test_command_directory_resolution_errors_fail_closed():
    evidence = run_command(CommandCheck("proof", argv=[sys.executable]), object())
    assert evidence.status is Status.ERROR
    assert evidence.rule_id == "command.invalid-directory"
    assert evidence.scan_complete is False


def test_posix_command_setup_is_used_without_changing_host_platform(monkeypatch, tmp_path):
    platform = SimpleNamespace(name="posix", environ=os.environ)
    monkeypatch.setattr(commands, "os", platform)

    evidence = run_command(
        CommandCheck("proof", argv=[sys.executable, "-c", "raise SystemExit(0)"]),
        tmp_path,
    )

    assert evidence.status is Status.PASS


def test_wait_failure_is_an_incomplete_execution_error(monkeypatch, tmp_path):
    process = Mock()
    process.stdout = io.BytesIO(b"partial output")
    process.returncode = None
    process.wait.side_effect = OSError("wait failed")
    terminate = Mock()
    monkeypatch.setattr(commands.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(commands, "_terminate_process_tree", terminate)

    evidence = run_command(CommandCheck("proof", argv=["proof"]), tmp_path)

    assert evidence.status is Status.ERROR
    assert evidence.rule_id == "command.execution-error"
    assert "wait failed" in evidence.detail
    assert "partial output" in evidence.detail
    assert evidence.scan_complete is False
    terminate.assert_called_once_with(process)


def test_stream_close_failure_does_not_hide_a_completed_command(monkeypatch, tmp_path):
    class CloseRace:
        def read(self, _size):
            return b""

        def close(self):
            raise OSError("already closed")

    process = Mock()
    process.stdout = CloseRace()
    process.returncode = 0
    process.wait.return_value = 0
    monkeypatch.setattr(commands.subprocess, "Popen", Mock(return_value=process))

    evidence = run_command(CommandCheck("proof", argv=["proof"]), tmp_path)

    assert evidence.status is Status.PASS


def test_timeout_with_a_stuck_reader_closes_the_pipe_without_reterminating(
    monkeypatch, tmp_path
):
    readers = []

    class CloseRace:
        def close(self):
            raise ValueError("already closed")

    class StuckReader:
        def __init__(self, **_kwargs):
            self.joins = []
            self.started = False
            readers.append(self)

        def start(self):
            self.started = True

        def join(self, timeout=None):
            self.joins.append(timeout)

        def is_alive(self):
            return True

    process = Mock()
    process.stdout = CloseRace()
    process.returncode = None
    process.wait.side_effect = subprocess.TimeoutExpired("proof", 1)
    terminate = Mock()
    monkeypatch.setattr(commands.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(commands.threading, "Thread", StuckReader)
    monkeypatch.setattr(commands, "_terminate_process_tree", terminate)

    evidence = run_command(CommandCheck("proof", argv=["proof"], timeout=1), tmp_path)

    assert evidence.status is Status.ERROR
    assert evidence.rule_id == "command.timeout"
    assert readers[0].started is True
    assert readers[0].joins
    terminate.assert_called_once_with(process)


def test_windows_process_tree_termination_falls_back_and_forces_kill(monkeypatch):
    platform = SimpleNamespace(name="nt")
    process = Mock(pid=123)
    process.poll.return_value = None
    process.terminate.side_effect = OSError("terminate failed")
    process.wait.side_effect = subprocess.TimeoutExpired("proof", 1)
    process.kill.side_effect = OSError("kill raced")
    monkeypatch.setattr(commands, "os", platform)
    monkeypatch.setattr(commands.subprocess, "run", Mock(side_effect=OSError("no taskkill")))

    _terminate_process_tree(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()


def test_windows_process_tree_termination_still_targets_descendants_after_root_exit(monkeypatch):
    platform = SimpleNamespace(name="nt")
    process = Mock(pid=123)
    process.poll.return_value = 0
    taskkill = Mock(return_value=SimpleNamespace(returncode=1))
    monkeypatch.setattr(commands, "os", platform)
    monkeypatch.setattr(commands.subprocess, "run", taskkill)

    _terminate_process_tree(process)

    taskkill.assert_called_once()
    assert taskkill.call_args.args[0] == ["taskkill.exe", "/PID", "123", "/T", "/F"]
    process.terminate.assert_not_called()


def test_windows_argv_resolves_pathext_command_shims(monkeypatch):
    monkeypatch.setattr(commands.shutil, "which", lambda executable, path: "C:/tools/npm.CMD")

    command = _resolved_argv(
        ["npm", "run", "test", "--silent"],
        {"Path": "C:/tools"},
        windows=True,
    )

    assert command == ["C:/tools/npm.CMD", "run", "test", "--silent"]
    assert _resolved_argv(["npm", "test"], {}, windows=False) == ["npm", "test"]


def test_posix_process_tree_termination_handles_fallback_races(monkeypatch):
    kill_group = Mock(side_effect=[OSError("term raced"), OSError("group gone")])
    platform = SimpleNamespace(name="posix", killpg=kill_group)
    process = Mock(pid=123)
    process.terminate.side_effect = ValueError("already exited")
    process.wait.side_effect = [
        subprocess.TimeoutExpired("proof", 0.5),
        subprocess.TimeoutExpired("proof", 1),
    ]
    process.kill.side_effect = OSError("already exited")
    monkeypatch.setattr(commands, "os", platform)

    _terminate_process_tree(process)

    assert kill_group.call_count == 2
    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2


def test_posix_process_tree_termination_escalates_a_live_group(monkeypatch):
    kill_group = Mock(side_effect=[None, None, OSError("kill group raced")])
    platform = SimpleNamespace(name="posix", killpg=kill_group)
    process = Mock(pid=123)
    process.wait.return_value = 0
    monkeypatch.setattr(commands, "os", platform)

    _terminate_process_tree(process)

    assert kill_group.call_count == 3
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
