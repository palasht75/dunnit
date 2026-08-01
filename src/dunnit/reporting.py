"""Render versioned Dunnit verdicts for humans and CI consumers."""

from __future__ import annotations

import contextlib
import html
import math
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from dunnit.verdict import Outcome, Status, Verdict

_XML_FORBIDDEN = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


def render_github(verdict: Verdict) -> str:
    """Return GitHub workflow annotations and a compact console result."""

    lines: list[str] = []
    for evidence in verdict.evidence:
        if evidence.status not in {Status.WARN, Status.FAIL, Status.ERROR}:
            continue
        level = "warning" if evidence.status is Status.WARN else "error"
        properties = [f"title={_github_property(evidence.rule_id or evidence.check)}"]
        if evidence.path:
            properties.append(f"file={_github_property(evidence.path)}")
        if type(evidence.line) is int and evidence.line > 0:
            properties.append(f"line={evidence.line}")
        detail = evidence.detail
        if evidence.hint:
            detail += f" Fix: {evidence.hint}"
        lines.append(f"::{level} {','.join(properties)}::{_github_data(detail)}")
    lines.append(f"Dunnit outcome: {verdict.outcome.value}")
    return "\n".join(lines) + "\n"


def write_github_summary(verdict: Verdict, destination: str | os.PathLike[str] | None) -> None:
    """Append a Markdown job summary when GitHub provides a summary path."""

    if not destination:
        return
    counts = verdict.counts
    raw_policy = verdict.meta.get("policy", {})
    raw_git = verdict.meta.get("git", {})
    policy = raw_policy if isinstance(raw_policy, dict) else {}
    git = raw_git if isinstance(raw_git, dict) else {}
    body = [
        "## Dunnit verification",
        "",
        f"**Outcome:** `{verdict.outcome.value}`",
        "",
        "| Passed | Warnings | Failed | Errors | Scan complete |",
        "|---:|---:|---:|---:|:---:|",
        (
            f"| {counts['pass']} | {counts['warn']} | {counts['fail']} | "
            f"{counts['error']} | {'yes' if verdict.scan_complete else 'no'} |"
        ),
    ]
    if policy:
        body.extend(
            [
                "",
                f"Policy: {_markdown_code(policy.get('origin', 'unknown'))}  ",
                f"Digest: {_markdown_code(policy.get('digest', 'unknown'))}",
            ]
        )
    if git.get("baseline_sha"):
        body.extend(["", f"Baseline: {_markdown_code(git['baseline_sha'])}"])
    findings = [item for item in verdict.evidence if item.status is not Status.PASS]
    if findings:
        body.extend(["", "### Findings", ""])
        for item in findings:
            detail = _safe_text(item.detail).splitlines()[0] if item.detail else ""
            body.append(
                f"- **{item.status.value}** {_markdown_code(item.rule_id or item.check)}: "
                f"{_markdown_code(detail)}"
            )
    body.append("")
    path = Path(destination)
    with path.open("a", encoding="utf-8", errors="replace", newline="\n") as stream:
        stream.write("\n".join(body) + "\n")


def render_junit(verdict: Verdict) -> str:
    """Render a dependency-free JUnit report with failures and errors distinct."""

    counts = verdict.counts
    suite = ET.Element(
        "testsuite",
        {
            "name": "dunnit",
            "tests": str(len(verdict.evidence)),
            "failures": str(counts["fail"]),
            "errors": str(counts["error"]),
            "skipped": "0",
            "time": f"{_duration(verdict.meta.get('duration')):.6f}",
        },
    )
    properties = ET.SubElement(suite, "properties")
    ET.SubElement(properties, "property", {"name": "outcome", "value": verdict.outcome.value})
    ET.SubElement(
        properties,
        "property",
        {"name": "scan_complete", "value": str(verdict.scan_complete).lower()},
    )
    for evidence in verdict.evidence:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "dunnit",
                "name": _xml(evidence.rule_id or evidence.check),
                "time": f"{_duration(evidence.duration):.6f}",
            },
        )
        message = evidence.detail
        if evidence.hint:
            message += f"\nFix: {evidence.hint}"
        if evidence.status is Status.FAIL:
            ET.SubElement(case, "failure", {"message": _xml(evidence.detail)}).text = _xml(message)
        elif evidence.status is Status.ERROR:
            ET.SubElement(case, "error", {"message": _xml(evidence.detail)}).text = _xml(message)
        elif evidence.status is Status.WARN:
            ET.SubElement(case, "system-out").text = _xml("WARNING: " + message)
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def write_report(path: str | os.PathLike[str], verdict: Verdict) -> None:
    """Atomically write the canonical JSON report."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=destination.name + ".tmp-",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(verdict.to_json() + "\n")
        assert temporary is not None
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()


def outcome_exit_code(outcome: Outcome) -> int:
    if outcome is Outcome.ERROR:
        return 2
    if outcome is Outcome.FAIL:
        return 1
    return 0


def _safe_text(value: object) -> str:
    text = str(value)
    return "".join(
        char
        if char in "\r\n"
        else " "
        if char == "\t"
        else "�"
        if unicodedata.category(char) in {"Cc", "Cf"}
        else char
        for char in text
    )


def _github_data(value: object) -> str:
    return _safe_text(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _github_property(value: object) -> str:
    return _github_data(value).replace(":", "%3A").replace(",", "%2C")


def _xml(value: object) -> str:
    return _XML_FORBIDDEN.sub("�", _safe_text(value))


def _markdown_code(value: object) -> str:
    value = _safe_text(value).replace("\r", " ").replace("\n", " ")
    escaped = html.escape(value, quote=True)
    markdown_entities = (("`", "&#96;"), ("[", "&#91;"), ("]", "&#93;"), ("#", "&#35;"))
    for character, entity in markdown_entities:
        escaped = escaped.replace(character, entity)
    return f"<code>{escaped}</code>"


def _duration(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.0
    duration = float(value)
    return duration if duration >= 0 and math.isfinite(duration) else 0.0
