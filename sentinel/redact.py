"""Secret redaction. Applied to everything before it leaves the machine or lands in a report."""

from __future__ import annotations

import re

# (name, pattern, severity). Patterns are deliberately conservative to limit false positives.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "critical"),
    ("AWS secret access key", re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"=:\s]([A-Za-z0-9/+=]{40})\b"), "critical"),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), "critical"),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("Stripe live secret key", re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"), "critical"),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "critical"),
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"), "high"),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "high"),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "critical"),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "medium"),
    ("Password in connection string", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s@/]{3,})@[^\s]+"), "high"),
    (
        "Hardcoded password/secret assignment",
        re.compile(
            r"(?i)\b\w*(?:password|passwd|pwd|secret|api_?key|access_?token|auth_?token)\w*\s*[:=]\s*['\"]([^'\"\s]{6,})['\"]"
        ),
        "high",
    ),
]

_PLACEHOLDER_VALUES = re.compile(r"(?i)^(?:changeme|example|placeholder|your[_-]?\w*|x{4,}|\*{3,}|<[^>]+>|\$\{[^}]+\}|test|dummy)$")


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "[REDACTED]"
    return f"{value[:4]}…[REDACTED {len(value)} chars]"


def redact(text: str) -> str:
    """Mask anything that looks like a credential. Keeps a short prefix so analysts can identify the key."""

    def _sub(match: re.Match[str]) -> str:
        whole = match.group(0)
        secret = match.group(1) if match.groups() and match.group(1) else whole
        return whole.replace(secret, _mask(secret))

    for _name, pattern, _sev in SECRET_PATTERNS:
        text = pattern.sub(_sub, text)
    return text


def is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_VALUES.match(value.strip()))
