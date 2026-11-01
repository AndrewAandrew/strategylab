# -*- coding: utf-8 -*-
"""Lead audit of the round-6 letter (view: verify pipeline, not republish).
1) per-run journal<->report cross-check for all 24 r6_* cells:
   rows(journal_p)==trades, sum(pnl)==net_pnl, sign-wins==wins, 10000+net==equity;
2) fold/AGG means recomputed from the letter's published SUM/n (appendix);
3) fold means vs letter tables.
"""
import csv
import pathlib
from decimal import Decimal

calib = pathlib.Path("data/calib")
bad = tot = 0
for d in sorted(calib.glob("r6_*")):
    tot += 1
    rep = next(iter(d.glob("*_report_p.txt")), None)
    jp = next(iter(d.glob("*_journal_p.csv")), None)
    if rep is None or jp is None:
        print("FAIL missing files:", d.name)
        bad += 1
        continue
    fields = {}
    for line in rep.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" in line and not line.startswith("#"):
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    with jp.open(encoding="utf-8", newline="") as fh:
        pnls = [Decimal(r["pnl"]) for r in csv.DictReader(fh)]
    n, net, wins = len(pnls), sum(pnls, Decimal(0)), sum(1 for p in pnls if p > 0)
    errs = []
    if n != int(fields["trades"]):
        errs.append("n")
    if abs(net - Decimal(fields["net_pnl"])) > Decimal("0.01"):
        errs.append("net")
    if wins != int(fields["wins"]):
        errs.append("wins")
    if abs(net + Decimal("10000") - Decimal(fields["equity"])) > Decimal("0.01"):
        errs.append("eq=10k+net")
    if errs:
        bad += 1
        print("FAIL", d.name, errs)
print(f"runs checked: {tot} | mismatches: {bad}")

# letter appendix sums (fold-level published) -> mean check vs tables
APP = {
    "JhBTC": {"f1": (148, "21.905856"), "f2": (122, "12.372352"),
              "f3": (152, "-9.393328"), "AGG": (422, "24.884880")},
    "JhETH": {"f1": (154, "41.939845"), "f2": (138, "-1.522222"),
              "f3": (142, "12.444311"), "AGG": (434, "52.861933")},
    "S15BTC_a": {"f1": (235, "-64.742795"), "f2": (236, "-44.910829"),
                 "f3": (261, "-66.762228"), "AGG": (732, "-176.415853")},
    "S15BTC_b": {"f1": (235, "-43.590626"), "f2": (236, "-23.665775"),
                 "f3": (261, "-43.270674"), "AGG": (732, "-110.527075")},
    "S15ETH_a": {"f1": (265, "-7.178348"), "f2": (241, "-28.501219"),
                 "f3": (245, "-53.517661"), "AGG": (751, "-89.197229")},
    "S15ETH_b": {"f1": (265, "16.652090"), "f2": (241, "-6.827491"),
                 "f3": (245, "-31.468218"), "AGG": (751, "-21.643618")},
    "J15BTC": {"f1": (560, "-18.677440"), "f2": (516, "-13.120378"),
               "f3": (485, "-46.514454"), "AGG": (1561, "-78.312272")},
    "J15ETH": {"f1": (555, "-29.994077"), "f2": (547, "-23.604679"),
               "f3": (564, "-24.941935"), "AGG": (1666, "-78.540692")},
    "S5BTC_a": {"f1": (769, "-129.976104"), "f2": (809, "-152.689396"),
                "f3": (759, "-142.083599"), "AGG": (2337, "-424.749099")},
    "S5BTC_b": {"f1": (769, "-60.774073"), "f2": (809, "-79.881993"),
               "f3": (759, "-73.774472"), "AGG": (2337, "-214.430538")},
    "S5ETH_a": {"f1": (778, "-106.302237"), "f2": (813, "-120.676875"),
                "f3": (768, "-65.548521"), "AGG": (2359, "-292.527634")},
    "S5ETH_b": {"f1": (778, "-36.286140"), "f2": (813, "-47.502413"),
                "f3": (768, "3.590929"), "AGG": (2359, "-80.197624")},
}
TBL = {  # means as published in letter tables, pp
    "JhBTC": {"f1": 0.1480, "f2": 0.1014, "f3": -0.0618, "AGG": 0.0590},
    "JhETH": {"f1": 0.2723, "f2": -0.0110, "f3": 0.0876, "AGG": 0.1218},
    "S15BTC_a": {"f1": -0.2755, "f2": -0.1903, "f3": -0.2558, "AGG": -0.2410},
    "S15BTC_b": {"AGG": -0.1510},
    "S15ETH_a": {"f1": -0.0271, "f2": -0.1183, "f3": -0.2184, "AGG": -0.1188},
    "S15ETH_b": {"f1": 0.0628, "AGG": -0.0288},
    "J15BTC": {"f1": -0.0334, "f2": -0.0254, "f3": -0.0959, "AGG": -0.0502},
    "J15ETH": {"f1": -0.0540, "f2": -0.0432, "f3": -0.0442, "AGG": -0.0471},
    "S5BTC_a": {"f1": -0.1690, "f2": -0.1887, "f3": -0.1872, "AGG": -0.1817},
    "S5BTC_b": {"AGG": -0.0918},
    "S5ETH_a": {"f1": -0.1366, "f2": -0.1484, "f3": -0.0853, "AGG": -0.1240},
    "S5ETH_b": {"f3": 0.0047, "AGG": -0.0340},
}
mis = 0
for key, folds in APP.items():
    tot_s = Decimal(0)
    tot_n = 0
    for f, (n, s) in folds.items():
        if f == "AGG":
            continue
        m = Decimal(s) / n * 100
        tot_s += Decimal(s)
        tot_n += n
        if key in TBL and f in TBL[key]:
            if abs(m / 100 - Decimal(str(TBL[key][f]))) > Decimal("0.0001"):
                mis += 1
                print(f"MEAN MISMATCH {key} {f}: {m:.4f} vs {TBL[key][f]}")
    n_agg, s_agg = folds["AGG"]
    m_agg = Decimal(s_agg) / n_agg * 100
    if key in TBL and "AGG" in TBL[key]:
        if abs(m_agg / 100 - Decimal(str(TBL[key]["AGG"]))) > Decimal("0.0001"):
            mis += 1
            print(f"AGG MISMATCH {key}: {m_agg:.4f} vs {TBL[key]['AGG']}")
    if n_agg != tot_n or abs(Decimal(s_agg) - tot_s) > Decimal("0.0005"):
        mis += 1
        print(f"SUM/AGG n mismatch {key}: folds {tot_n}/{tot_s} vs agg {n_agg}/{s_agg}")
    print(f"{key}: AGG mean {m_agg/100:.6f} ok")
print("letter-internal mismatches:", mis)
