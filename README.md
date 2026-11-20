# Sentinel: AI Security Analyst Agent

Sentinel is an AI security analyst that runs inside a company's own environment. It assesses the code, configs, logs and hosts the company has authorized, and produces a prioritized report that both executives and engineers can act on.

It works in two layers:

1. **Deterministic scanners** cover hardcoded secrets, vulnerable dependencies (OSV.dev), SSH and web attack patterns in logs, misconfigured sshd, Docker, Compose and nginx files, and TLS posture. They are fast, repeatable, free to run, and don't need an AI model.
2. **An AI analyst** (Claude, through the Anthropic API) works on top of the scanner results. It triages every candidate and dismisses false positives such as test fixtures and placeholders. It also reads the code to find what scanners miss (SQL or command injection, debug mode, auth gaps). It links related evidence across sources, for example *brute force in `auth.log` + `PasswordAuthentication yes` + a successful login from the same IP = probable compromise*. Then it writes an executive summary.

```
scope.toml ──► baseline scanners ──► AI analyst (triage · deep review · correlation) ──► HTML / MD / JSON / SARIF
                     │                        │ every tool call scope-checked, redacted, audited
                     └────────────── audit-*.jsonl (append-only action log) ◄──────────┘
```

## Quick start

```bash
pip install -e .

# 1) No-AI demo, no API key needed
sentinel scan --scope examples/demo-scope.toml --offline --out reports

# 2) Full AI-analyst run
export ANTHROPIC_API_KEY=sk-ant-...
sentinel scan --scope examples/demo-scope.toml --out reports
```

Open `reports/sentinel-DEMO-2026-001.html`. The demo target (`examples/demo-target/`) is a fictional, deliberately vulnerable company. It includes a planted prompt-injection comment in `app.py` that the analyst is instructed to flag rather than obey.

### CLI options

| Flag | Purpose |
|---|---|
| `--scope FILE` | Engagement scope (required). See `examples/demo-scope.toml`. |
| `--offline` | Scanners only. Nothing is sent to an AI model. |
| `--fail-on high` | Exit code 2 if any finding is at or above this severity, for CI gates. |
| `--model`, `--effort` | Default is `claude-opus-5-5` at `high` effort. Use `--effort medium` for cheaper routine scans. |
| `--max-turns N` | Hard cap on agent iterations (default 60). |

Docker: `docker build -t sentinel . && docker run --rm -v "$PWD:/work:ro" -v "$PWD/reports:/work/reports" -e ANTHROPIC_API_KEY sentinel scan --scope scope.toml`

CI: see `deploy/github-action.yml`. Findings show up in GitHub's Security tab via SARIF.

## Safety and trust model (what buyers will ask about)

| Control | How it's enforced |
|---|---|
| **Authorized scope only** | A scope file naming the client, engagement ID and approver is required. Every path is resolved and checked in code, which blocks `../` traversal and symlink escapes. Hosts need an exact or wildcard match. Out-of-scope calls are refused and logged as `scope_violation_blocked`. |
| **Read-only** | No tool writes, deletes or executes anything on the assessed systems. TLS checks are a single standard handshake. |
| **Secrets never leave in clear text** | Credentials are redacted before tool output goes to the model, and again before anything is written to reports or the audit log. Tests check this. |
| **Prompt-injection resistant** | File and log contents are wrapped as untrusted data. The analyst is told never to follow instructions found in them and to report them as findings instead. |
| **Full audit trail** | `audit-<engagement>-<time>.jsonl` records every tool call, every result size, every model turn and its request ID, and any blocked action. |
| **Defensive only** | The analyst recommends fixes and never writes exploits or attack instructions. |
| **Data control** | `--offline` mode sends nothing to an AI provider. In AI mode, only redacted excerpts go to the Anthropic API. |

## Extending

- **New scanner:** add a module in `sentinel/scanners/` that returns `list[Finding]`, then expose it in `sentinel/tools.py` (schema + `_tool_<name>` method).
- **New report format:** add a function in `sentinel/report.py`.
- **Tests:** `pip install -e '.[dev]' && pytest`. These include a scripted fake Claude client, so the agent loop is tested without an API key.

## Layout

```
sentinel/
  cli.py          command-line entry point
  agent.py        Claude tool-use loop, system prompt, cost tracking
  tools.py        tool schemas + scope-checked, audited dispatcher
  scope.py        engagement scope loading and enforcement
  redact.py       credential patterns and masking
  audit.py        append-only JSONL audit log
  report.py       HTML / Markdown / JSON / SARIF output
  scanners/       secrets, dependencies, logs, configs, tls
examples/         demo scope + deliberately vulnerable demo target
deploy/           GitHub Actions example
docs/SELLING.md   go-to-market notes, pricing ideas, pre-sale checklist
```

## Legal

Use Sentinel only on systems you own or have **written authorization** to assess. Keep a signed statement of work or rules of engagement for every scope file.
