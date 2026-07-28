"""Verdict and evidence models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class Evidence:
    check: str
    status: Status
    detail: str = ""

    def to_dict(self) -> dict:
        return {"check": self.check, "status": self.status.value, "detail": self.detail}


@dataclass
class Verdict:
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(e.status is not Status.FAIL for e in self.evidence)

    def add(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)

    def to_dict(self) -> dict:
        return {
            "tool": "dunnit",
            "verdict": "pass" if self.passed else "fail",
            "evidence": [e.to_dict() for e in self.evidence],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
