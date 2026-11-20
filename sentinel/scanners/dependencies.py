"""Known-vulnerable dependency detection via the public OSV.dev database (https://osv.dev)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from ..models import Finding
from ..scope import Scope

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{}"


def parse_manifests(scope: Scope, root: Path) -> list[tuple[str, str, str, str]]:
    """Return (ecosystem, name, version, manifest_path) for pinned dependencies."""
    deps: list[tuple[str, str, str, str]] = []
    for path in scope.iter_files(root):
        if path.name == "requirements.txt" or (path.name.startswith("requirements") and path.suffix == ".txt"):
            for line in path.read_text(errors="ignore").splitlines():
                m = re.match(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9_.\-+]+)", line)
                if m:
                    deps.append(("PyPI", m.group(1), m.group(2), str(path)))
        elif path.name == "package.json":
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            for section in ("dependencies", "devDependencies"):
                for name, spec in (data.get(section) or {}).items():
                    m = re.match(r"^[\^~=v]?(\d+\.\d+\.\d+[A-Za-z0-9.\-+]*)$", str(spec).strip())
                    if m:
                        deps.append(("npm", name, m.group(1), str(path)))
        elif path.name == "go.mod":
            for line in path.read_text(errors="ignore").splitlines():
                m = re.match(r"^\s*(?:require\s+)?([\w.\-/]+\.[\w.\-/]+)\s+v([\w.\-+]+)", line)
                if m:
                    deps.append(("Go", m.group(1), m.group(2), str(path)))
    return deps


def _post_json(url: str, payload: dict, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get_json(url: str, timeout: float = 20) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _severity_from_osv(vuln: dict) -> str:
    sev = (vuln.get("database_specific") or {}).get("severity", "")
    mapping = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium", "LOW": "low"}
    return mapping.get(str(sev).upper(), "high")


def audit_dependencies(scope: Scope, root: Path) -> tuple[list[Finding], str]:
    """Returns (findings, status_note)."""
    deps = parse_manifests(scope, root)
    if not deps:
        return [], "No pinned dependencies found in requirements*.txt, package.json, or go.mod."
    if not scope.allow_network_lookups:
        listing = ", ".join(f"{e}:{n}@{v}" for e, n, v, _ in deps[:50])
        return [], f"Network lookups disabled by scope; {len(deps)} dependencies found but not checked: {listing}"

    queries = [{"package": {"ecosystem": e, "name": n}, "version": v} for e, n, v, _ in deps]
    try:
        results = _post_json(OSV_BATCH_URL, {"queries": queries}).get("results", [])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], f"Could not reach OSV.dev ({exc}); {len(deps)} dependencies were not checked."

    findings: list[Finding] = []
    detail_cache: dict[str, dict] = {}
    for (eco, name, version, manifest), result in zip(deps, results):
        for v in result.get("vulns", []) or []:
            vid = v["id"]
            if vid not in detail_cache:
                try:
                    detail_cache[vid] = _get_json(OSV_VULN_URL.format(vid))
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    detail_cache[vid] = {"id": vid}
            d = detail_cache[vid]
            aliases = ", ".join(a for a in d.get("aliases", []) if a.startswith("CVE-"))
            fixed = sorted(
                {
                    ev["fixed"]
                    for aff in d.get("affected", [])
                    for rng in aff.get("ranges", [])
                    for ev in rng.get("events", [])
                    if "fixed" in ev
                }
            )
            findings.append(
                Finding(
                    title=f"Vulnerable dependency {name}@{version} ({vid})",
                    severity=_severity_from_osv(d),
                    category="dependencies",
                    location=manifest,
                    evidence=f"{eco} package {name} {version}: {d.get('summary', 'see advisory')}" + (f" [{aliases}]" if aliases else ""),
                    recommendation=(f"Upgrade {name} to a fixed version ({', '.join(fixed[:3])})." if fixed else f"Review advisory {vid} and upgrade or replace {name}.")
                    + f" Advisory: https://osv.dev/vulnerability/{vid}",
                    cwe="CWE-1395",
                )
            )
    return findings, f"Checked {len(deps)} dependencies against OSV.dev."
