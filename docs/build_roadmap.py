"""Builds docs/EdgeLab_Monetization_Roadmap.pdf  (python3 docs/build_roadmap.py)."""
import os

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

FD = "/usr/share/fonts/truetype/dejavu/"
pdfmetrics.registerFont(TTFont("Sans", FD + "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("Sans-Bold", FD + "DejaVuSans-Bold.ttf"))
_obl = FD + "DejaVuSans-Oblique.ttf"
pdfmetrics.registerFont(TTFont("Sans-Oblique", _obl if os.path.exists(_obl) else FD + "DejaVuSans.ttf"))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Oblique", boldItalic="Sans-Bold")

INK = colors.HexColor("#0b0b0b")
INK2 = colors.HexColor("#52514e")
MUTED = colors.HexColor("#7a7974")
ACCENT = colors.HexColor("#2a78d6")
ACCENT_D = colors.HexColor("#184f95")
GRID = colors.HexColor("#e1e0d9")
TINT = colors.HexColor("#f0efeb")
GOOD = colors.HexColor("#006300")
SERIES = [colors.HexColor(c) for c in ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#6250d6")]

ss = {
    "h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=20, leading=25, textColor=INK, spaceAfter=6, spaceBefore=4),
    "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=13.5, leading=18, textColor=INK, spaceBefore=12, spaceAfter=4),
    "h3": ParagraphStyle("h3", fontName="Sans-Bold", fontSize=10.5, leading=14, textColor=ACCENT_D, spaceBefore=8, spaceAfter=2),
    "body": ParagraphStyle("body", fontName="Sans", fontSize=9.6, leading=14.2, textColor=INK, spaceAfter=5, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="Sans", fontSize=8.2, leading=11.5, textColor=INK2),
    "bullet": ParagraphStyle("bullet", fontName="Sans", fontSize=9.6, leading=14, textColor=INK, leftIndent=14, bulletIndent=3, spaceAfter=2),
    "cell": ParagraphStyle("cell", fontName="Sans", fontSize=8.4, leading=11.2, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Sans-Bold", fontSize=8.4, leading=11.2, textColor=INK),
    "head": ParagraphStyle("head", fontName="Sans-Bold", fontSize=8.2, leading=11, textColor=INK2),
    "callout": ParagraphStyle("callout", fontName="Sans", fontSize=9.8, leading=14.5, textColor=INK),
    "kick": ParagraphStyle("kick", fontName="Sans-Bold", fontSize=8.5, leading=11, textColor=ACCENT, spaceAfter=2),
}


def P(t, s="body"):
    return Paragraph(t, ss[s])


def B(items):
    return [Paragraph(i, ss["bullet"], bulletText="•") for i in items]


def table(rows, widths, head=True, zebra=True):
    data = [[P(c, "head" if (head and r == 0) else "cell") if isinstance(c, str) else c for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, GRID),
          ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if head:
        st += [("LINEBELOW", (0, 0), (-1, 0), 1, INK2)]
    if zebra:
        for r in range(1 if head else 0, len(rows)):
            if r % 2 == 0:
                st.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f7f7f4")))
    t.setStyle(TableStyle(st))
    return t


def callout(html, title=None):
    inner = ([P(title, "kick")] if title else []) + [P(html, "callout")]
    t = Table([[inner]], colWidths=[6.9 * inch])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    return t


def timeline():
    W, H = 6.9 * inch, 1.55 * inch
    d = Drawing(W, H)
    phases = [("0", "Foundation", "Wk 0–2"), ("1", "Lead magnet", "Wk 2–8"), ("2", "Proof + beta", "Mo 2–4"),
              ("3", "Scale paid", "Mo 4–9"), ("4", "Data & B2B", "Mo 9–18")]
    n = len(phases)
    gap = 4
    w = (W - gap * (n - 1)) / n
    for i, (num, name, when) in enumerate(phases):
        x = i * (w + gap)
        d.add(Rect(x, 30, w, 52, fillColor=SERIES[i], strokeColor=None, rx=4, ry=4))
        d.add(String(x + 9, 62, f"PHASE {num}", fontName="Sans-Bold", fontSize=7.5, fillColor=colors.white))
        d.add(String(x + 9, 43, name, fontName="Sans-Bold", fontSize=10.5, fillColor=colors.white))
        d.add(String(x + 9, 14, when, fontName="Sans", fontSize=8.5, fillColor=INK2))
    d.add(Line(0, 96, W, 96, strokeColor=GRID, strokeWidth=1))
    labels = [("Test live", 0), ("1k emails", 1), ("1st paid", 2), ("$5k MRR", 3), ("Data revenue", 4)]
    for text, i in labels:
        x = i * (w + gap) + 9
        d.add(Rect(x - 3, 93, 6, 6, fillColor=INK, strokeColor=None))
        d.add(String(x + 7, 103, text, fontName="Sans", fontSize=7.6, fillColor=INK))
    return d


def funnel(stages):
    W, H = 6.9 * inch, 1.9 * inch
    d = Drawing(W, H)
    maxv = stages[0][1]
    rowh = H / len(stages)
    for i, (name, v, note) in enumerate(stages):
        y = H - (i + 1) * rowh + 4
        bw = max(6, (W - 3.5 * inch) * v / maxv)
        d.add(Rect(1.45 * inch, y, bw, rowh - 8, fillColor=ACCENT if i < len(stages) - 1 else GOOD, strokeColor=None, rx=3, ry=3))
        d.add(String(0, y + rowh / 2 - 7, name, fontName="Sans", fontSize=8.5, fillColor=INK))
        d.add(String(1.45 * inch + bw + 6, y + rowh / 2 - 7, f"{v:,.0f}  {note}", fontName="Sans", fontSize=8.2, fillColor=INK2))
    return d


def on_page(c, doc):
    c.saveState()
    if doc.page > 1:
        c.setFont("Sans", 7.5)
        c.setFillColor(MUTED)
        c.drawString(0.8 * inch, 0.5 * inch, "EdgeLab · Monetization Roadmap & Game Plan · v1.0 · November 2026")
        c.drawRightString(letter[0] - 0.8 * inch, 0.5 * inch, str(doc.page))
        c.setStrokeColor(GRID)
        c.line(0.8 * inch, letter[1] - 0.55 * inch, letter[0] - 0.8 * inch, letter[1] - 0.55 * inch)
    c.restoreState()


def cover(c, doc):
    c.saveState()
    c.setFillColor(colors.HexColor("#111110"))
    c.rect(0, 0, letter[0], letter[1], stroke=0, fill=1)
    c.setFillColor(ACCENT)
    c.rect(0.8 * inch, 7.35 * inch, 0.9 * inch, 0.07 * inch, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Sans-Bold", 38)
    c.drawString(0.8 * inch, 6.55 * inch, "EdgeLab")
    c.setFont("Sans", 20)
    c.setFillColor(colors.HexColor("#c3c2b7"))
    c.drawString(0.8 * inch, 6.05 * inch, "Monetization Roadmap & Game Plan")
    c.setFont("Sans", 11)
    c.setFillColor(colors.HexColor("#8f8e86"))
    lines = ["Turning an open, scientifically-tested +EV model for EVENT markets",
             "(politics, geopolitics, economics) on Kalshi, Polymarket and Manifold",
             "into a free lead magnet, an audience, and a recurring-revenue product."]
    for i, l in enumerate(lines):
        c.drawString(0.8 * inch, 5.45 * inch - i * 17, l)
    # mini equity-curve motif
    import math
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    p = c.beginPath()
    x0, y0 = 0.8 * inch, 2.2 * inch
    for i in range(0, 121):
        x = x0 + i * (6.9 * inch / 120)
        y = y0 + i * 0.75 + 14 * math.sin(i / 5.0) + 7 * math.sin(i / 1.7)
        p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
    c.drawPath(p, stroke=1, fill=0)
    c.setFont("Sans", 8.5)
    c.setFillColor(colors.HexColor("#8f8e86"))
    c.drawString(0.8 * inch, 1.0 * inch, "Version 1.0 · November 2026 · Internal planning document. Not financial, legal or tax advice.")
    c.restoreState()


def build(out):
    doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title="EdgeLab Monetization Roadmap & Game Plan", author="EdgeLab")
    s = [PageBreak()]

    # ---------------------------------------------------------------- 1. summary
    s += [P("1. Executive summary", "h1"),
          P("<b>What exists today.</b> EdgeLab is a working, zero-dependency Python package. It pulls free market data "
            "from Kalshi, Polymarket and Manifold for politics, geopolitics and economics questions, and builds its own "
            "fair-probability model: devig each venue, a cross-venue consensus, then walk-forward calibration. It also scores "
            "every venue for accuracy and annualises EV over each contract's lock-up period. It paper-trades every +EV opportunity and grades each strategy with pre-registered "
            "hypotheses, a negative control, closing-line value (CLV) and significance tests. It ships with a "
            "shareable HTML dashboard and a Claude skill."),
          P("<b>The thesis.</b> Paid +EV screens commonly charge $100–$300+ per month and are black boxes. A transparent, "
            "free, <i>provably tested</i> alternative is a strong lead magnet. Bettors trust what they can audit. "
            "The free version builds the audience. Convenience, alerts, data and community are what people pay for."),
          P("<b>Why events only.</b> (1) Prediction markets don't limit or ban winning accounts the way sportsbooks do, so an edge "
            "can actually be used. (2) Event markets are growing fast and have far fewer analytics tools than sports. (3) Cross-venue "
            "price gaps on elections and geopolitics are novel, shareable content. (4) One model covers politics, geopolitics, "
            "economics and more. Sports support stays in the code, switched off, as an optional add-on later."),
          callout("Give away the <b>model</b> (code, skill, calculator, demo dashboard). Sell the <b>service</b>: "
                  "hosted live scanning, instant alerts, maintained cross-venue mappings, historical data, and an "
                  "honest public track record. Credibility is the product. Never sell picks with guarantees.",
                  "THE ONE-SENTENCE STRATEGY"),
          Spacer(1, 8),
          P("Targets (illustrative, not forecasts)", "h3"),
          table([["Milestone", "Target", "By"],
                 ["Live forward test running, public ledger", "All 5 pre-registered event arms collecting", "Week 2"],
                 ["Email list from lead magnet", "1,000 subscribers", "Week 8"],
                 ["Statistically meaningful CLV verdict", "≥ 1,000 tracked event contracts", "Month 3"],
                 ["Paid beta (founding members)", "50 members", "Month 4"],
                 ["Recurring revenue", "$5,000 MRR", "Month 9"],
                 ["Data / API / white-label revenue line", "First B2B contract", "Month 12–18"]],
                [3.2 * inch, 2.4 * inch, 1.3 * inch]),
          ]

    # ---------------------------------------------------------------- 2. assets
    s += [P("2. What you are giving away (the lead magnet)", "h2"),
          table([["Asset", "What the user gets", "Why it converts"],
                 ["Claude skill <b>edgelab-ev</b>", "Ask Claude “Kalshi YES is 41¢, Polymarket is 46/48¢: is that +EV after fees?” and get fair price, EV, APR, Kelly and resolution-rule flags",
                  "Instant “aha” in 30 seconds on any election, geopolitics or macro contract"],
                 ["Demo dashboard", "45-day synthetic forward test with planted edges and ground truth",
                  "Shows the method working (and failing) honestly. Screenshot-able"],
                 ["EdgeLab code", "Live scanner, paper trader, stats, devig tournament",
                  "Power users self-host. They become evangelists and contributors"],
                 ["EV calculator CLI", "<font face='Sans'>edgelab ev --fair 0.46 0.56 --bet 0.41</font>", "Daily-use utility keeps you top of mind"],
                 ["“Lab Notes” newsletter", "Weekly public ledger: what passed, what failed", "Habit + trust. The paid upsell lives here"]],
                [1.55 * inch, 2.9 * inch, 2.45 * inch]),
          Spacer(1, 6),
          P("Gate the dashboard and skill download behind an email capture. Keep the GitHub repo public: open source is the trust engine, "
            "and the email list is the business."),
          ]

    # ---------------------------------------------------------------- 3. ladder
    s += [P("3. The value ladder", "h1"),
          P("Each rung removes friction or adds information the free tier can't. Pricing anchors against $100–$300+/mo screens "
            "while staying an impulse buy at the entry tier."),
          table([["Tier", "Price", "Includes", "Who buys"],
                 ["<b>Free</b>", "$0", "Skill, code, demo dashboard, calculator, weekly Lab Notes", "Everyone: the funnel top"],
                 ["<b>Founding member</b> (beta)", "$15/mo locked for life (first 100)", "Hosted live dashboard, daily email digest, private Discord", "Early believers; creates urgency + testimonials"],
                 ["<b>Lab Pro</b>", "$29/mo or $249/yr", "Real-time alerts (Discord/Telegram/email), maintained Kalshi↔Polymarket↔Manifold mappings with resolution-rule checks, more categories, position tracker, CLV report card",
                  "Prediction-market traders and politics/macro followers"],
                 ["<b>Lab Quant</b>", "$99/mo", "API + raw snapshot history, custom strategy hosting, calibration models per domain, CSV exports, priority venues",
                  "Data-driven bettors, prediction-market traders, builders"],
                 ["<b>Cohort course</b>", "$299–$499 one-time", "4-week “Build & test your own model”: devig, CLV, Kelly, experiment design", "Learners, creators, analysts"],
                 ["<b>B2B / data</b>", "$500–$2,000+/mo", "White-label dashboard for creators/tout-free communities, historical odds dataset licence, consulting",
                  "Media, Discord owners, researchers, funds"]],
                [1.3 * inch, 1.15 * inch, 2.75 * inch, 1.7 * inch]),
          P("Side revenue: affiliates", "h3"),
          P("Prediction-market referral programmes can pay per funded account. Treat them as secondary and regulated. "
            "Rules for promoting event contracts differ by jurisdiction, and ads must follow responsible-trading and age "
            "rules. Disclose every affiliate link. Never let an affiliate deal change what the model recommends."),
          ]
    scen = [("Conservative", 3000, 0.02, 29, 0.002, 99), ("Base", 8000, 0.03, 29, 0.004, 99), ("Upside", 20000, 0.04, 29, 0.006, 99)]
    rows = [["Scenario", "Email list", "Pro conv.", "Pro MRR", "Quant conv.", "Quant MRR", "Total MRR"]]
    for name, L, c1, p1, c2, p2 in scen:
        pro, q = L * c1 * p1, L * c2 * p2
        rows.append([name, f"{L:,}", f"{c1:.0%}", f"${pro:,.0f}", f"{c2:.1%}", f"${q:,.0f}", f"<b>${pro + q:,.0f}</b>"])
    s += [KeepTogether([P("Revenue scenarios at Month 12 (illustrative assumptions)", "h3"), table(rows, [1.15 * inch, 0.95 * inch, 0.8 * inch, 0.95 * inch, 0.9 * inch, 1.0 * inch, 1.15 * inch]),
          P("Excludes course, affiliate and B2B revenue. Free-to-paid conversion of 2–4% is a typical planning range for newsletter-led "
            "SaaS. Measure your own and replace these numbers by Month 4.", "small")])]

    # ---------------------------------------------------------------- 4. roadmap
    s += [P("4. Phased roadmap", "h1"), timeline(), Spacer(1, 4)]
    phases = [
        ("Phase 0 · Foundation (Weeks 0–2)", [
            "Run <font face='Sans'>python3 -m edgelab demo</font> and publish the demo dashboard as the hero visual.",
            "Enable Kalshi + Polymarket + Manifold (no keys needed). Run <font face='Sans'>edgelab match</font>, review the first 30 cross-venue pairs into mappings.json, then start <font face='Sans'>scan --every 900</font> on a $5–10/mo VPS (cron).",
            "Pre-register the 5 active event experiments as-is. Commit the hashes publicly (GitHub) so nobody can accuse you of cherry-picking.",
            "Landing page + email tool (ConvertKit/Beehiiv/Substack). Offer: “Free +EV model, Claude skill & dashboard.”",
            "Legal basics: entity, privacy policy, terms, “not betting advice” disclaimers, 21+ / responsible-gambling messaging."]),
        ("Phase 1 · Lead-magnet launch (Weeks 2–8)", [
            "Flagship content: “I built my own +EV model instead of paying for a screen. Here is the code.” Video + thread + blog.",
            "Weekly <b>Lab Notes</b>: the public ledger, CLV per arm, and one lesson (e.g. “why an 18% EV estimate was really 3%”).",
            "Distribution: X/Twitter politics, forecasting and prediction-market communities, YouTube, relevant subreddits (follow each sub's self-promo rules), Discord partnerships, GitHub trending.",
            "Build cross-venue mappings for the 30 biggest political/geopolitical questions. Post the Kalshi vs Polymarket gaps weekly. It's novel, shareable content.",
            "KPI gate to exit Phase 1: <b>1,000 emails</b>, ≥ 35% landing opt-in, ≥ 40% newsletter open rate."]),
        ("Phase 2 · Proof + paid beta (Months 2–4)", [
            "Accumulate ≥ 1,000 tracked event contracts across ≥ 200 mapped questions. Publish the first real verdicts, including the ones that fail. Failures build trust.",
            "Ship the hosted version: scheduled scans, a static dashboard per user (Cloudflare Pages/S3), and a daily digest email.",
            "Open <b>Founding Member</b> at $15/mo lifetime for the first 100. Collect testimonials and feature requests.",
            "Add alerts (Discord webhook / Telegram bot): the #1 reason people pay for screens is speed.",
            "KPI gate: 50 paying members, churn < 8%/mo, ≥ 1 strategy at PROMISING (CLV t > 3) on live data."]),
        ("Phase 3 · Scale paid tiers (Months 4–9)", [
            "Launch Lab Pro ($29) and Lab Quant ($99). Add categories (economics data releases, company events, weather, culture) and deeper Kalshi/Polymarket order-book data.",
            "Add venues: PredictIt, Betfair politics markets, Smarkets, Metaculus/forecast aggregators as signals. Add auto-matching for event markets with human review.",
            "Run the first cohort course. Record it and resell it evergreen.",
            "Start referral programmes only in jurisdictions where you are compliant. Optional later: switch the sports module back on as an add-on.",
            "KPI gate: $5k MRR, CAC < 1 month of ARPU, net revenue retention ≥ 95%."]),
        ("Phase 4 · Data & B2B (Months 9–18)", [
            "Productise the snapshot archive (every price, every scan) as a historical dataset. Licence it to researchers and creators.",
            "White-label dashboard for Discord/newsletter owners (revenue share or flat monthly).",
            "Public API with usage tiers. Consider a market-making or research partnership with prediction-market venues.",
            "Option value: an audited multi-year public track record is the asset that acquirers and partners pay for."]),
    ]
    for title, items in phases:
        s += [KeepTogether([P(title, "h3")] + B(items))]

    # ---------------------------------------------------------------- 5. funnel
    s += [P("5. Funnel & metrics", "h1"),
          P("Model the business as a funnel and instrument every step from day one. Example monthly flow at the Month-9 target:"),
          funnel([("Landing visits", 20000, "content + search + referrals"), ("Email sign-ups", 7000, "35% opt-in"),
                  ("Activated", 2800, "opened dashboard / used skill (40%)"), ("Trial / beta", 420, "15% of activated"),
                  ("Paid", 170, "≈ 40% trial-to-paid")]),
          Spacer(1, 6),
          table([["Metric", "Definition", "Healthy target"],
                 ["Opt-in rate", "Sign-ups ÷ landing visitors", "30–45% for a strong free tool"],
                 ["Activation", "% of sign-ups who run the demo, open the dashboard or use the skill within 7 days", "≥ 40%"],
                 ["Lab Notes open rate", "Opens ÷ delivered", "≥ 40%"],
                 ["Free → paid", "Paying ÷ list size", "2–4%"],
                 ["Monthly churn", "Cancels ÷ start-of-month payers", "< 6–8%"],
                 ["Model health", "Mean CLV and t-stat per live arm (published weekly)", "CLV > 0, t rising"],
                 ["Alert latency", "Price change → user notified", "< 60 s for Pro"]],
                [1.5 * inch, 3.6 * inch, 1.8 * inch]),
          ]

    # ---------------------------------------------------------------- 6. science as marketing
    s += [P("6. Science as the marketing engine", "h2"),
          P("Most of the betting-content market sells certainty. EdgeLab sells <b>verifiable process</b>. Make the method itself the content:"),
          ] + B([
              "<b>Pre-registration posts.</b> “Here is the hypothesis, the rules, and the hash. Check back in 8 weeks.”",
              "<b>Negative control.</b> Show the deliberately −EV arm losing. It proves the measuring stick works.",
              "<b>CLV before ROI.</b> Teach that ROI needs thousands of bets (≈ 10,000 at a 2% edge) while CLV converges about 10× faster. But CLV measured against the same venues you used to pick the bet can mislead, so pair it with the venue scorecard.",
              "<b>Winner's curse.</b> The demo's first event model claimed ~18% EV that was really ~3%. Show the fix (keep the bet venue in the consensus) and its effect. It's a rare, honest story.",
              "<b>Devig tournament.</b> Publish which method wins on your data. It's a recurring, data-driven post.",
              "<b>Event markets.</b> Weekly Kalshi vs Polymarket vs Manifold gaps on elections and geopolitics. Few others publish this systematically.",
              "<b>Post-mortems.</b> When an arm is REJECTED, write it up. Honest losses earn more trust than cherry-picked wins.",
          ]) + [Spacer(1, 6),
          callout("Synthetic forward test (seed 7, 45 days, events only). The first event arm (bet venue excluded from its own fair line) "
                  "claimed <b>+18% model EV</b> but had <b>+2.9% true EV</b>: a winner's curse. The pre-registered fix "
                  "(<b>events_inclusive_ev3</b>: the bet venue stays in as one weighted signal) cut model EV to 8.9% and raised "
                  "<b>true EV to +6.2%</b>. The short-dated APR arm reached +6.4%, the negative control lost (and caught a biased CLV benchmark, since fixed), and the venue scorecard "
                  "correctly ranked the planted accurate exchange first. The demo's numbers come from a simulated world. Real "
                  "evidence only comes from the live forward test.",
                  "EVIDENCE FROM THE BUILT-IN DEMO")]

    # ---------------------------------------------------------------- 7. game plan
    s += [PageBreak(), P("7. The game plan: first 90 days", "h1"),
          table([["Days", "Build", "Grow", "Prove"],
                 ["1–14", "Deploy live scanner (cron). Map the first 30 questions. Landing page + email capture. Package skill zip.",
                  "Announce build-in-public. First thread/video. Join 3 communities.", "6 arms collecting. Public hash commit."],
                 ["15–30", "Grow mappings to 100+ questions. Daily dashboard auto-publish.",
                  "Lab Notes #1–#2. Flagship “built my own model” video.", "First CLV readout (directional, not conclusive)."],
                 ["31–60", "Hosted multi-user version. Discord/Telegram alerts. Real-bet tracker.",
                  "Lab Notes weekly. 2 creator collabs. Reddit/X AMAs.", "500–1,000 contracts. First verdicts."],
                 ["61–90", "Billing (Stripe), Founding Member tier, onboarding email sequence.",
                  "Open beta to the list. Testimonials. Referral programme.", "Publish 90-day report: wins, losses, method updates."]],
                [0.6 * inch, 2.35 * inch, 2.1 * inch, 1.85 * inch]),
          P("Weekly operating cadence", "h3"),
          table([["Day", "Ritual"],
                 ["Monday", "Review scan health and errors, settle results, check data-source quotas"],
                 ["Tuesday", "Write Lab Notes from the dashboard; schedule social clips"],
                 ["Wednesday", "Ship one product improvement (alerts, venues, UX)"],
                 ["Thursday", "Community: answer questions, Discord office hours, collect feedback"],
                 ["Friday", "Metrics review (funnel + model health); decide next week's single priority"]],
                [1.1 * inch, 5.8 * inch]),
          P("Decision rules (pre-commit to these)", "h3")] + B([
              "If no live arm reaches CLV t > 2 by ~1,500 contracts: <b>don't</b> market it as a winning model. Pivot the message to “the honest lab” and the tooling.",
              "If opt-in < 20% after 2,000 visitors: change the offer headline before changing the product.",
              "If churn > 10%/mo: interview churned users. Usually the cause is alert speed, too few mapped questions, or a venue being unavailable in their country.",
              "Never alter a running experiment. Clone it with a new name.",
          ])

    # ---------------------------------------------------------------- 8. tech roadmap
    s += [P("8. Product & model roadmap", "h2"),
          table([["Priority", "Feature", "Tier"],
                 ["P0", "Hosted scheduler + per-user dashboards; alerting (Discord, Telegram, email)", "Pro"],
                 ["P0", "Semi-automatic event matching (embeddings + resolution-rule check + human approval)", "Pro"],
                 ["P0", "Region filter: only venues available in the user's state/country", "Free/Pro"],
                 ["P1", "PredictIt, Betfair politics, Smarkets connectors; order-book depth for size-aware EV", "Pro"],
                 ["P1", "Learned venue weights from the scorecard (walk-forward), auto-refit monthly", "Pro"],
                 ["P1", "Real-bet tracker: log actual bets, auto-compute personal CLV report card", "Pro"],
                 ["P2", "Model upgrades: liquidity/time-decay weights, Bayesian EV shrinkage for selection bias, line-movement features", "Quant"],
                 ["P2", "Monte-Carlo bankroll & risk-of-ruin simulator; Kelly fraction optimiser", "Quant"],
                 ["P2", "API + historical snapshot exports", "Quant / B2B"]],
                [0.75 * inch, 4.9 * inch, 1.25 * inch])]

    # ---------------------------------------------------------------- 9. compliance
    s += [P("9. Risk, compliance & ethics", "h1"),
          P("This section is a checklist, not legal advice. Talk to a lawyer before taking money or running affiliate links.", "small"),
          table([["Risk", "Mitigation"],
                 ["Data terms of service", "Use only official/public APIs (Kalshi, Polymarket, Manifold). Do <b>not</b> scrape OddsJam or other paid screens. Respect rate limits and attribution."],
                 ["Gambling advertising rules", "21+ (or local age) messaging, responsible-gambling notices, no “guaranteed profit” claims, geo-restrict where required."],
                 ["Affiliate licensing", "Several US states require affiliate registration/licensing. Join programmes only where compliant; disclose every link (FTC)."],
                 ["Prediction-market legality", "Access to Kalshi/Polymarket varies by jurisdiction and changes over time. Show users the rules; don't help anyone circumvent geoblocks."],
                 ["Performance claims", "Publish full ledgers, including losses and paper-vs-real caveats. State clearly that past or simulated results ≠ future returns."],
                 ["Tout / advisory regulation", "Sell software, data and education, not individualised picks with promised outcomes."],
                 ["Capital lock-up & liquidity", "Long-dated contracts tie up money for months. Rank by APR, show order-book depth, warn on thin markets."],
                 ["Resolution-rule mismatch", "The #1 fake edge: two venues' “same” question resolving differently. Every mapping needs a human rules check."],
                 ["User harm", "Bet limits in the tracker, cool-off reminders, 1-800-GAMBLER link in every footer and email."],
                 ["Taxes & payments", "Use Stripe/Paddle; collect sales tax/VAT as required; keep business and betting funds separate."]],
                [1.75 * inch, 5.15 * inch]),
          P("10. What is in the repository", "h2"),
          table([["Path", "Contents"],
                 ["edgelab/", "Model (odds, devig, consensus, calibration), simulator, ledger, stats, dashboard renderer, CLI"],
                 ["edgelab/sources/", "Connectors: kalshi, polymarket, manifold, demo (synthetic world with known truth); oddsapi (sports, off by default)"],
                 ["experiments/", "Pre-registered strategies: 5 active event arms (incl. negative control), 4 retired sports arms kept for the record"],
                 ["skill/edgelab-ev/", "Claude skill (SKILL.md + methodology and CLI references); zipped in docs/ for give-away"],
                 ["tests/", "16 unit and end-to-end tests (planted edge found; control loses; venue scorecard; matcher; pre-registration enforced)"],
                 ["docs/", "This roadmap and its generator script"]],
                [1.6 * inch, 5.3 * inch]),

          ]
    doc.build(s, onFirstPage=cover, onLaterPages=on_page)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "EdgeLab_Monetization_Roadmap.pdf")
    build(out)
    print(out)
