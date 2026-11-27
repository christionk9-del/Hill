"""Best-effort promo discovery.

Sportsbook and PrizePicks promo pages need a login, a geolocation check or a
JavaScript app, so they can't be read reliably by a script. Instead this module
reads any public pages or RSS feeds you list in config.json ("promo_feeds"),
for example a promo-aggregator page, a book's public X/RSS bridge, or a
subreddit's RSS, and pulls out lines that look like promos (profit boosts,
bonus bets, no-sweats, PrizePicks discounts).

Detected promos are shown in the report as "verify and add to promos.json",
because only you can see what your account is actually offered. Once a promo is
in promos.json the scanner finds the best bet to use it on.
"""
from __future__ import annotations

import html
import re
import urllib.request

PATTERNS = [
    ("profit_boost", re.compile(r"(\d{2,3})\s*%\s*(?:profit|odds)?\s*boost", re.I)),
    ("bonus_bet", re.compile(r"\$?(\d{2,4})\s*(?:in\s+)?(?:bonus bets?|free bets?|site credit)", re.I)),
    ("no_sweat", re.compile(r"no[\s-]?sweat|second[\s-]chance|bet insurance|risk[\s-]free", re.I)),
    ("dfs_discount", re.compile(r"(?:taco tuesday|discount(?:ed)? (?:line|pick)|demon|goblin|flash sale|\d+% off)", re.I)),
]

BOOK_NAMES = {
    "prizepicks": "prizepicks", "underdog": "underdog", "draftkings": "draftkings",
    "fanduel": "fanduel", "betmgm": "betmgm", "caesars": "williamhill_us",
    "fanatics": "fanatics", "espn bet": "espnbet", "thescore": "espnbet",
    "betrivers": "betrivers", "hard rock": "hardrockbet", "kalshi": "kalshi",
    "novig": "novig", "prophetx": "prophetx", "pick6": "pick6", "betr": "betr_us_dfs",
    "bet365": "bet365",
}


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ev-scanner"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(2_000_000).decode(errors="replace")
    raw = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", raw)
    return html.unescape(text)


def detect_promos(text: str, source: str = "") -> list[dict]:
    found, seen = [], set()
    for line in re.split(r"[\n\r]+|(?<=[.!?])\s+", text):
        line = " ".join(line.split())
        if not (15 <= len(line) <= 300):
            continue
        low = line.lower()
        book = next((v for k, v in BOOK_NAMES.items() if k in low), None)
        for kind, pat in PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            key = (kind, low[:120])
            if key in seen:
                continue
            seen.add(key)
            d = {"type": kind, "book": book, "text": line, "source": source}
            if kind == "profit_boost":
                d["boost_pct"] = int(m.group(1))
            if kind == "bonus_bet":
                d["amount"] = int(m.group(1))
            found.append(d)
            break
    return found


def scan_feeds(urls: list[str], log=print) -> tuple[list[dict], list[str]]:
    promos, errors = [], []
    for url in urls:
        try:
            promos += detect_promos(_fetch_text(url), url)
        except Exception as e:  # network / parsing: never kill the daily scan
            errors.append(f"promo feed {url}: {e}")
    log(f"[promos] {len(promos)} promo mentions from {len(urls)} feeds")
    return promos, errors
