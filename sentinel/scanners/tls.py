"""TLS posture check for an in-scope host (passive: a single standard handshake, no attacks)."""

from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone

from ..models import Finding


def check_tls(host: str, port: int = 443, timeout: float = 8.0) -> tuple[list[Finding], dict]:
    loc = f"{host}:{port}"
    info: dict = {"host": host, "port": port}
    findings: list[Finding] = []
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock, ctx.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
            info["protocol"] = tls.version()
            info["cipher"] = tls.cipher()[0] if tls.cipher() else None
    except ssl.SSLCertVerificationError as exc:
        findings.append(
            Finding(
                title="TLS certificate failed verification",
                severity="high",
                category="tls",
                location=loc,
                evidence=str(exc.verify_message or exc),
                recommendation="Install a certificate from a trusted CA that matches the hostname and includes the full chain.",
                cwe="CWE-295",
            )
        )
        return findings, info
    except (OSError, ssl.SSLError) as exc:
        info["error"] = str(exc)
        return findings, info

    not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    days = (not_after - datetime.now(timezone.utc)).days
    info["expires"] = not_after.isoformat()
    info["days_remaining"] = days
    info["issuer"] = dict(x[0] for x in cert.get("issuer", ()))
    if days < 0:
        sev = "critical"
    elif days < 14:
        sev = "high"
    elif days < 30:
        sev = "medium"
    else:
        sev = None
    if sev:
        findings.append(
            Finding(
                title=f"TLS certificate expires in {days} days" if days >= 0 else "TLS certificate expired",
                severity=sev,
                category="tls",
                location=loc,
                evidence=f"notAfter={cert['notAfter']}",
                recommendation="Renew the certificate and automate renewal (e.g. ACME/cert-manager) with expiry alerting.",
                cwe="CWE-298",
            )
        )
    if info.get("protocol") in {"TLSv1", "TLSv1.1", "SSLv3"}:
        findings.append(
            Finding(
                title=f"Deprecated TLS protocol negotiated ({info['protocol']})",
                severity="high",
                category="tls",
                location=loc,
                evidence=f"Negotiated {info['protocol']} with a default modern client.",
                recommendation="Disable TLS 1.0/1.1; allow only TLS 1.2 and 1.3.",
                cwe="CWE-327",
            )
        )
    return findings, info
