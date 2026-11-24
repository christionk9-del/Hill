import sys, json, itertools; 
import numpy as np
from datetime import datetime
from kalshi_bot.crypto_stack import pair_rho, leg_ticker, leg_mid, fair_prob
R = json.load(open("/home/user/Hill/data/results15.json")); C = json.load(open("/home/user/Hill/data/combo15.json"))
CR = ["KXBTC15M","KXETH15M","KXSOL15M","KXXRP15M","KXDOGE15M","KXHYPE15M","KXZEC15M"]
rho = {}
for a, b in itertools.combinations(CR, 2):
    rho[(a, b)] = rho[(b, a)] = pair_rho(R, a, b)
print("implied pair correlations: avg", round(np.mean([rho[(a,b)] for a,b in itertools.combinations(CR,2)]),2), "| BTC-ETH", round(rho[("KXBTC15M","KXETH15M")],2))
out = []
for c in C:
    legs = c["legs"]
    if len(legs) < 2 or not all(s in CR for s, _ in legs) or len({s for s, _ in legs}) != len(legs): continue
    coins = [s for s, _ in legs]; sides = [x for _, x in legs]
    tks = [leg_ticker(s, c["close_time"]) for s in coins]
    for y, n, ts, t in c["trades"]:
        if ts != "yes" or n <= 0 or not 0 < y < 1: continue
        tt = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        mids = [leg_mid(k, tt) for k in tks]
        if any(m is None for m in mids): continue
        f = fair_prob(sides, mids, coins, rho)
        indep = float(np.prod([m if s == "yes" else 1 - m for s, m in zip(sides, mids)]))
        out.append({"k": len(legs), "same": len(set(sides)) == 1, "price": y, "n": n, "fair": f, "indep": indep,
                    "win": c["result"] == "yes", "mins_left": (datetime.fromisoformat(c["close_time"].replace("Z","+00:00")).timestamp() - tt) / 60})
json.dump(out, open("/home/user/Hill/data/stack_eval.json", "w")); print("evaluated", len(out), "trades")
