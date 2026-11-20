"""Command-line entry point: `sentinel scan --scope scope.toml`."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .audit import AuditLog
from .models import SEVERITIES, meets_threshold
from .report import build_report, write_reports
from .scope import Scope
from .tools import Session, run_all_scanners

COLORS = {"status": "\033[36m", "tool": "\033[90m", "analyst": "\033[0m", "warning": "\033[33m"}


def _printer(quiet: bool):
    tty = sys.stderr.isatty()

    def emit(kind: str, text: str) -> None:
        if quiet and kind in ("tool", "analyst"):
            return
        prefix = {"status": "==>", "tool": "  ->", "analyst": "  analyst:", "warning": "  !"}[kind]
        c, r = (COLORS[kind], "\033[0m") if tty else ("", "")
        print(f"{c}{prefix} {text}{r}", file=sys.stderr)

    return emit


def cmd_scan(args: argparse.Namespace) -> int:
    scope = Scope.load(args.scope)
    out_dir = Path(args.out)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    audit = AuditLog(out_dir / f"audit-{scope.engagement_id}-{stamp}.jsonl", scope.engagement_id)
    audit.write("run_start", version=__version__, mode="offline" if args.offline else "agent", scope=scope.describe())
    session = Session(scope=scope, audit=audit)
    emit = _printer(args.quiet)
    emit("status", f"Sentinel {__version__} — engagement {scope.engagement_id} for {scope.client_name}")

    if args.offline:
        emit("status", "Offline mode: deterministic scanners only, no data leaves this machine except OSV dependency lookups.")
        run_all_scanners(session)
        meta = {"mode": "offline", "turns": 0, "est_cost_usd": 0.0, "models": [], "scan_notes": session.scan_notes}
    else:
        from .agent import run_agent  # imported lazily so offline mode works without the SDK

        stats = run_agent(session, model=args.model, effort=args.effort, max_turns=args.max_turns, on_event=emit)
        meta = {
            "mode": "agent",
            "turns": stats.turns,
            "stopped_reason": stats.stopped_reason,
            "models": sorted(stats.models_used or []),
            "tokens": {"input": stats.input_tokens, "output": stats.output_tokens, "cache_read": stats.cache_read_tokens, "cache_write": stats.cache_write_tokens},
            "est_cost_usd": round(stats.est_cost_usd, 4),
            "scan_notes": session.scan_notes,
        }

    report = build_report(scope, list(session.findings.values()), session.summary, meta)
    paths = write_reports(report, out_dir)
    audit.write("reports_written", paths={k: str(v) for k, v in paths.items()})

    counts = report["counts"]
    emit("status", "Findings: " + ", ".join(f"{counts[s]} {s}" for s in SEVERITIES) + f" (overall risk: {report['summary']['overall_risk']})")
    for kind, p in paths.items():
        emit("status", f"{kind:>8}: {p}")
    emit("status", f"   audit: {audit.path}")

    if args.fail_on != "none" and any(meets_threshold(f["severity"], args.fail_on) for f in report["findings"]):
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sentinel", description="AI security analyst for authorized, read-only assessments.")
    p.add_argument("--version", action="version", version=f"sentinel {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="Run an assessment against the targets in a scope file.")
    s.add_argument("--scope", required=True, help="Path to the engagement scope TOML file.")
    s.add_argument("--out", default="reports", help="Output directory for reports and the audit log.")
    s.add_argument("--offline", action="store_true", help="Run deterministic scanners only (no AI, no API key needed).")
    s.add_argument("--model", default="claude-opus-5-5")
    s.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--max-turns", type=int, default=60)
    s.add_argument("--fail-on", default="none", choices=["none", *SEVERITIES], help="Exit 2 if any finding is at or above this severity (for CI).")
    s.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)
    try:
        return cmd_scan(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
