# -*- coding: utf-8 -*-
"""Независимый пересчёт comb % ведущим (аудит письма брата r7, колонки %).

Свой код, та же контрактная формула §1.6:
  comb = 100*[(px-pe)*s - f_x*px - f_e*pe]/pe
 Fees: J f_e=TAKER; S-B f_e=MAKER; S-A вход реконструкция pe*(1±SLIP)+TAKER.
 f_x: take_profit -> MAKER, иначе TAKER. Направление — на позицию (entry_ts):
 допустим тот s, у которого sign(bracket)==sign(pnl) во ВСЕХ строках позиции;
 оба допустимы -> AMBIG -> консервативный минимум (как у брата); ни один -> ERROR.
Сверка SUM/mean/amb по строкам sums_r7.txt. Только stdlib, Decimal.
"""
from decimal import Decimal, getcontext
import csv
import pathlib
import re
import sys

getcontext().prec = 40
TAKER = Decimal("0.0006")
MAKER = Decimal("0.0002")
SLIP = Decimal("0.0005")
BASE = pathlib.Path("data/calib")

FOLDS = [
    ("j_b1_h", "j", ["j_b1_h_btc_f1", "j_b1_h_btc_f2", "j_b1_h_btc_f3",
                     "j_b1_h_eth_f1", "j_b1_h_eth_f2", "j_b1_h_eth_f3"]),
    ("j_b25_h", "j", ["j_b25_h_btc_f1", "j_b25_h_btc_f2", "j_b25_h_btc_f3",
                      "j_b25_h_eth_f1", "j_b25_h_eth_f2", "j_b25_h_eth_f3"]),
    ("s_tp25_15", "s", ["s_tp25_15_btc_f1", "s_tp25_15_btc_f2", "s_tp25_15_btc_f3",
                        "s_tp25_15_eth_f1", "s_tp25_15_eth_f2", "s_tp25_15_eth_f3"]),
    ("s_tp60_15", "s", ["s_tp60_15_btc_f1", "s_tp60_15_btc_f2", "s_tp60_15_btc_f3",
                        "s_tp60_15_eth_f1", "s_tp60_15_eth_f2", "s_tp60_15_eth_f3"]),
]

def brackets(r, pe, s, kind):
    """возвращает (comb_a, comb_b) для строки при направлении s."""
    px = r["px"]
    f_x = MAKER if r["reason"] == "take_profit" else TAKER
    comb_b = ((px - pe) * s - f_x * px - (TAKER if kind == "j" else MAKER) * pe) / pe * 100
    if kind == "s":
        pe_a = pe * (1 + SLIP) if s > 0 else pe * (1 - SLIP)
        comb_a = ((px - pe_a) * s - f_x * px - TAKER * pe_a) / pe_a * 100
    else:
        comb_a = comb_b
    return comb_a, comb_b

def bracket_sign(r, pe, s, kind):
    """знак денежного скобка (для сверки со знаком pnl); fees-модель A не важна —
    знак long/short-скобка определяем по сырой (px-pe)*s минус комиссии B."""
    px = r["px"]
    f_x = MAKER if r["reason"] == "take_profit" else TAKER
    fe = TAKER if kind == "j" else MAKER
    return (px - pe) * s - f_x * px - fe * pe

def sgn(x):
    return (x > 0) - (x < 0)

mine = {}  # tag -> dict
errors = []
for pref, kind, ids in FOLDS:
    for dname in ids:
        d = BASE / ("r7_" + dname)
        jf = next(p for p in d.glob("*_journal_p.csv"))
        with open(jf, newline="", encoding="utf-8") as fh:
            rows = [{
                "pe": Decimal(r["entry_price"]), "px": Decimal(r["exit_price"]),
                "pnl": Decimal(r["pnl"]), "reason": r["exit_reason"],
                "eps": int(r["entry_ts"]),
            } for r in csv.DictReader(fh)]
        # позиции = группы по entry_ts (J/S: одна позиция на entry_ts)
        poss = {}
        for r in rows:
            poss.setdefault((r["eps"], r["pe"]), []).append(r)
        sum_a = Decimal(0); sum_b = Decimal(0); amb = 0; n = len(rows)
        for (eps, pe), pr in poss.items():
            if len({r["pe"] for r in pr}) != 1:
                errors.append(f"{dname}: разно-pe в группе pos {eps}")
            ok = {}
            for s in (1, -1):
                if all(sgn(bracket_sign(r, pe, s, kind)) == sgn(r["pnl"]) or r["pnl"] == 0
                       for r in pr):
                    ok[s] = True
            if not ok:
                errors.append(f"{dname} pos {eps}: ни одно направление не согласуется со знаком pnl")
                continue
            if len(ok) == 2:
                amb += 1
                # консервативный минимум: меньший comb на строку
                per = []
                for r in pr:
                    ab = [brackets(r, pe, s, kind) for s in (1, -1)]
                    per.append(min(x[0] for x in ab))
                for r, cv in zip(pr, per):
                    sum_a += cv
                    sum_b += cv
                continue
            s = 1 if 1 in ok else -1
            for r in pr:
                ca, cb = brackets(r, pe, s, kind)
                sum_a += ca; sum_b += cb
        mine[dname] = dict(n=n, sum_a=sum_a, sum_b=sum_b, amb=amb)

# ---- сверка с sums_r7.txt ----
sums_txt = pathlib.Path.home().joinpath("AppData/Local/Temp/opencode/meas-r7/sums_r7.txt").read_text(encoding="utf-8")
pat = re.compile(r"^(\S+) (\w+): n=(\d+) SUM_a=(-?[\d.]+) SUM_b=(-?[\d.]+) wins_a=(\d+) wins_j=(\d+) net=(-?[\d.]+)", re.M)
print("== сверка пер-фолд SUM_a/SUM_b (мой пересчёт vs sums брата) ==")
bad = 0
for m in pat.finditer(sums_txt):
    cell, fk, n, sa, sb = m.group(1), m.group(2), int(m.group(3)), Decimal(m.group(4)), Decimal(m.group(5))
    tag = f"{cell}_{fk}"
    if tag not in mine:
        continue
    mm = mine[tag]
    da = mm["sum_a"] - sa; db = mm["sum_b"] - sb
    flag = "OK" if abs(da) < Decimal("0.005") and abs(db) < Decimal("0.005") else "RASSHOD"
    if flag != "OK":
        bad += 1
        print(f"  {tag:18s} n={mm['n']:4d} mine_a={mm['sum_a']:.4f} his_a={sa} d_a={da:+.4f} "
              f"mine_b={mm['sum_b']:.4f} his_b={sb} d_b={db:+.4f} amb={mm['amb']} {flag}")
print(f"  расхождений >0.005: {bad}; ошибок направления: {len(errors)}")
for e in errors:
    print("   ERR:", e)

# ---- пересчёт средних по клеткам (агрегат 3 фолдов) + правило 2 ----
print("\n== клеточные агрегаты comb A % (мой пересчёт) ==")
for pref, kind, ids in FOLDS:
    for sk in ("btc", "eth"):
        tags = [i for i in ids if f"_{sk}_" in i]
        n = sum(mine[t]["n"] for t in tags)
        sa = sum(mine[t]["sum_a"] for t in tags)
        sb = sum(mine[t]["sum_b"] for t in tags)
        mean_a = sa / n
        mean_b = sb / n
        v6 = "FAIL"
        print(f"  {pref} x {sk.upper()} n={n:4d} meanA={mean_a:+.4f}% meanB={mean_b:+.4f}% "
              f"правило2(≥+0.30): {'ок' if mean_a >= Decimal('0.30') else 'FAIL'} правило6: {v6}")
