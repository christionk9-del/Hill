"""Engagement scope: the hard boundary of what the agent is authorized to touch.

Every tool call is checked against the scope *in code*, not by asking the model nicely.
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ScopeViolation(Exception):
    """Raised when a tool call targets something outside the authorized engagement scope."""


DEFAULT_EXCLUDES = [".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox"]


@dataclass
class Scope:
    client_name: str
    engagement_id: str
    authorized_by: str
    paths: list[Path]
    hosts: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    max_file_bytes: int = 2_000_000
    allow_network_lookups: bool = True  # e.g. querying the public OSV vulnerability database

    @classmethod
    def load(cls, path: str | Path) -> "Scope":
        path = Path(path)
        data = tomllib.loads(path.read_text())
        eng = data.get("engagement", {})
        tgt = data.get("targets", {})
        opts = data.get("options", {})
        missing = [k for k in ("client_name", "engagement_id", "authorized_by") if not eng.get(k)]
        if missing:
            raise ValueError(f"scope file {path} is missing required [engagement] keys: {', '.join(missing)}")
        base = path.parent
        paths = [(base / p).resolve() for p in tgt.get("paths", [])]
        if not paths and not tgt.get("hosts"):
            raise ValueError("scope must list at least one target path or host")
        for p in paths:
            if not p.exists():
                raise ValueError(f"scope path does not exist: {p}")
        return cls(
            client_name=eng["client_name"],
            engagement_id=eng["engagement_id"],
            authorized_by=eng["authorized_by"],
            paths=paths,
            hosts=[h.lower() for h in tgt.get("hosts", [])],
            exclude=opts.get("exclude", list(DEFAULT_EXCLUDES)),
            max_file_bytes=int(opts.get("max_file_bytes", 2_000_000)),
            allow_network_lookups=bool(opts.get("allow_network_lookups", True)),
        )

    # --- enforcement -------------------------------------------------------------------------

    def resolve_path(self, raw: str) -> Path:
        """Resolve a path and verify it is inside an authorized root. Blocks traversal and symlink escapes."""
        candidate = Path(raw)
        if not candidate.is_absolute():
            # Relative paths are interpreted against the first authorized root.
            if not self.paths:
                raise ScopeViolation("no filesystem paths are in scope")
            candidate = self.paths[0] / candidate
        resolved = candidate.resolve()
        for root in self.paths:
            if resolved == root or resolved.is_relative_to(root):
                if self.is_excluded(resolved):
                    raise ScopeViolation(f"{raw} is excluded by scope options")
                return resolved
        raise ScopeViolation(f"{raw} is outside the authorized scope")

    def is_excluded(self, path: Path) -> bool:
        return any(fnmatch.fnmatch(part, pat) for part in path.parts for pat in self.exclude)

    def check_host(self, host: str) -> str:
        host = host.lower().strip()
        for allowed in self.hosts:
            if host == allowed or (allowed.startswith("*.") and host.endswith(allowed[1:])):
                return host
        raise ScopeViolation(f"host {host!r} is not in the authorized scope")

    def iter_files(self, root: Path):
        """Yield in-scope regular files under root, skipping excludes, symlinks, and oversized files."""
        if root.is_file():
            if not root.is_symlink() and root.stat().st_size <= self.max_file_bytes:
                yield root
            return
        for p in sorted(root.rglob("*")):
            if p.is_symlink() or not p.is_file() or self.is_excluded(p.relative_to(root)):
                continue
            if p.stat().st_size > self.max_file_bytes:
                continue
            yield p

    def describe(self) -> str:
        lines = [
            f"Client: {self.client_name}",
            f"Engagement ID: {self.engagement_id}",
            f"Authorized by: {self.authorized_by}",
            "Authorized paths:",
            *[f"  - {p}" for p in self.paths],
            "Authorized hosts:",
            *([f"  - {h}" for h in self.hosts] or ["  (none)"]),
        ]
        return "\n".join(lines)
