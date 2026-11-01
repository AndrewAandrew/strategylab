# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "C:/Users/egoro/AppData/Local/Temp/opencode/meas-r6")
from decimal import Decimal as D
import csvonly_r6 as m

res = {}
for cid, strategy, sk, fk, kind in m.CELLS:
    res[cid] = m.analyse(cid, strategy, sk, fk, kind)

groups = [
    ("JhBTC", ["j_h_btc_f1", "j_h_btc_f2", "j_h_btc_f3"], "j"),
    ("JhETH", ["j_h_eth_f1", "j_h_eth_f2", "j_h_eth_f3"], "j"),
    ("S15BTC", ["s_15_btc_f1", "s_15_btc_f2", "s_15_btc_f3"], "s"),
    ("S15ETH", ["s_15_eth_f1", "s_15_eth_f2", "s_15_eth_f3"], "s"),
    ("J15BTC", ["j_15_btc_f1", "j_15_btc_f2", "j_15_btc_f3"], "j"),
    ("J15ETH", ["j_15_eth_f1", "j_15_eth_f2", "j_15_eth_f3"], "j"),
    ("S5BTC", ["s_5_btc_f1", "s_5_btc_f2", "s_5_btc_f3"], "s"),
    ("S5ETH", ["s_5_eth_f1", "s_5_eth_f2", "s_5_eth_f3"], "s"),
]
lines = []
for tag, ids, kind in groups:
    tn = tsa = tsb = tw = twj = 0
    tnet = D(0)
    for cid in ids:
        r = res[cid]
        n = len(r["rows"])
        sa = sum((t["comb_a"] for t in r["rows"]), D(0))
        sb = sum((t["comb_b"] for t in r["rows"]), D(0))
        w = sum(1 for t in r["rows"] if t["comb_a"] > 0)
        wj = sum(1 for t in r["rows"] if t["pnl"] > 0)
        net = D(r["report"]["net_pnl"])
        tn += n; tsa += sa; tsb += sb; tw += w; twj += wj; tnet += net
        lines.append(f"{tag} {cid.rsplit('_',1)[1]}: n={n} SUM_a={float(sa):.6f} "
                     f"SUM_b={float(sb):.6f} wins_a={w} wins_j={wj} net={float(net):.6f}")
    lines.append(f"{tag} AGG: n={tn} SUM_a={float(tsa):.6f} mean_a={float(tsa/tn):.6f} "
                 f"SUM_b={float(tsb):.6f} mean_b={float(tsb/tn):.6f} wins_a={tw} "
                 f"WR_a={tw/tn*100:.4f} wins_j={twj} WR_j={twj/tn*100:.4f} net={float(tnet):.6f}")
    lines.append("")
text = "\n".join(lines)
print(text)
with open("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r6/sums_r6.txt", "w",
          encoding="utf-8") as fh:
    fh.write(text)
