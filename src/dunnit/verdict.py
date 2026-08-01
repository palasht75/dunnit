"""Structured verdict and evidence models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    ERROR = "error"


class Outcome(str, Enum):
    """Overall verification outcome.

    ``PASS_WITH_WARNINGS`` is successful but deliberately distinct from a
    clean pass. ``ERROR`` means verification could not be completed and must
    never be interpreted as success.
    """

    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL = "fail"
    ERROR = "error"


_DEFAULT_SEVERITY = {
    Status.PASS: "info",
    Status.WARN: "warning",
    Status.FAIL: "error",
    Status.ERROR: "error",
}


@dataclass
class Evidence:
    # Keep the first four fields in their original order: integrations may
    # construct Evidence positionally.
    check: str
    status: Status
    detail: str = ""
    hint: str = ""  # what the agent should do instead of gaming the check

    # Additive structured fields for annotations, reports, and deduplication.
    rule_id: str | None = None
    path: str | None = None
    line: int | None = None
    fingerprint: str | None = None
    severity: str | None = None
    duration: float | None = None
    exit_code: int | None = None
    scan_complete: bool | None = None

    def __post_init__(self) -> None:
        # Runtime validation matters for the public Python API: a plain
        # ``"fail"`` value previously missed identity checks against
        # ``Status.FAIL`` and could make invalid evidence look successful.
        if type(self.status) is str:
            try:
                self.status = Status(self.status)
            except ValueError as exc:
                raise ValueError(f"unsupported evidence status: {self.status!r}") from exc
        elif not isinstance(self.status, Status):
            raise TypeError("evidence status must be a Status value")
        if self.scan_complete is not None and type(self.scan_complete) is not bool:
            raise TypeError("evidence scan_complete must be a boolean or None")

    @property
    def complete(self) -> bool:
        """Whether this evidence represents a completed verification step."""

        return (
            isinstance(self.status, Status)
            and self.status is not Status.ERROR
            and (self.scan_complete is None or self.scan_complete is True)
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "check": self.check,
            "status": self.status.value,
            "detail": self.detail,
            "rule_id": self.rule_id or self.check,
            "severity": self.severity or _DEFAULT_SEVERITY[self.status],
            "scan_complete": self.complete,
        }
        if self.hint:
            d["hint"] = self.hint
        if self.path is not None:
            d["path"] = self.path
        if self.line is not None:
            d["line"] = self.line
        if self.fingerprint is not None:
            d["fingerprint"] = self.fingerprint
        if self.duration is not None:
            d["duration"] = self.duration
        if self.exit_code is not None:
            d["exit_code"] = self.exit_code
        return d


@dataclass(init=False)
class Verdict:
    evidence: list[Evidence] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)  # refs, files changed, environment, ...
    _scan_complete: bool = field(default=True, repr=False)

    def __init__(
        self,
        evidence: list[Evidence] | None = None,
        meta: dict[str, Any] | None = None,
        scan_complete: bool = True,
    ) -> None:
        # Keep the original positional ``evidence, meta`` constructor shape;
        # scan_complete is an additive third argument.
        self.evidence = evidence if evidence is not None else []
        self.meta = meta if meta is not None else {}
        if type(scan_complete) is not bool:
            raise TypeError("scan_complete must be a boolean")
        self._scan_complete = scan_complete

    @property
    def scan_complete(self) -> bool:
        """Whether non-empty evidence covers every requested verification."""

        return (
            bool(self.evidence)
            and self._scan_complete is True
            and all(isinstance(item, Evidence) and item.complete for item in self.evidence)
        )

    @scan_complete.setter
    def scan_complete(self, value: bool) -> None:
        if type(value) is not bool:
            raise TypeError("scan_complete must be a boolean")
        self._scan_complete = value

    @property
    def verification_complete(self) -> bool:
        """Whether every requested verification step produced evidence."""

        return self.scan_complete

    @property
    def outcome(self) -> Outcome:
        # Empty evidence used to pass via ``all([])``. Treat it as an
        # infrastructure error because nothing was actually verified.
        if not self.evidence or not self.verification_complete:
            return Outcome.ERROR
        if any(item.status is Status.FAIL for item in self.evidence):
            return Outcome.FAIL
        if any(item.status is Status.WARN for item in self.evidence):
            return Outcome.PASS_WITH_WARNINGS
        return Outcome.PASS

    @property
    def passed(self) -> bool:
        return self.outcome in {Outcome.PASS, Outcome.PASS_WITH_WARNINGS}

    @property
    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in Status}
        for item in self.evidence:
            if isinstance(item, Evidence) and isinstance(item.status, Status):
                counts[item.status.value] += 1
            else:
                counts[Status.ERROR.value] += 1
        return counts

    def add(self, evidence: Evidence) -> None:
        if not isinstance(evidence, Evidence):
            raise TypeError("verdict evidence must be an Evidence value")
        self.evidence.append(evidence)
        if not evidence.complete:
            self._scan_complete = False

    def mark_incomplete(self) -> None:
        """Mark this verification incomplete without fabricating evidence."""

        self._scan_complete = False

    def to_dict(self) -> dict[str, Any]:
        from dunnit import __version__  # deferred: dunnit/__init__ imports this module

        outcome = self.outcome
        d: dict[str, Any] = {
            "schema_version": 1,
            "tool": "dunnit",
            "version": __version__,
            # Preserve the original binary field for existing consumers. New
            # consumers should use ``outcome`` for warning/error distinctions.
            "verdict": "pass" if self.passed else "fail",
            "outcome": outcome.value,
            "scan_complete": self.verification_complete,
            "summary": self.counts,
            "evidence": [item.to_dict() for item in self.evidence],
        }
        if self.meta:
            d["meta"] = self.meta
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
