#!/usr/bin/env python3
"""Freeze independently authored and adjudicated benchmark labels.

This command consumes two JSONL files that never contain Dunnit execution
output. Author records contain ``author_id``, ``authored_at``, and ``case`` (a
complete manifest record). Adjudication records use one of these forms::

    {"case_id": "python-skip-001", "adjudicator_id": "reviewer-b",
     "adjudicated_at": "2026-08-06T12:00:00Z", "decision": "agree"}

    {"case_id": "python-skip-002", "adjudicator_id": "reviewer-b",
     "adjudicated_at": "2026-08-06T12:01:00Z", "decision": "resolved",
     "final_case": {...}, "resolution": "Joint review corrected the rule path.",
     "resolver_id": "review-panel-1"}

The author and adjudicator IDs must differ. Resolved records must provide a
complete final case and an auditable resolution. The output is canonical JSONL
plus a SHA-256 sidecar and is created exclusively so an existing frozen
manifest cannot be overwritten accidentally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from aggregate import BenchmarkValidationError, Case, _parse_case
from run import fixture_digest

PROTOCOL = "dunnit-benchmark-v1"
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AdjudicationError(ValueError):
    """Authorship or adjudication data cannot safely produce a manifest."""


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdjudicationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise AdjudicationError(f"non-finite JSON number {value!r}")


def _read_jsonl(path: Path, *, kind: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AdjudicationError(f"cannot read {kind} {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise AdjudicationError(f"{kind} line {line_number} is blank")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_duplicate_safe_object,
                parse_constant=_reject_nonfinite,
            )
        except (ValueError, AdjudicationError) as exc:
            raise AdjudicationError(f"{kind} line {line_number} is not JSON: {exc}") from exc
        if type(value) is not dict:
            raise AdjudicationError(f"{kind} line {line_number} must be an object")
        records.append(value)
    if not records:
        raise AdjudicationError(f"{kind} contains no records")
    return records


def _exact_keys(
    value: dict[str, Any], *, required: set[str], optional: set[str], where: str
) -> None:
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise AdjudicationError(f"{where} is missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise AdjudicationError(f"{where} has unknown keys: {', '.join(sorted(extra))}")


def _text(value: Any, *, where: str) -> str:
    if type(value) is not str or not value.strip():
        raise AdjudicationError(f"{where} must be a non-empty string")
    return value


def _timestamp(value: Any, *, where: str) -> str:
    rendered = _text(value, where=where)
    if not _TIMESTAMP.fullmatch(rendered):
        raise AdjudicationError(f"{where} must be an RFC 3339 UTC timestamp ending in Z")
    return rendered


def _validated_case(value: Any, *, where: str) -> tuple[dict[str, Any], Case]:
    if type(value) is not dict:
        raise AdjudicationError(f"{where} must be a complete manifest object")
    try:
        parsed = _parse_case(value, line_number=1)
    except BenchmarkValidationError as exc:
        raise AdjudicationError(f"{where}: {exc}") from exc
    return value, parsed


def finalize(
    author_path: Path,
    adjudication_path: Path,
    *,
    fixtures_root: Path,
) -> tuple[bytes, dict[str, Any]]:
    """Return canonical manifest bytes and an audit summary."""

    authors: dict[str, tuple[str, dict[str, Any], Case]] = {}
    for line_number, record in enumerate(_read_jsonl(author_path, kind="author labels"), 1):
        where = f"author labels line {line_number}"
        _exact_keys(
            record,
            required={"author_id", "authored_at", "case"},
            optional=set(),
            where=where,
        )
        author_id = _text(record["author_id"], where=f"{where}.author_id")
        _timestamp(record["authored_at"], where=f"{where}.authored_at")
        case_record, case = _validated_case(record["case"], where=f"{where}.case")
        if case.case_id in authors:
            raise AdjudicationError(f"duplicate authored case {case.case_id!r}")
        authors[case.case_id] = (author_id, case_record, case)

    adjudications: dict[str, dict[str, Any]] = {}
    for line_number, record in enumerate(
        _read_jsonl(adjudication_path, kind="adjudications"), 1
    ):
        where = f"adjudications line {line_number}"
        _exact_keys(
            record,
            required={"case_id", "adjudicator_id", "adjudicated_at", "decision"},
            optional={"final_case", "resolution", "resolver_id"},
            where=where,
        )
        case_id = _text(record["case_id"], where=f"{where}.case_id")
        _text(record["adjudicator_id"], where=f"{where}.adjudicator_id")
        _timestamp(record["adjudicated_at"], where=f"{where}.adjudicated_at")
        if case_id in adjudications:
            raise AdjudicationError(f"duplicate adjudication for case {case_id!r}")
        adjudications[case_id] = record

    missing = authors.keys() - adjudications.keys()
    extra = adjudications.keys() - authors.keys()
    if missing:
        raise AdjudicationError("cases missing adjudication: " + ", ".join(sorted(missing)))
    if extra:
        raise AdjudicationError("adjudications reference unknown cases: " + ", ".join(sorted(extra)))

    final_records: list[dict[str, Any]] = []
    resolved = 0
    author_ids: set[str] = set()
    adjudicator_ids: set[str] = set()
    for case_id in sorted(authors):
        author_id, authored_record, authored_case = authors[case_id]
        review = adjudications[case_id]
        adjudicator_id = review["adjudicator_id"]
        if author_id == adjudicator_id:
            raise AdjudicationError(f"{case_id}: author and adjudicator must be different people")
        author_ids.add(author_id)
        adjudicator_ids.add(adjudicator_id)

        decision = review["decision"]
        if decision == "agree":
            forbidden = {"final_case", "resolution", "resolver_id"} & review.keys()
            if forbidden:
                raise AdjudicationError(
                    f"{case_id}: agree decision cannot include {', '.join(sorted(forbidden))}"
                )
            final_record = authored_record
            final_case = authored_case
        elif decision == "resolved":
            for key in ("final_case", "resolution", "resolver_id"):
                if key not in review:
                    raise AdjudicationError(f"{case_id}: resolved decision requires {key}")
            _text(review["resolution"], where=f"{case_id}.resolution")
            _text(review["resolver_id"], where=f"{case_id}.resolver_id")
            final_record, final_case = _validated_case(
                review["final_case"], where=f"{case_id}.final_case"
            )
            if final_case.case_id != authored_case.case_id:
                raise AdjudicationError(f"{case_id}: resolution cannot change the case ID")
            if final_case.fixture != authored_case.fixture:
                raise AdjudicationError(f"{case_id}: resolution cannot replace the fixture")
            resolved += 1
        else:
            raise AdjudicationError(f"{case_id}: decision must be 'agree' or 'resolved'")

        relative = Path(final_case.fixture).relative_to("fixtures")
        fixture = fixtures_root / relative
        if not fixture.is_dir():
            raise AdjudicationError(f"{case_id}: fixture directory {fixture} is missing")
        actual_digest = fixture_digest(fixture)
        if actual_digest != final_case.fixture_sha256:
            raise AdjudicationError(
                f"{case_id}: fixture digest {actual_digest} does not match "
                f"{final_case.fixture_sha256}"
            )
        final_records.append(final_record)

    manifest = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in final_records
    ).encode("utf-8")
    summary = {
        "protocol": PROTOCOL,
        "case_count": len(final_records),
        "resolved_disagreement_count": resolved,
        "distinct_author_count": len(author_ids),
        "distinct_adjudicator_count": len(adjudicator_ids),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
    }
    return manifest, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("author_labels", type=Path)
    parser.add_argument("adjudications", type=Path)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest, summary = finalize(
            args.author_labels,
            args.adjudications,
            fixtures_root=args.fixtures,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(manifest)
        try:
            with args.audit_output.open("x", encoding="utf-8") as stream:
                json.dump(summary, stream, indent=2, sort_keys=True)
                stream.write("\n")
        except Exception:
            args.output.unlink()
            raise
    except (AdjudicationError, OSError) as exc:
        print(f"benchmark adjudication refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
