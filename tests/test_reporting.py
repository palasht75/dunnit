import json
import os
import subprocess
import xml.etree.ElementTree as ET

from dunnit.reporting import (
    outcome_exit_code,
    render_github,
    render_junit,
    write_github_summary,
    write_report,
)
from dunnit.runner import verify
from dunnit.verdict import Evidence, Outcome, Status, Verdict


def test_github_annotations_escape_commands_and_include_locations():
    verdict = Verdict(
        [
            Evidence(
                "tamper:test",
                Status.FAIL,
                "bad%value\nsecond line",
                hint="restore it",
                rule_id="tamper.test",
                path="tests/a,b.py",
                line=7,
            )
        ]
    )

    rendered = render_github(verdict)

    assert rendered.startswith("::error ")
    assert "file=tests/a%2Cb.py" in rendered
    assert "line=7" in rendered
    assert "bad%25value%0Asecond line" in rendered
    assert "Dunnit outcome: fail" in rendered


def test_github_renderer_skips_passes_and_renders_warning_without_location():
    verdict = Verdict(
        [
            Evidence("ok", Status.PASS, "complete"),
            Evidence("review", Status.WARN, "look again", rule_id="review.rule"),
        ]
    )
    rendered = render_github(verdict)
    assert "::notice" not in rendered
    assert "::warning title=review.rule::look again" in rendered


def test_github_renderer_rejects_line_and_terminal_control_injection():
    evidence = Evidence(
        "proof",
        Status.FAIL,
        "bad\x1b[31m value",
        path="tests/a\x1b[2J,b.py",
        line=1,
    )
    evidence.line = "1\n::error title=forged::owned"  # type: ignore[assignment]

    rendered = render_github(Verdict([evidence]))

    assert "line=" not in rendered
    assert "\x1b" not in rendered
    assert rendered.count("::error ") == 1
    assert "%2C" in rendered


def test_junit_distinguishes_failures_errors_and_warnings():
    verdict = Verdict(
        [
            Evidence("pass", Status.PASS, "ok"),
            Evidence("warn", Status.WARN, "review"),
            Evidence("fail", Status.FAIL, "broken\x01", hint="repair it"),
            Evidence("error", Status.ERROR, "incomplete", scan_complete=False),
        ],
        meta={"duration": 1.25},
    )

    root = ET.fromstring(render_junit(verdict))

    assert root.attrib["tests"] == "4"
    assert root.attrib["failures"] == "1"
    assert root.attrib["errors"] == "1"
    assert root.find(".//failure") is not None
    assert root.find(".//error") is not None
    assert root.find(".//system-out") is not None
    assert "\x01" not in ET.tostring(root, encoding="unicode")


def test_junit_invalid_programmatic_durations_remain_valid_xml():
    evidence = Evidence("pass", Status.PASS, duration=1.0)
    evidence.duration = float("nan")
    root = ET.fromstring(render_junit(Verdict([evidence], meta={"duration": "not-a-number"})))
    assert root.attrib["time"] == "0.000000"
    assert root.find("testcase").attrib["time"] == "0.000000"  # type: ignore[union-attr]


def test_json_report_and_github_summary_are_written(tmp_path):
    verdict = Verdict(
        [Evidence("proof", Status.PASS, "complete")],
        meta={
            "policy": {"origin": "git:abc:dod.yaml", "digest": "sha256:123"},
            "git": {"baseline_sha": "abc"},
        },
    )
    report = tmp_path / "reports" / "dunnit.json"
    summary = tmp_path / "summary.md"

    write_report(report, verdict)
    write_github_summary(verdict, summary)

    payload = json.loads(report.read_text())
    assert payload["schema_version"] == 1
    assert payload["outcome"] == "pass"
    body = summary.read_text()
    assert "Dunnit verification" in body
    assert "sha256:123" in body


def test_json_report_uses_an_unpredictable_atomic_temporary_file(tmp_path):
    report = tmp_path / "report.json"
    predictable = report.with_name(report.name + f".tmp-{os.getpid()}")
    predictable.mkdir()

    write_report(report, Verdict([Evidence("proof", Status.PASS)]))

    assert json.loads(report.read_text())["outcome"] == "pass"
    assert predictable.is_dir()


def test_github_summary_neutralizes_markdown_and_multiline_spoofing(tmp_path):
    verdict = Verdict(
        [Evidence("review", Status.WARN, "[click](https://example.invalid)\n## forged")],
        meta={"policy": {"origin": "`\n## forged", "digest": "<script>bad</script>"}},
    )
    summary = tmp_path / "summary.md"
    write_github_summary(verdict, summary)
    body = summary.read_text()

    assert body.count("## forged") == 0
    assert "[click](" not in body
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_json_report_omits_remote_telemetry_and_author_identity(repo, tmp_path):
    remote = "https://example.invalid/private/repository-with-secret-name.git"
    author_name = "Private Maintainer Identity"
    author_email = "private-maintainer@example.invalid"
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", author_name], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", author_email], cwd=repo, check=True)
    (repo / "dod.yaml").write_text(
        "version: 2\nprotected: [dod.yaml]\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "dod.yaml"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add private policy"], cwd=repo, check=True)

    report = tmp_path / "privacy-report.json"
    write_report(report, verify(cwd=repo))
    serialized = report.read_text(encoding="utf-8")
    payload = json.loads(serialized)

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key.casefold()
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    forbidden_keys = {
        "author",
        "email",
        "remote",
        "remote_url",
        "repository_remote",
        "telemetry",
        "user",
        "username",
    }
    assert forbidden_keys.isdisjoint(keys(payload))
    assert remote not in serialized
    assert author_name not in serialized
    assert author_email not in serialized


def test_github_summary_is_optional_and_handles_findings_without_metadata(tmp_path):
    verdict = Verdict([Evidence("review", Status.WARN, "")])
    write_github_summary(verdict, None)

    summary = tmp_path / "summary.md"
    write_github_summary(verdict, summary)
    body = summary.read_text()
    assert "### Findings" in body
    assert "Policy:" not in body
    assert "Baseline:" not in body


def test_outcome_exit_codes_are_stable():
    assert outcome_exit_code(Outcome.PASS) == 0
    assert outcome_exit_code(Outcome.PASS_WITH_WARNINGS) == 0
    assert outcome_exit_code(Outcome.FAIL) == 1
    assert outcome_exit_code(Outcome.ERROR) == 2
