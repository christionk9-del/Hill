"""Log analysis: SSH brute force / suspicious successful logins, and web attack patterns in access logs."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from ..models import Finding
from ..scope import Scope

SSH_FAIL = re.compile(r"Failed (?:password|publickey) for (?:invalid user )?(\S+) from (\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]+)")
SSH_OK = re.compile(r"Accepted (?:password|publickey|keyboard-interactive/pam) for (\S+) from (\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]+)")
SUDO = re.compile(r"sudo:\s+(\S+) : .*COMMAND=(.*)")
ACCESS = re.compile(r'^(\S+) \S+ \S+ \[[^\]]+\] "(\S+) (\S+) [^"]*" (\d{3})')

WEB_ATTACKS: list[tuple[str, re.Pattern[str], str]] = [
    ("SQL injection probe", re.compile(r"(?i)(union(\s|%20|\+)+select|('|%27)(\s|%20|\+)*or(\s|%20|\+)+'?1'?(\s|%20)*=|sleep\(\d+\)|information_schema)"), "CWE-89"),
    ("Path traversal probe", re.compile(r"(?i)(\.\./|%2e%2e%2f|%2e%2e/|\.\.%2f)"), "CWE-22"),
    ("Cross-site scripting probe", re.compile(r"(?i)(<script|%3cscript|javascript:|onerror=)"), "CWE-79"),
    ("Command injection probe", re.compile(r"(?i)(;|%3b|\|)(\s|%20)*(cat|wget|curl|bash|sh|nc)(\s|%20)"), "CWE-78"),
    ("Sensitive file probe", re.compile(r"(?i)/(\.env|\.git/config|wp-config\.php|etc/passwd|\.aws/credentials)"), "CWE-538"),
]

BRUTE_FORCE_THRESHOLD = 10


def analyze_logs(scope: Scope, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in scope.iter_files(root):
        if not (path.suffix in {".log", ""} or "log" in path.name.lower()):
            continue
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        findings += _ssh(path, lines)
        findings += _web(path, lines)
    return findings


def _ssh(path: Path, lines: list[str]) -> list[Finding]:
    fails: Counter[str] = Counter()
    users_by_ip: dict[str, set[str]] = defaultdict(set)
    success_after_fail: list[tuple[str, str, int]] = []
    root_logins: list[tuple[str, int]] = []
    for i, line in enumerate(lines, start=1):
        if m := SSH_FAIL.search(line):
            user, ip = m.groups()
            fails[ip] += 1
            users_by_ip[ip].add(user)
        elif m := SSH_OK.search(line):
            user, ip = m.groups()
            if fails[ip] >= BRUTE_FORCE_THRESHOLD:
                success_after_fail.append((ip, user, i))
            if user == "root":
                root_logins.append((ip, i))

    out: list[Finding] = []
    for ip, count in fails.most_common():
        if count < BRUTE_FORCE_THRESHOLD:
            break
        users = sorted(users_by_ip[ip])
        out.append(
            Finding(
                title=f"SSH brute-force activity from {ip}",
                severity="medium",
                category="logs",
                location=str(path),
                evidence=f"{count} failed SSH logins from {ip} targeting {len(users)} account(s): {', '.join(users[:8])}",
                recommendation="Block the source at the firewall, deploy fail2ban or equivalent rate limiting, and disable SSH password authentication.",
                cwe="CWE-307",
            )
        )
    for ip, user, lineno in success_after_fail:
        out.append(
            Finding(
                title=f"Successful SSH login after brute force from {ip}",
                severity="critical",
                category="logs",
                location=f"{path}:{lineno}",
                evidence=f"Account '{user}' logged in from {ip} after {fails[ip]} failed attempts from the same address.",
                recommendation=(
                    "Treat as a probable compromise: isolate the host, rotate credentials for this account, "
                    "review the session's commands and persistence mechanisms, and begin incident response."
                ),
                cwe="CWE-307",
            )
        )
    for ip, lineno in root_logins:
        out.append(
            Finding(
                title="Direct root SSH login",
                severity="high",
                category="logs",
                location=f"{path}:{lineno}",
                evidence=f"root logged in over SSH from {ip}.",
                recommendation="Set PermitRootLogin no and require named accounts with sudo for accountability.",
                cwe="CWE-250",
            )
        )
    return out


def _web(path: Path, lines: list[str]) -> list[Finding]:
    hits: dict[tuple[str, str], list[tuple[int, str, str]]] = defaultdict(list)
    for i, line in enumerate(lines, start=1):
        m = ACCESS.match(line)
        if not m:
            continue
        ip, _method, url, status = m.groups()
        for name, pattern, _cwe in WEB_ATTACKS:
            if pattern.search(url):
                hits[(name, ip)].append((i, url, status))
    out: list[Finding] = []
    cwe_by_name = {n: c for n, _p, c in WEB_ATTACKS}
    for (name, ip), events in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        succeeded = [e for e in events if e[2].startswith("2")]
        sev = "high" if succeeded else "low"
        sample = events[0]
        out.append(
            Finding(
                title=f"{name} from {ip}" + (" (server returned 2xx)" if succeeded else ""),
                severity=sev,
                category="logs",
                location=f"{path}:{sample[0]}",
                evidence=f"{len(events)} request(s), {len(succeeded)} with 2xx status. Example: {sample[1][:200]} -> {sample[2]}",
                recommendation=(
                    "Verify the endpoint is not vulnerable (2xx responses to attack payloads warrant urgent review), "
                    "apply WAF rules, and block or rate-limit the source."
                ),
                cwe=cwe_by_name[name],
            )
        )
    return out
