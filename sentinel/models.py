"""Core data types shared by scanners, the agent, and the report generator."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}  # lower = worse


@dataclass
class Finding:
    title: str
    severity: str
    category: str
    location: str
    evidence: str
    recommendation: str
    cwe: str = ""
    source: str = "scanner"  # "scanner" (deterministic) or "agent" (triaged/added by the AI analyst)
    status: str = "open"  # open | confirmed | false_positive
    analyst_notes: str = ""
    id: str = field(default="")

    def __post_init__(self) -> None:
        self.severity = self.severity.lower()
        if self.severity not in SEVERITY_RANK:
            raise ValueError(f"invalid severity {self.severity!r}; expected one of {SEVERITIES}")
        if not self.id:
            digest = hashlib.sha256(f"{self.category}|{self.title}|{self.location}".encode()).hexdigest()
            self.id = f"SNT-{digest[:8].upper()}"

    def to_dict(self) -> dict:
        return asdict(self)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_RANK[f.severity], f.category, f.location))


def meets_threshold(severity: str, threshold: str) -> bool:
    """True if `severity` is at least as bad as `threshold`."""
    return SEVERITY_RANK[severity] <= SEVERITY_RANK[threshold]
