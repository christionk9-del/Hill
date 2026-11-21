"""Backtest: enter one side of a 15-minute up/down market early in the window and
exit at a take-profit (or stop / time exit / settlement).

Prices come from real trades, split by which side the taker bought, so entries pay
the ask and exits get the bid:
  taker bought NO  at n  -> NO ask was n   (we can BUY NO at n)
  taker bought YES at y  -> NO bid was 1-y (we can SELL NO at 1-y)
(and the mirror for YES). Fees: Kalshi taker fee 0.07*p*(1-p) per contract on
every taker fill; a resting (maker) exit is charged `maker_fee` instead, and only
fills if a later taker BUYS our side at a price above our resting price.
"""
from dataclasses import dataclass, field

import numpy as np

TAKER = 0.07


@dataclass(frozen=True)
class Params:
    side: str = "no"              # "no" = DOWN, "yes" = UP
    win_lo: float = 120           # entry window, seconds after the window opens
    win_hi: float = 180
    p_lo: float = 0.20            # entry price band (ask)
    p_hi: float = 0.35
    tp: float | None = 0.20       # take profit, +20% of entry price; None = no TP
    sl: float | None = None       # stop loss as a fraction, e.g. 0.5 = exit if bid <= 50% of entry
    exit_mode: str = "taker"      # "taker": hit the bid when bid >= target; "maker": resting sell at target
    maker_fee: float = 0.0
    time_exit: float | None = None  # seconds before close to sell at the bid if still open
    latency: float = 1.0          # seconds after entry before an exit can happen
    entry_delay: float = 0.0      # seconds between seeing the signal and our buy filling
    entry_mode: str = "taker"     # "taker": buy at the ask; "maker": resting bid at p_hi, filled when a
                                  # taker SELLS through it (trades strictly below p_hi), maker fee only
    entry_maker_fee: float = 0.0175

    def label(self):
        tp = "hold" if self.tp is None else f"TP+{self.tp:.0%}"
        sl = "" if self.sl is None else f" SL-{self.sl:.0%}"
        te = "" if self.time_exit is None else f" out@{self.time_exit/60:.0f}m left"
        return (f"{'DOWN' if self.side == 'no' else 'UP'} {self.p_lo*100:.0f}-{self.p_hi*100:.0f}c "
                f"@{self.win_lo/60:.0f}-{self.win_hi/60:.0f}m {tp}{sl}{te} {self.exit_mode}")


@dataclass
class Market:
    ticker: str
    open_ts: float
    close_ts: float
    result: str                   # "yes" / "no"
    t: np.ndarray = field(repr=False)        # trade times (unix s), sorted
    yes_px: np.ndarray = field(repr=False)   # dollars
    taker_yes: np.ndarray = field(repr=False)  # bool: taker bought YES


def fee(p):
    return TAKER * p * (1 - p)


def simulate(m: Market, P: Params):
    """Return per-contract P&L in dollars (and details) or None if no entry."""
    s = m.t - m.open_ts
    my_px = m.yes_px if P.side == "yes" else 1 - m.yes_px          # price of OUR side at each trade
    taker_bought_mine = m.taker_yes if P.side == "yes" else ~m.taker_yes
    # asks for our side = trades where taker bought our side; bids = trades where taker bought the other side
    if P.entry_mode == "maker":
        # our bid rests at p_hi from win_lo to win_hi; a taker selling our side below it fills us at p_hi
        ent = np.nonzero(~taker_bought_mine & (s >= P.win_lo) & (s <= P.win_hi) &
                         (my_px < P.p_hi - 1e-9) & (my_px >= P.p_lo - 1e-9))[0]
        if not len(ent):
            return None
        i = ent[0]
        entry, t0 = P.p_hi, m.t[i]
        cost = entry + P.entry_maker_fee * entry * (1 - entry)
        return _exits(m, P, s, my_px, taker_bought_mine, entry, t0, cost)
    ent = np.nonzero(taker_bought_mine & (s >= P.win_lo) & (s <= P.win_hi) &
                     (my_px >= P.p_lo - 1e-9) & (my_px <= P.p_hi + 1e-9))[0]
    if not len(ent):
        return None
    i = ent[0]
    if P.entry_delay > 0:   # we react late: buy at the first ask >= delay later, if still <= top of band
        late = np.nonzero(taker_bought_mine & (m.t >= m.t[i] + P.entry_delay) & (my_px <= P.p_hi + 1e-9)
                          & (my_px >= 0.05) & (s <= P.win_hi + P.entry_delay))[0]
        if not len(late):
            return None
        i = late[0]
    entry, t0 = my_px[i], m.t[i]
    cost = entry + fee(entry)
    return _exits(m, P, s, my_px, taker_bought_mine, entry, t0, cost)


def _exits(m, P, s, my_px, taker_bought_mine, entry, t0, cost):
    after = m.t >= t0 + P.latency
    if P.time_exit is not None:
        after &= m.t <= m.close_ts - P.time_exit
    bids = after & ~taker_bought_mine          # we could sell at my_px
    asks_lifted = after & taker_bought_mine    # someone bought our side at my_px
    exit_idx, exit_px, kind = None, None, "settle"
    cands = []
    if P.tp is not None:
        target = entry * (1 + P.tp)
        if P.exit_mode == "taker":
            k = np.nonzero(bids & (my_px >= target - 1e-9))[0]
            if len(k):
                cands.append((k[0], my_px[k[0]], "tp", fee(my_px[k[0]])))
        else:
            k = np.nonzero(asks_lifted & (my_px > target + 1e-9))[0]   # strictly through our price
            if len(k):
                cands.append((k[0], target, "tp", P.maker_fee * target * (1 - target)))
    if P.sl is not None:
        k = np.nonzero(bids & (my_px <= entry * (1 - P.sl) + 1e-9))[0]
        if len(k):
            cands.append((k[0], my_px[k[0]], "sl", fee(my_px[k[0]])))
    if cands:
        exit_idx, exit_px, kind, xfee = min(cands, key=lambda c: c[0])
        return {"pnl": exit_px - xfee - cost, "cost": cost, "kind": kind, "entry": entry, "t0": t0}
    if P.time_exit is not None:
        k = np.nonzero((m.t > m.close_ts - P.time_exit) & ~taker_bought_mine & (m.t >= t0 + P.latency))[0]
        if len(k):
            px = my_px[k[0]]
            return {"pnl": px - fee(px) - cost, "cost": cost, "kind": "time", "entry": entry, "t0": t0}
    payout = 1.0 if m.result == P.side else 0.0
    return {"pnl": payout - cost, "cost": cost, "kind": "settle", "entry": entry, "t0": t0}


def evaluate(markets, P: Params):
    res = [r for r in (simulate(m, P) for m in markets) if r]
    if not res:
        return {"n": 0}
    pnl = np.array([r["pnl"] for r in res]); cost = np.array([r["cost"] for r in res])
    kinds = [r["kind"] for r in res]
    return {"n": len(res), "ret": float((pnl.sum() + cost.sum()) / cost.sum()), "pnl_per_bet": float(pnl.mean()),
            "win": float((pnl > 0).mean()), "tp_rate": kinds.count("tp") / len(res),
            "sl_rate": kinds.count("sl") / len(res), "avg_entry": float(np.mean([r["entry"] for r in res])),
            "worst_streak": _worst_streak(pnl), "_pnl": pnl, "_cost": cost}


def _worst_streak(pnl):
    w = c = 0
    for x in pnl:
        c = c + 1 if x < 0 else 0
        w = max(w, c)
    return w


def load_dataset_markets(series="KXBTC15M", strikes_pkl="data/updown/KXBTC15M_strikes.pkl"):
    import duckdb, pickle
    strikes = pickle.load(open(strikes_pkl, "rb"))
    con = duckdb.connect()
    df = con.sql(f"""SELECT ticker, epoch(created_time) t, yes_price/100.0 y, taker_side='yes' ty
        FROM 'data/hf/trades-*.parquet' WHERE ticker LIKE '{series}-%' ORDER BY ticker, created_time""").fetchnumpy()
    out, tk = [], df["ticker"]
    bounds = np.nonzero(tk[1:] != tk[:-1])[0] + 1
    for a, b in zip(np.r_[0, bounds], np.r_[bounds, len(tk)]):
        k = strikes.get(tk[a])
        if not k:
            continue
        _, close, result = k
        c = close.timestamp()
        out.append(Market(tk[a], c - 900, c, result, df["t"][a:b].astype(float), df["y"][a:b].astype(float),
                          df["ty"][a:b].astype(bool)))
    return out
