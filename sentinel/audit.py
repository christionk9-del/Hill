"""Append-only audit trail of every action the agent takes (for the customer's compliance team)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .redact import redact


class AuditLog:
    def __init__(self, path: Path, engagement_id: str):
        self.path = path
        self.engagement_id = engagement_id
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **data: Any) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "engagement_id": self.engagement_id,
            "event": event,
            **data,
        }
        line = redact(json.dumps(record, default=str, sort_keys=True))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
