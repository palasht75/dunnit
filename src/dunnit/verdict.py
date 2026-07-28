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
    hint: str = ""  # what the agent should do instead of gaming the check

    def to_dict(self) -> dict:
        d = {"check": self.check, "status": self.status.value, "detail": self.detail}
        if self.hint:
            d["hint"] = self.hint
        return d


@dataclass
class Verdict:
    evidence: list[Evidence] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # base ref, files changed, ...

    @property
    def passed(self) -> bool:
        return all(e.status is not Status.FAIL for e in self.evidence)

    @property
    def counts(self) -> dict[str, int]:
        c = {"pass": 0, "fail": 0, "warn": 0}
        for e in self.evidence:
            c[e.status.value] += 1
        return c

    def add(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)

    def to_dict(self) -> dict:
        from dunnit import __version__  # deferred: dunnit/__init__ imports this module

        d = {
            "tool": "dunnit",
            "version": __version__,
            "verdict": "pass" if self.passed else "fail",
            "summary": self.counts,
            "evidence": [e.to_dict() for e in self.evidence],
        }
        if self.meta:
            d["meta"] = self.meta
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
