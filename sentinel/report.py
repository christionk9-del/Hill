"""Report generation: executive HTML, Markdown, machine-readable JSON, and SARIF 2.1.0."""

from __future__ import annotations

import html
import json
import re
import time
from collections import Counter
from pathlib import Path

from . import __version__
from .models import SEVERITIES, Finding, sort_findings
from .redact import redact
from .scope import Scope

SEV_COLORS = {"critical": "#b42318", "high": "#d92d20", "medium": "#dc6803", "low": "#1570ef", "info": "#667085"}


def _split(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    active = sort_findings([f for f in findings if f.status != "false_positive"])
    dismissed = sort_findings([f for f in findings if f.status == "false_positive"])
    return active, dismissed


def _default_summary(active: list[Finding]) -> dict:
    counts = Counter(f.severity for f in active)
    worst = next((s for s in SEVERITIES if counts.get(s)), "low")
    return {
        "overall_risk": worst if worst != "info" else "low",
        "executive_summary": (
            f"Automated baseline scan identified {len(active)} potential issue(s): "
            + ", ".join(f"{counts[s]} {s}" for s in SEVERITIES if counts.get(s))
            + ". Results have not been reviewed by the AI analyst (offline mode)."
            if active
            else "No issues were identified by the automated baseline scan."
        ),
        "priority_actions": [f"{f.title} — {f.location}" for f in active[:5]],
    }


def _relativize(findings: list[Finding], scope: Scope) -> list[Finding]:
    """Show locations relative to the scope root they belong to (shorter, and what SARIF expects)."""
    from dataclasses import replace

    roots = sorted({str(p.parent if p.is_file() else p) for p in scope.paths}, key=len, reverse=True)
    out = []
    for f in findings:
        loc = f.location
        for r in roots:
            if loc.startswith(r + "/"):
                loc = loc[len(r) + 1 :]
                break
        out.append(replace(f, location=loc) if loc != f.location else f)
    return out


def build_report(scope: Scope, findings: list[Finding], summary: dict | None, meta: dict) -> dict:
    active, dismissed = _split(_relativize(findings, scope))
    summary = summary or _default_summary(active)
    return {
        "tool": "Sentinel",
        "version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engagement": {"client": scope.client_name, "id": scope.engagement_id, "authorized_by": scope.authorized_by},
        "scope": {"paths": [str(p) for p in scope.paths], "hosts": scope.hosts},
        "summary": summary,
        "counts": {s: sum(1 for f in active if f.severity == s) for s in SEVERITIES},
        "findings": [f.to_dict() for f in active],
        "false_positives": [f.to_dict() for f in dismissed],
        "meta": meta,
    }


def write_reports(report: dict, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"sentinel-{re.sub(r'[^A-Za-z0-9_-]+', '-', report['engagement']['id'])}"
    paths = {
        "json": out_dir / f"{stem}.json",
        "markdown": out_dir / f"{stem}.md",
        "html": out_dir / f"{stem}.html",
        "sarif": out_dir / f"{stem}.sarif",
    }
    paths["json"].write_text(redact(json.dumps(report, indent=2)))
    paths["markdown"].write_text(redact(to_markdown(report)))
    paths["html"].write_text(redact(to_html(report)))
    paths["sarif"].write_text(redact(json.dumps(to_sarif(report), indent=2)))
    return paths


# --- Markdown ------------------------------------------------------------------------------------


def to_markdown(r: dict) -> str:
    s = r["summary"]
    lines = [
        f"# Security Assessment — {r['engagement']['client']}",
        "",
        f"**Engagement:** {r['engagement']['id']}  ",
        f"**Authorized by:** {r['engagement']['authorized_by']}  ",
        f"**Generated:** {r['generated_at']} by Sentinel {r['version']}  ",
        f"**Overall risk:** {s['overall_risk'].upper()}",
        "",
        "## Executive summary",
        "",
        s["executive_summary"],
        "",
        "## Priority actions",
        "",
        *[f"{i}. {a}" for i, a in enumerate(s.get("priority_actions", []), 1)],
        "",
        "## Findings by severity",
        "",
        "| Severity | Count |",
        "|---|---|",
        *[f"| {sev.title()} | {r['counts'][sev]} |" for sev in SEVERITIES],
        "",
        "## Findings",
        "",
    ]
    for f in r["findings"]:
        lines += [
            f"### [{f['severity'].upper()}] {f['title']}",
            "",
            f"- **ID:** {f['id']}  ",
            f"- **Location:** `{f['location']}`",
            f"- **Category:** {f['category']}" + (f" · {f['cwe']}" if f["cwe"] else ""),
            f"- **Source:** {'AI analyst' if f['source'] == 'agent' else 'scanner'} · status: {f['status']}",
            "",
            f"**Evidence:** `{f['evidence']}`",
            "",
        ]
        if f["analyst_notes"]:
            lines += [f"**Analyst notes:** {f['analyst_notes']}", ""]
        lines += [f"**Recommendation:** {f['recommendation']}", ""]
    if r["false_positives"]:
        lines += ["## Dismissed as false positives", ""]
        lines += [f"- {f['id']} {f['title']} (`{f['location']}`) — {f['analyst_notes']}" for f in r["false_positives"]]
        lines.append("")
    m = r["meta"]
    lines += ["---", "", f"Mode: {m.get('mode')} · Model: {', '.join(m.get('models', [])) or 'n/a'} · Turns: {m.get('turns', 0)} · Est. AI cost: ${m.get('est_cost_usd', 0):.2f}"]
    return "\n".join(lines) + "\n"


# --- HTML ----------------------------------------------------------------------------------------


def to_html(r: dict) -> str:
    e = html.escape
    s = r["summary"]
    risk = s["overall_risk"]
    tiles = "".join(
        f'<div class="tile"><div class="n" style="color:{SEV_COLORS[sev]}">{r["counts"][sev]}</div><div class="l">{sev.title()}</div></div>' for sev in SEVERITIES
    )
    cards = []
    for f in r["findings"]:
        notes = f'<p><b>Analyst notes.</b> {e(f["analyst_notes"])}</p>' if f["analyst_notes"] else ""
        cards.append(
            f"""<details class="f" {'open' if f['severity'] in ('critical', 'high') else ''}>
<summary><span class="sev" style="background:{SEV_COLORS[f['severity']]}">{e(f['severity'].upper())}</span> {e(f['title'])}<span class="id">{e(f['id'])}</span></summary>
<p class="meta"><code>{e(f['location'])}</code> · {e(f['category'])}{' · ' + e(f['cwe']) if f['cwe'] else ''} · {'AI analyst' if f['source'] == 'agent' else 'scanner'} · {e(f['status'])}</p>
<p><b>Evidence.</b> <code>{e(f['evidence'])}</code></p>{notes}
<p><b>Fix.</b> {e(f['recommendation'])}</p></details>"""
        )
    fps = "".join(f"<li><code>{e(f['location'])}</code> — {e(f['title'])}: {e(f['analyst_notes'])}</li>" for f in r["false_positives"])
    actions = "".join(f"<li>{e(a)}</li>" for a in s.get("priority_actions", []))
    m = r["meta"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Security Assessment — {e(r['engagement']['client'])}</title>
<style>
:root{{--bg:#f8fafc;--card:#fff;--fg:#101828;--mut:#667085;--bd:#e4e7ec}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0c111d;--card:#161b26;--fg:#f5f5f6;--mut:#94969c;--bd:#2b303b}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:960px;margin:0 auto;padding:32px 16px}}h1{{margin:0 0 4px;font-size:26px}}h2{{margin-top:36px;font-size:18px}}
.sub{{color:var(--mut)}}.risk{{display:inline-block;margin-top:12px;padding:4px 12px;border-radius:999px;color:#fff;font-weight:600;background:{SEV_COLORS.get(risk, '#667085')}}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px 20px}}
.tiles{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.tile{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px;text-align:center}}
.tile .n{{font-size:28px;font-weight:700}}.tile .l{{color:var(--mut);font-size:13px}}
.f{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 16px;margin:10px 0}}.f summary{{cursor:pointer;font-weight:600}}
.sev{{color:#fff;border-radius:6px;padding:1px 8px;font-size:12px;margin-right:6px}}.id{{float:right;color:var(--mut);font-weight:400;font-size:12px}}
.meta{{color:var(--mut);font-size:13px}}code{{font-size:13px;word-break:break-all}}footer{{margin-top:40px;color:var(--mut);font-size:13px}}
@media (max-width:600px){{.tiles{{grid-template-columns:repeat(3,1fr)}}.id{{float:none;display:block}}}}
</style></head><body><main>
<h1>Security Assessment — {e(r['engagement']['client'])}</h1>
<div class="sub">Engagement {e(r['engagement']['id'])} · authorized by {e(r['engagement']['authorized_by'])} · {e(r['generated_at'])}</div>
<div class="risk">Overall risk: {e(risk.upper())}</div>
<h2>Executive summary</h2><div class="card"><p>{e(s['executive_summary'])}</p>{'<b>Priority actions</b><ol>' + actions + '</ol>' if actions else ''}</div>
<h2>Findings by severity</h2><div class="tiles">{tiles}</div>
<h2>Findings ({len(r['findings'])})</h2>{''.join(cards) or '<p>No findings.</p>'}
{'<h2>Dismissed as false positives</h2><div class="card"><ul>' + fps + '</ul></div>' if fps else ''}
<footer>Generated by Sentinel {e(r['version'])} · mode: {e(str(m.get('mode')))} · AI turns: {m.get('turns', 0)} · est. AI cost ${m.get('est_cost_usd', 0):.2f}. Secrets are redacted. Full action log in the audit trail.</footer>
</main></body></html>"""


# --- SARIF ---------------------------------------------------------------------------------------

_SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}


def to_sarif(r: dict) -> dict:
    rules, results = {}, []
    for f in r["findings"]:
        rule_id = f"{f['category']}/{re.sub(r'[^a-z0-9]+', '-', f['title'].lower()).strip('-')[:60]}"
        rules.setdefault(rule_id, {"id": rule_id, "name": f["title"][:100], "shortDescription": {"text": f["title"]}, "help": {"text": f["recommendation"]}, "properties": {"tags": [t for t in (f["category"], f["cwe"]) if t]}})
        loc = f["location"]
        m = re.match(r"^(.*?):(\d+)$", loc)
        uri, line = (m.group(1), int(m.group(2))) if m else (loc, None)
        region = {"region": {"startLine": line}} if line else {}
        results.append(
            {
                "ruleId": rule_id,
                "level": _SARIF_LEVEL[f["severity"]],
                "message": {"text": f"{f['evidence']}\n\n{f['analyst_notes']}".strip()},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}, **region}}],
                "properties": {"severity": f["severity"], "sentinelId": f["id"]},
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "Sentinel", "version": r["version"], "rules": list(rules.values())}}, "results": results}],
    }
