# Selling Sentinel

## The pitch (one paragraph)

> Sentinel is an AI security analyst that runs inside your environment. It scans your code, infrastructure configs, logs and public endpoints, confirms what matters, throws out the false positives, and gives you a prioritized report your executives and engineers can both act on in minutes instead of weeks. It is read-only, stays inside the scope you approve, redacts every secret, and logs every action it takes for your auditors.

## Who buys it

| Buyer | Pain | What to show them |
|---|---|---|
| SMB / mid-market with no security team (50–500 staff) | Can't afford a full-time analyst or a $20k+ pentest every quarter | The executive summary and priority actions in the HTML report |
| MSPs / MSSPs | Need to deliver security reviews to many clients at low cost per client | Scope-per-client, audit trail, white-label HTML report |
| Engineering teams preparing for SOC 2 / ISO 27001 | Need evidence of regular vulnerability assessment | The audit log, SARIF in GitHub, `--fail-on high` in CI |
| Consultants / vCISOs | Want to speed up assessments | AI triage and correlation that turns 300 scanner hits into 15 real issues |

## Packaging and pricing ideas (validate with real customers)

- **Starter:** scheduled offline scans + report, flat monthly fee per environment.
- **Pro:** AI analyst runs (weekly + on every PR), SARIF/CI integration, per-repo or per-environment pricing.
- **Assessment-as-a-service:** a one-off AI-assisted assessment that you review and deliver, priced per engagement.
- Your cost per AI run is printed in every report footer (`est. AI cost`), so you can set margins with real numbers. Run the demo with `--effort medium` vs `high` to compare cost against quality.

## Live demo script (10 minutes)

1. Show `examples/demo-scope.toml`: "nothing outside this file can be touched."
2. Run `sentinel scan --scope examples/demo-scope.toml --offline`, which finishes in seconds and gives 28 raw findings.
3. Run it again without `--offline`. The analyst triages, spots the SQL injection in `app.py` that the access log shows was exploited (2xx responses), connects the brute force to the successful `deploy` login and the `wget` it ran via sudo, and flags the planted "ignore previous instructions" comment as tampering.
4. Open the HTML report and show the executive summary, then the false-positives appendix.
5. Open the audit `.jsonl` and show every action, including a blocked out-of-scope attempt if one happened.

## Before your first paying customer (checklist)

- [ ] **Contracts:** an MSA plus a rules-of-engagement / authorization template the customer signs, naming targets. Include a limitation-of-liability clause and make clear Sentinel supplements, not replaces, professional penetration testing.
- [ ] **Data handling:** tell customers which data reaches the Anthropic API (redacted excerpts only) and review Anthropic's commercial terms and data-retention options for your account. Offer `--offline` for customers who can't send any data out.
- [ ] **Insurance:** professional liability / E&O plus cyber liability coverage.
- [ ] **Licensing:** choose a commercial license for your code (this repo currently has none, so all rights are reserved by default).
- [ ] **Branding:** the name "Sentinel" is widely used by other security products (e.g. Microsoft Sentinel). Pick and trademark-check your own product name before you market it.
- [ ] **Validation:** run it on a few friendly companies' real environments, measure the false-positive rate, and tune the scanners.
- [ ] **Roadmap items customers will ask for:** cloud posture checks (AWS/Azure/GCP IAM, public buckets), scheduled runs + email/Slack delivery, a web dashboard, ticket creation (Jira/Linear), and trend reports over time.
