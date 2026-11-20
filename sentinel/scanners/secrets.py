"""Hardcoded credential detection."""

from __future__ import annotations

from pathlib import Path

from ..models import Finding
from ..redact import SECRET_PATTERNS, is_placeholder, redact
from ..scope import Scope

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar", ".so", ".dll", ".exe", ".bin", ".pyc", ".woff", ".woff2"}
SENSITIVE_FILENAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc", "credentials", ".htpasswd"}


def scan_secrets(scope: Scope, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in scope.iter_files(root):
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        if path.name in SENSITIVE_FILENAMES:
            findings.append(
                Finding(
                    title=f"Sensitive file present: {path.name}",
                    severity="medium",
                    category="secrets",
                    location=str(path),
                    evidence=f"File {path.name} commonly contains credentials and should not be stored in source trees.",
                    recommendation="Remove the file from the repository, add it to .gitignore, and rotate any credentials it contained.",
                    cwe="CWE-538",
                )
            )
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, pattern, severity in SECRET_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                value = m.group(1) if m.groups() and m.group(1) else m.group(0)
                if is_placeholder(value):
                    continue
                findings.append(
                    Finding(
                        title=f"Hardcoded credential: {name}",
                        severity=severity,
                        category="secrets",
                        location=f"{path}:{lineno}",
                        evidence=redact(line.strip())[:300],
                        recommendation=(
                            "Revoke and rotate this credential immediately, purge it from version-control history, "
                            "and load it at runtime from a secrets manager (e.g. Vault, AWS Secrets Manager) or environment."
                        ),
                        cwe="CWE-798",
                    )
                )
                break  # one finding per line is enough
    return findings
