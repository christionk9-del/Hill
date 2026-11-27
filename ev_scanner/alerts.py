"""Report rendering and alert delivery (console, Markdown file, ntfy push,
Discord / Slack webhooks, email)."""
from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

KIND_LABEL = {"+EV": "+EV", "boost": "Profit boost", "bonus_bet": "Bonus bet",
              "no_sweat": "No-sweat", "dfs": "DFS pick", "dfs_promo": "DFS promo",
              "kalshi": "Kalshi", "kalshi_arb": "Kalshi arb"}


def _local(iso, tz):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).strftime("%a %-I:%M%p")
    except Exception:
        return iso


def fmt_line(o, tz):
    odds = f"{o.odds_american:+d}" if o.odds_decimal > 1 and o.kind not in ("dfs",) else f"{o.odds_decimal:.2f}x"
    ev_label = "conversion" if o.kind == "bonus_bet" else "EV"
    s = (f"[{KIND_LABEL.get(o.kind, o.kind)}] {o.book}: {o.selection} {odds} "
         f"({o.market}) | {o.event} {_local(o.start, tz)} | {ev_label} {o.ev:+.1%} "
         f"| fair {o.fair_prob:.1%} via {o.sharp_source}")
    if o.notes:
        s += f" | {o.notes}"
    if o.stake_hint:
        s += f" | max ${o.stake_hint:g}"
    return s


def render_markdown(result, cfg, detected_promos=()):
    tz = cfg.timezone
    when = datetime.fromisoformat(result["generated_at"]).astimezone(ZoneInfo(tz))
    lines = [f"# EV scan — {when:%a %b %-d, %Y %-I:%M %p %Z}", ""]
    a = result["alerts"]
    lines.append(f"## Alerts (EV ≥ {cfg.ev_threshold:.0%}) — {len(a)}")
    lines += [f"- {fmt_line(o, tz)}" for o in a] or ["- None today."]
    lines += ["", "## Watchlist (below threshold)"]
    lines += [f"- {fmt_line(o, tz)}" for o in result["watchlist"]] or ["- Nothing above the report floor."]
    if detected_promos:
        lines += ["", "## Promos spotted in feeds (verify, then add to promos.json)"]
        lines += [f"- **{p['type']}** {p.get('book') or '?'}: {p['text']} ({p['source']})"
                  for p in detected_promos[:30]]
    if result["errors"]:
        lines += ["", "## Errors"] + [f"- {e}" for e in result["errors"]]
    lines += ["", f"_Offers priced: {result['offers_priced']} · Odds API credits left: "
              f"{result['api_remaining'] or 'n/a'}_"]
    return "\n".join(lines)


def _post(url, payload, headers=None):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=headers or {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def send_alerts(result, cfg, markdown, log=print):
    """Delivers to whichever channels have env vars set. Only sends when there
    are alerts, unless ALERT_ALWAYS=1."""
    alerts = result["alerts"]
    if not alerts and os.environ.get("ALERT_ALWAYS") != "1":
        log("[alert] no bets over threshold; nothing sent")
        return []
    tz = cfg.timezone
    head = f"{len(alerts)} bet(s) at {cfg.ev_threshold:.0%}+ EV" if alerts else "EV scan: no 20%+ bets today"
    body = "\n".join(fmt_line(o, tz) for o in alerts[:15]) or "Nothing over threshold."
    sent = []

    if topic := os.environ.get("NTFY_TOPIC"):
        server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
        try:
            _post(f"{server}/{topic}", body.encode(),
                  {"Title": head, "Priority": "high" if alerts else "default", "Tags": "moneybag"})
            sent.append("ntfy")
        except Exception as e:
            log(f"[alert] ntfy failed: {e}")

    if url := os.environ.get("DISCORD_WEBHOOK_URL"):
        try:
            _post(url, {"content": f"**{head}**\n```\n{body[:1800]}\n```"})
            sent.append("discord")
        except Exception as e:
            log(f"[alert] discord failed: {e}")

    if url := os.environ.get("SLACK_WEBHOOK_URL"):
        try:
            _post(url, {"text": f"*{head}*\n```{body[:2800]}```"})
            sent.append("slack")
        except Exception as e:
            log(f"[alert] slack failed: {e}")

    if os.environ.get("SMTP_HOST") and os.environ.get("ALERT_EMAIL_TO"):
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"] = head, os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
            msg["To"] = os.environ["ALERT_EMAIL_TO"]
            msg.set_content(markdown)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
            sent.append("email")
        except Exception as e:
            log(f"[alert] email failed: {e}")

    log(f"[alert] sent via: {', '.join(sent) or 'console only (no channels configured)'}")
    return sent


def write_reports(result, markdown, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.md").write_text(markdown)
    (out_dir / "latest.json").write_text(json.dumps({
        **result,
        "alerts": [o.to_dict() for o in result["alerts"]],
        "watchlist": [o.to_dict() for o in result["watchlist"]],
    }, indent=2))
