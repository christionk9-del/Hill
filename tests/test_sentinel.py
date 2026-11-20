import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinel.agent import run_agent
from sentinel.audit import AuditLog
from sentinel.models import Finding, meets_threshold
from sentinel.redact import redact
from sentinel.report import build_report, to_sarif, write_reports
from sentinel.scanners import analyze_logs, check_configs, scan_secrets
from sentinel.scope import Scope, ScopeViolation
from sentinel.tools import Session

REPO = Path(__file__).resolve().parents[1]
DEMO_SCOPE = REPO / "examples" / "demo-scope.toml"

# Built at runtime so no real-looking token is committed to the repo.
FAKE_GH = "ghp_" + "A1b2C3d4E5" * 4
FAKE_STRIPE = "sk_live_" + "Z9y8X7w6V5u4T3s2R1q0"


@pytest.fixture
def target(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text(f'GITHUB_TOKEN = "{FAKE_GH}"\nSTRIPE = "{FAKE_STRIPE}"\nPASSWORD = "changeme"\n')
    (tmp_path / "auth.log").write_text(
        "".join(f"Nov 1 sshd[1]: Failed password for invalid user u{i} from 10.0.0.9 port 1 ssh2\n" for i in range(12))
        + "Nov 1 sshd[2]: Accepted password for bob from 10.0.0.9 port 2 ssh2\n"
    )
    (tmp_path / "sshd_config").write_text("PermitRootLogin yes\nPasswordAuthentication no\n")
    scope_file = tmp_path / "scope.toml"
    scope_file.write_text(
        '[engagement]\nclient_name="T"\nengagement_id="T-1"\nauthorized_by="me"\n'
        '[targets]\npaths=["src", "auth.log", "sshd_config"]\nhosts=["*.example.com", "api.test.io"]\n'
    )
    return tmp_path, Scope.load(scope_file)


def test_scope_requires_authorization_fields(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text('[engagement]\nclient_name="x"\n[targets]\npaths=["."]\n')
    with pytest.raises(ValueError, match="authorized_by"):
        Scope.load(f)


def test_scope_blocks_traversal_and_symlink_escape(target):
    root, scope = target
    with pytest.raises(ScopeViolation):
        scope.resolve_path(str(root / "src" / ".." / ".." / "etc" / "passwd"))
    with pytest.raises(ScopeViolation):
        scope.resolve_path("/etc/passwd")
    os.symlink("/etc", root / "src" / "escape")
    with pytest.raises(ScopeViolation):
        scope.resolve_path(str(root / "src" / "escape" / "passwd"))
    assert scope.resolve_path(str(root / "src" / "settings.py")).name == "settings.py"


def test_scope_hosts(target):
    _, scope = target
    assert scope.check_host("shop.example.com") == "shop.example.com"
    assert scope.check_host("API.test.io") == "api.test.io"
    for bad in ("example.com.evil.net", "evil.io", "test.io"):
        with pytest.raises(ScopeViolation):
            scope.check_host(bad)


def test_redaction_masks_secrets():
    out = redact(f"token={FAKE_GH} url=postgres://u:hunter22@db/x")
    assert FAKE_GH not in out and "hunter22" not in out
    assert "ghp_" in out  # prefix kept for identification


def test_secret_scanner(target):
    root, scope = target
    found = scan_secrets(scope, root / "src")
    titles = {f.title for f in found}
    assert "Hardcoded credential: GitHub token" in titles
    assert "Hardcoded credential: Stripe live secret key" in titles
    assert not any("changeme" in f.evidence for f in found), "placeholders must be ignored"
    assert all(FAKE_GH not in f.evidence for f in found), "evidence must be redacted"


def test_log_analysis_flags_compromise(target):
    root, scope = target
    found = analyze_logs(scope, root / "auth.log")
    sev = {f.title: f.severity for f in found}
    assert sev["Successful SSH login after brute force from 10.0.0.9"] == "critical"
    assert sev["SSH brute-force activity from 10.0.0.9"] == "medium"


def test_config_checks(target):
    root, scope = target
    titles = [f.title for f in check_configs(scope, root / "sshd_config")]
    assert titles == ["SSH permits root login"]


def test_demo_offline_end_to_end(tmp_path):
    scope = Scope.load(DEMO_SCOPE)
    session = Session(scope=scope, audit=AuditLog(tmp_path / "a.jsonl", scope.engagement_id))
    from sentinel.tools import run_all_scanners

    run_all_scanners(session)
    report = build_report(scope, list(session.findings.values()), None, {"mode": "offline"})
    assert report["counts"]["critical"] >= 3
    paths = write_reports(report, tmp_path)
    blob = "".join(p.read_text() for p in paths.values()) + (tmp_path / "a.jsonl").read_text()
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in blob, "secrets must never reach reports or the audit log"
    sarif = json.loads(paths["sarif"].read_text())
    assert sarif["version"] == "2.1.0" and sarif["runs"][0]["results"]


def test_threshold():
    assert meets_threshold("critical", "high") and not meets_threshold("low", "high")


def test_sarif_location_parsing():
    f = Finding(title="x", severity="high", category="code", location="/a/b.py:12", evidence="e", recommendation="r")
    r = build_report(Scope("c", "e", "a", [Path("/")]), [f], None, {})
    loc = to_sarif(r)["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "/a/b.py" and loc["region"]["startLine"] == 12


# --- agent loop with a scripted fake client ---------------------------------------------------


def _resp(content, stop):
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage, model="claude-opus-5-5", _request_id="req_x", stop_details=None)


def _tool(id_, name, inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    def __init__(self, script):
        self.script = script
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        step = self.script[len(self.calls) - 1]
        return step(kw) if callable(step) else step


def test_agent_loop_triages_blocks_out_of_scope_and_finishes(target):
    root, scope = target
    session = Session(scope=scope, audit=AuditLog(root / "audit.jsonl", scope.engagement_id))

    def turn1(kw):
        # Every candidate from the baseline is in the first message; triage them all, try an out-of-scope read.
        ids = [f.id for f in session.findings.values()]
        calls = [_tool(f"t{i}", "triage_finding", {"finding_id": fid, "status": "confirmed", "severity": "high", "analyst_notes": "ok"}) for i, fid in enumerate(ids)]
        calls.append(_tool("tx", "read_file", {"path": "/etc/passwd", "start_line": 1, "max_lines": 5}))
        return _resp(calls, "tool_use")

    def turn2(kw):
        results = kw["messages"][-1]["content"]
        assert len(results) >= 2 and all(r["type"] == "tool_result" for r in results), "results must be in one user message"
        blocked = [r for r in results if r["tool_use_id"] == "tx"][0]
        assert blocked["is_error"] and "BLOCKED" in blocked["content"]
        return _resp([_tool("f", "finish_assessment", {"overall_risk": "critical", "executive_summary": "s", "priority_actions": ["a"]})], "tool_use")

    client = FakeClient([turn1, turn2])
    stats = run_agent(session, client=client, max_turns=5)
    assert stats.stopped_reason == "completed" and stats.turns == 2
    assert session.summary["overall_risk"] == "critical"
    first = client.calls[0]
    assert first["fallbacks"] == "default" and first["thinking"] == {"type": "adaptive"}
    assert all(t["strict"] for t in first["tools"])
    assert "scope_violation_blocked" in (root / "audit.jsonl").read_text()


def test_agent_handles_refusal(target):
    root, scope = target
    session = Session(scope=scope, audit=AuditLog(root / "audit.jsonl", scope.engagement_id))
    stats = run_agent(session, client=FakeClient([_resp([], "refusal")]), max_turns=3)
    assert stats.stopped_reason == "refusal"
    assert session.findings, "scanner findings survive a refusal"
