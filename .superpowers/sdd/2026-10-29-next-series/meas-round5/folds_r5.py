# dump per-fold diagnostics rows from sums logic
import sys
sys.path.insert(0, "C:/Users/egoro/AppData/Local/Temp/opencode/meas-r5")
from decimal import Decimal as D
import csvonly_r5 as m
groups = [
    ("JwBTC", ["j_w_btc_f1","j_w_btc_f2","j_w_btc_f3"]),
    ("JwETH", ["j_w_eth_f1","j_w_eth_f2","j_w_eth_f3"]),
    ("SwBTC", ["s_w_btc_f1","s_w_btc_f2","s_w_btc_f3"]),
    ("SwETH", ["s_w_eth_f1","s_w_eth_f2","s_w_eth_f3"]),
    ("t2BTC", ["j_t2_btc_f1","j_t2_btc_f2","j_t2_btc_f3"]),
]
lines=[]
for tag, ids in groups:
    for cid in ids:
        strategy = "jaguar_ema" if cid.startswith("j") else "smc_choch_fvg"
        sk = "btc" if "_btc_" in cid else "eth"
        fk = cid.rsplit("_",1)[1]
        kind = "j" if cid.startswith("j") else "s"
        fam = "w" if "_w_" in cid else ("p" if "_p_" in cid else "t2")
        floor = D("0.001") if fam=="w" and kind=="j" else D("0.0015") if fam=="w" else m.FLOOR_P
        r = m.analyse(cid, strategy, sk, fk, kind, fam, floor)
        n=len(r["rows"]); sa=sum((t["comb_a"] for t in r["rows"]),D(0)); sb=sum((t["comb_b"] for t in r["rows"]),D(0))
        wa=sum(1 for t in r["rows"] if t["comb_a"]>0); wj=sum(1 for t in r["rows"] if t["pnl"]>0)
        lines.append(f"{tag} {fk}: n={n} SUM_a={float(sa):.6f} mean_a={float(sa/n):.6f} SUM_b={float(sb/n):.6f} wins_a={wa} wins_j={wj} net={float(D(r['report']['net_pnl'])):.6f}")
print("\n".join(lines))
