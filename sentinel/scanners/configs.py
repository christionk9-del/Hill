"""Misconfiguration checks for common infrastructure files."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from ..models import Finding
from ..scope import Scope


def _f(title, severity, path, lineno, evidence, rec, cwe) -> Finding:
    loc = f"{path}:{lineno}" if lineno else str(path)
    return Finding(title=title, severity=severity, category="configuration", location=loc, evidence=evidence, recommendation=rec, cwe=cwe)


def check_configs(scope: Scope, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in scope.iter_files(root):
        name = path.name.lower()
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        if name in {"sshd_config"} or name.endswith(".sshd_config"):
            findings += _sshd(path, lines)
        if name == "dockerfile" or name.endswith(".dockerfile"):
            findings += _dockerfile(path, lines)
        if name.endswith(".conf") and ("server {" in text or "http {" in text):
            findings += _nginx(path, lines)
        if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            findings += _compose(path, lines)
        if os.name == "posix":
            mode = path.stat().st_mode
            if mode & stat.S_IWOTH:
                findings.append(
                    _f("World-writable file", "medium", path, 0, f"mode {oct(mode & 0o777)}", "Remove world-write permission (chmod o-w).", "CWE-732")
                )
    return findings


def _sshd(path, lines):
    out = []
    rules = [
        (r"^\s*PermitRootLogin\s+yes\b", "SSH permits root login", "high", "Set 'PermitRootLogin no' (or 'prohibit-password').", "CWE-250"),
        (r"^\s*PasswordAuthentication\s+yes\b", "SSH password authentication enabled", "medium", "Set 'PasswordAuthentication no' and use key-based auth or SSO certificates.", "CWE-307"),
        (r"^\s*PermitEmptyPasswords\s+yes\b", "SSH permits empty passwords", "critical", "Set 'PermitEmptyPasswords no'.", "CWE-258"),
        (r"^\s*Protocol\s+1\b", "SSH protocol 1 enabled", "critical", "Remove 'Protocol 1'; only SSHv2 is acceptable.", "CWE-327"),
        (r"^\s*X11Forwarding\s+yes\b", "SSH X11 forwarding enabled", "low", "Set 'X11Forwarding no' unless explicitly required.", "CWE-16"),
    ]
    for i, line in enumerate(lines, 1):
        for pat, title, sev, rec, cwe in rules:
            if re.search(pat, line, re.I):
                out.append(_f(title, sev, path, i, line.strip(), rec, cwe))
    return out


def _dockerfile(path, lines):
    out = []
    has_user = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if re.match(r"(?i)^FROM\s+\S+:latest\b", s) or re.match(r"(?i)^FROM\s+[^\s:@]+(\s|$)", s):
            out.append(_f("Unpinned base image", "low", path, i, s, "Pin base images to a specific version tag or digest for reproducible, auditable builds.", "CWE-1357"))
        if re.match(r"(?i)^USER\s+", s):
            has_user = not re.match(r"(?i)^USER\s+(root|0)\b", s)
        if re.match(r"(?i)^ADD\s+https?://", s):
            out.append(_f("Remote ADD in Dockerfile", "medium", path, i, s, "Use RUN curl with checksum verification, or COPY a vetted artifact.", "CWE-494"))
        if re.match(r"(?i)^(ENV|ARG)\s+\S*(PASSWORD|SECRET|TOKEN|API_KEY)\S*", s):
            out.append(_f("Secret baked into image", "high", path, i, s.split("=")[0] + "=…", "Use build secrets (--secret) or runtime injection; never ENV/ARG for secrets.", "CWE-798"))
        if re.search(r"curl[^|]*\|\s*(ba)?sh", s):
            out.append(_f("Pipe-to-shell install", "medium", path, i, s[:200], "Download, verify a checksum/signature, then execute.", "CWE-494"))
    if lines and not has_user:
        out.append(_f("Container runs as root", "medium", path, 0, "No non-root USER instruction found.", "Add a non-root USER instruction before the entrypoint.", "CWE-250"))
    return out


def _nginx(path, lines):
    out = []
    rules = [
        (r"^\s*server_tokens\s+on\s*;", "nginx discloses version (server_tokens on)", "low", "Set 'server_tokens off;'.", "CWE-200"),
        (r"^\s*autoindex\s+on\s*;", "nginx directory listing enabled", "medium", "Set 'autoindex off;'.", "CWE-548"),
        (r"^\s*ssl_protocols\s+[^;]*(SSLv2|SSLv3|TLSv1(\.0)?\s|TLSv1\.1)", "Deprecated TLS protocol enabled", "high", "Use 'ssl_protocols TLSv1.2 TLSv1.3;'.", "CWE-327"),
        (r"^\s*ssl_ciphers\s+[^;]*(RC4|DES|MD5|NULL|EXPORT)", "Weak TLS cipher suites", "high", "Use a modern cipher list (e.g. Mozilla 'intermediate').", "CWE-327"),
    ]
    text = "\n".join(lines)
    for i, line in enumerate(lines, 1):
        for pat, title, sev, rec, cwe in rules:
            if re.search(pat, line + " ", re.I):
                out.append(_f(title, sev, path, i, line.strip(), rec, cwe))
    if "listen 443" in text and "Strict-Transport-Security" not in text:
        out.append(_f("HSTS header missing", "low", path, 0, "TLS server block without Strict-Transport-Security.", "add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;", "CWE-319"))
    return out


def _compose(path, lines):
    out = []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if re.match(r"^privileged:\s*true", s):
            out.append(_f("Privileged container", "high", path, i, s, "Remove 'privileged: true'; grant only specific capabilities.", "CWE-250"))
        if "/var/run/docker.sock" in s:
            out.append(_f("Docker socket mounted into container", "high", path, i, s, "Avoid mounting the Docker socket; it grants root on the host.", "CWE-250"))
        if re.match(r"^-?\s*[\"']?0\.0\.0\.0:\d+:(5432|3306|6379|27017|9200)", s) or re.match(r"^-\s*[\"']?(5432|3306|6379|27017|9200):\1", s):
            out.append(_f("Database port exposed on all interfaces", "medium", path, i, s, "Bind database ports to 127.0.0.1 or an internal network only.", "CWE-668"))
    return out
