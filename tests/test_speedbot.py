import math
import time
from dataclasses import replace

from kalshi_bot import speedbot as sb


def test_fair_value_basics():
    assert abs(sb.fair_up(100, 100, 10, 0.001) - 0.5) < 1e-9
    assert sb.fair_up(100.3, 100, 10, 0.001) > 0.8
    # the 60 s settlement average makes the outcome less random than the raw time left
    assert sb.fair_up(100.1, 100, 1.0, 0.001) > sb.fair_up(100.1, 100, 1.0 + 2 / 3, 0.001) - 1e-12
    # inside the last minute, the part already averaged is locked in
    assert sb.fair_up(100.0, 100, 0.2, 0.001, window_avg=100.5) > 0.95
    assert sb.fair_up(100.0, 100, 0.2, 0.001, window_avg=99.5) < 0.05
    assert sb.fair_up(99, 100, 0, 0.001, window_avg=101) == 1.0


def test_fee_rounding():
    assert sb.order_fee(2, 0.45) == 0.04
    assert abs(sb.fee_per_contract(0.5) - 0.0175) < 1e-12


def test_book_snapshot_delta_both_formats():
    b = sb.Book()
    b.reset("T")
    b.snapshot({"yes": [[40, 10], [42, 5]], "no": [[55, 7], [56, 3]]}, 1.0)
    assert b.ask("yes") == (0.44, 3)          # 1 - best NO bid (0.56)
    assert b.ask("no") == (0.58, 5)           # 1 - best YES bid (0.42)
    b.delta({"side": "no", "price": 56, "delta": -3}, 2.0)
    assert b.ask("yes") == (0.45, 7)
    b.snapshot({"yes_dollars": [["0.4100", "4.00"]], "no_dollars": [["0.5700", "9.00"]]}, 3.0)
    assert b.ask("yes") == (0.43, 9.0) and b.ask("no") == (0.59, 4.0)
    b.delta({"side": "yes", "price_dollars": "0.4300", "delta_fp": "2.00"}, 4.0)
    assert b.ask("no") == (0.57, 2.0)


def test_spot_series_and_window_avg():
    s = sb.SpotFeed()
    for t, p in ((100.2, 10.0), (101.5, 12.0), (104.0, 14.0)):
        s.on_price(p, t)
    assert s.series(100, 105) == [10.0, 12.0, 12.0, 12.0, 14.0]
    assert s.window_avg(100, 105) == 12.0


def test_evaluate_picks_underpriced_side_once_per_window(tmp_path):
    bot = sb.SpeedBot(sb.SpeedConfig(), db=str(tmp_path / "s.db"), client=type("C", (), {"signed_headers": lambda *a: {}})())
    now = time.time()
    bot.windows.cur = {"ticker": "KXBTC15M-X", "strike": 58000.0, "open_ts": now - 300, "close_ts": now + 600}
    bot.spot.on_price(58100.0, now)          # $100 above strike, 10 min left
    bot.spot.sigma = 0.0008
    bot.book.reset("KXBTC15M-X")
    bot.book.set_top({"yes": (0.55, 50), "no": (0.47, 50)}, now)
    b = bot.evaluate(now)
    fair = sb.fair_up(58100, 58000, 10, 0.0008, None, bot.cfg.basis)
    assert b and b["side"] == "yes" and abs(b["fair"] - fair) < 1e-9 and b["edge"] >= 0.10
    bot.db.execute("INSERT INTO bets (ticker, mode, filled) VALUES ('KXBTC15M-X', 'paper', 1)")
    assert bot.evaluate(now) is None                     # one bet per window
    bot2 = sb.SpeedBot(sb.SpeedConfig(), db=str(tmp_path / "t.db"), client=bot.client)
    bot2.windows, bot2.spot, bot2.book = bot.windows, bot.spot, bot.book
    bot2.spot.ts = now - 3                               # stale BTC price -> no trade
    assert bot2.evaluate(now) is None
