"""Live state machine for the intraday Donchian breakout variant (MNQ, 5-minute RTH bars).

Rules (identical to donchian_lab.py with BEST settings):
  - Only regular-hours bars (9:30-16:00 New York) exist for the indicators; the channel and
    ATR run continuously across days.
  - Entry: price breaks the high (long) or low (short) of the previous 24 RTH bars.
    Allowed on bars 1-47 of the day (9:35 to the 13:25 bar), max 3 trades a day, never in
    the same bar as the previous exit.
  - Initial stop: 1 x ATR(14, Wilder) from entry, ATR as known at the entry bar's open.
  - After every bar close the stop only tightens: to the 24-bar channel low (long) / high
    (short) including that bar, or to close -/+ 2 x ATR, whichever is tighter.
  - Flat at the close of the 15:55 bar.

The engine holds one "model" position. Callers feed it bars and intrabar prices and act on
the events it returns. It never talks to a broker.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

BARS_PER_DAY = 78


@dataclass
class Params:
    n: int = 24              # entry channel, bars
    exit_n: int = 24         # exit channel, bars
    stop_atr: float = 1.0    # initial stop, ATR multiples
    trail_atr: float = 2.0   # trailing stop, ATR multiples
    max_trades: int = 3
    first_bar: int = 1       # first bar index allowed for entries (0 = 9:30 bar)
    last_bar: int = 48       # entries only on bars with index < last_bar (48 = 13:30 bar)
    atr_len: int = 14
    tick: float = 0.25


@dataclass
class Position:
    side: int                # +1 long, -1 short
    entry: float
    stop: float
    R: float
    bar_idx: int


@dataclass
class Event:
    kind: str                # "entry", "stop_moved", "exit"
    side: int = 0
    price: float = 0.0
    stop: float = 0.0
    reason: str = ""
    info: dict = field(default_factory=dict)


class DonchianEngine:
    def __init__(self, p: Params = Params()):
        self.p = p
        keep = max(p.n, p.exit_n)
        self.highs = deque(maxlen=keep)
        self.lows = deque(maxlen=keep)
        self.atr: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.pos: Optional[Position] = None
        self.day = None
        self.tradable = False
        self.trades = 0
        self.next_ok = p.first_bar
        self.idx = -1
        self.bar_open = None
        self.hi = self.lo = self.atr_now = None

    # ---- indicator bookkeeping -------------------------------------------------------
    def _push(self, h, l, c):
        tr = h - l if self.prev_close is None else max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
        a = 1.0 / self.p.atr_len
        self.atr = tr if self.atr is None else self.atr + a * (tr - self.atr)
        self.prev_close = c
        self.highs.append(h)
        self.lows.append(l)

    def ready(self):
        return self.atr is not None and len(self.highs) >= max(self.p.n, self.p.exit_n)

    def warmup(self, h, l, c):
        """Feed a historical RTH bar without trading."""
        self._push(h, l, c)

    # ---- bar lifecycle ---------------------------------------------------------------
    def begin_bar(self, day, idx, bar_open, tradable=True):
        """Call at the start of each RTH bar. idx is 0..77 within the day."""
        if day != self.day:
            self.day, self.trades, self.next_ok, self.tradable = day, 0, self.p.first_bar, tradable
        self.idx, self.bar_open = idx, bar_open
        if self.ready():
            self.hi = max(list(self.highs)[-self.p.n:])
            self.lo = min(list(self.lows)[-self.p.n:])
            self.atr_now = self.atr
        else:
            self.hi = self.lo = self.atr_now = None

    def entry_allowed(self):
        p = self.p
        return (self.tradable and self.pos is None and self.hi is not None and self.trades < p.max_trades
                and p.first_bar <= self.idx < p.last_bar and self.idx >= self.next_ok)

    def entry_levels(self):
        """Prices that trigger a long / short entry on the current bar (one tick through)."""
        if not self.entry_allowed():
            return None
        return self.hi + self.p.tick, self.lo - self.p.tick

    def enter(self, side, price):
        """Open the model position at `price` (a fill or the modelled fill)."""
        R = self.p.stop_atr * self.atr_now
        self.pos = Position(side, price, price - side * R, R, self.idx)
        self.trades += 1
        return Event("entry", side, price, self.pos.stop, info={"R": R, "atr": self.atr_now, "hi": self.hi, "lo": self.lo})

    def on_price(self, high, low):
        """Intrabar check with the bar's running high/low. Returns an entry or a stop exit."""
        if self.pos is None:
            lv = self.entry_levels()
            if lv is None:
                return None
            up, dn = high >= lv[0], low <= lv[1]
            if up and not dn:
                return self.enter(1, max(self.hi, self.bar_open))
            if dn and not up:
                return self.enter(-1, min(self.lo, self.bar_open))
            return None
        ps = self.pos
        if (ps.side > 0 and low <= ps.stop) or (ps.side < 0 and high >= ps.stop):
            px = min(ps.stop, self.bar_open) if ps.side > 0 else max(ps.stop, self.bar_open)
            if ps.bar_idx == self.idx:
                px = ps.stop
            return self._exit(px, "stop")
        return None

    def _exit(self, price, reason):
        ps = self.pos
        self.pos = None
        self.next_ok = self.idx + 1
        return Event("exit", ps.side, price, ps.stop, reason, info={"entry": ps.entry, "pnl_pts": (price - ps.entry) * ps.side})

    def end_bar(self, o, h, l, c, entry_bar_close_rule=False):
        """Call when the bar completes. Returns a list of events (exit, stop_moved)."""
        ev = []
        ps = self.pos
        if ps is not None and entry_bar_close_rule and ps.bar_idx == self.idx and (c - ps.stop) * ps.side <= 0:
            ev.append(self._exit(ps.stop, "stop"))
            ps = None
        self._push(h, l, c)
        if ps is not None:
            if self.idx >= BARS_PER_DAY - 1:
                ev.append(self._exit(c, "close"))
            else:
                chan = list(self.lows if ps.side > 0 else self.highs)[-self.p.exit_n:]
                trail = c - ps.side * self.p.trail_atr * self.atr
                new = max(ps.stop, min(chan), trail) if ps.side > 0 else min(ps.stop, max(chan), trail)
                if new != ps.stop:
                    ps.stop = new
                    ev.append(Event("stop_moved", ps.side, c, new))
        return ev

    def force_exit(self, price, reason):
        return self._exit(price, reason) if self.pos is not None else None
