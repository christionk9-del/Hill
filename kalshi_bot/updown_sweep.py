"""Sweep entry/exit rules for BTC 15-min up/down across 5 periods (Dec 2025-Jan 2026 halves +
Mar-Sep 2026 thirds). Needs data/updown/oos_markets.pkl (Kalshi /historical/trades).
Result: no rule is profitable in all 5 periods; best are ~break-even favorites (80-92c)."""
import itertools, pickle, sys, time
from multiprocessing import Pool
import numpy as np

from kalshi_bot.updown_tp import Params, evaluate, load_dataset_markets
M = load_dataset_markets(); M.sort(key=lambda m: m.open_ts); mid = M[len(M)//2].open_ts
O = pickle.load(open("/home/user/Hill/data/updown/oos_markets.pkl", "rb")); O.sort(key=lambda m: m.open_ts)
PER = [[m for m in M if m.open_ts < mid], [m for m in M if m.open_ts >= mid]] + [list(x) for x in np.array_split(np.array(O, dtype=object), 3)]
grid = []
for side, (wl, wh), (pl, ph), tp, ex, em in itertools.product(
        ("no", "yes"), ((0, 60), (60, 180), (120, 180), (180, 420), (420, 720)),
        ((.10, .20), (.20, .35), (.35, .50), (.50, .65), (.65, .80), (.80, .92)),
        (.10, .20, .50, None), ("taker", "maker"), ("taker", "maker")):
    if tp is None and ex == "maker":
        continue
    grid.append(Params(side=side, win_lo=wl, win_hi=wh, p_lo=pl, p_hi=ph, tp=tp, exit_mode=ex, entry_mode=em))
def run(P):
    return P, [{k: v for k, v in evaluate(S, P).items() if not k.startswith("_")} for S in PER]
if __name__ == "__main__":
    t = time.time()
    with Pool(4) as pool:
        out = pool.map(run, grid, chunksize=4)
    pickle.dump(out, open("/home/user/Hill/data/updown/sweep5.pkl", "wb"))
    print(len(out), "combos x 5 periods in", round(time.time() - t), "s")
