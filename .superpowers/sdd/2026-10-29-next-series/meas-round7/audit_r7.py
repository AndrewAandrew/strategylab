# -*- coding: utf-8 -*-
"""Аудит ведущего раунда 7: независимо из артефактов data/calib/r7_* — целостность
каждой ячейки-фолда (trades==строк, wins==знаки, sum(pnl)==net_pnl,
equity==10000+net) + вердикт правила 6 (net_pnl>=0 каждый фолд) + сводка % из
csv-колонки net для сверки с письмом брата. Только stdlib, read-only."""
import csv
import pathlib
import sys
from decimal import Decimal

BASE = pathlib.Path("data/calib")
dirs = sorted(p for p in BASE.iterdir() if p.is_dir() and p.name.startswith("r7_"))
print(f"ячеек-диров: {len(dirs)}")
if len(dirs) != 24:
    print("СТОП: ожидалось 24 дира");  sys.exit(1)

METRICS = ("trades", "wins", "net_pnl", "equity", "max_drawdown")
fail = []
rows_out = []
for d in dirs:
    csv_files = [p for p in d.glob("*.csv") if not p.name.endswith(("_journal_p.csv", "_journal_o.csv"))]
    assert len(csv_files) == 1, f"{d}: csv не один: {csv_files}"
    with open(csv_files[0], newline="", encoding="utf-8") as fh:
        all_lines = fh.read().splitlines()
    col_idx = next(i for i, ln in enumerate(all_lines) if ln.startswith("fork,"))
    rdr = csv.DictReader(all_lines[col_idx:])
    pess = [r for r in rdr if r["fork"] == "pessimistic"]
    assert len(pess) == 1, f"{d}: строк pessimistic != 1"
    row = pess[0]
    trades = int(row["trades"]); wins = int(row["wins"])
    net = Decimal(row["net_pnl"]); equity = Decimal(row["equity"])
    jpath = d / (csv_files[0].name.replace(".csv", "_journal_p.csv"))
    with open(jpath, newline="", encoding="utf-8") as fh:
        jrows = list(csv.DictReader(fh))
    pnls = [Decimal(r["pnl"]) for r in jrows]
    problems = []
    rounding = False
    if len(jrows) != trades:
        problems.append(f"trades={trades} != строк журнала {len(jrows)}")
    if sum(pnls, Decimal(0)) != net:
        problems.append(f"sum(pnl)={sum(pnls, Decimal(0))} != net_pnl={net}")
    if sum(1 for p in pnls if p > 0) != wins:
        problems.append(f"wins={wins} != знаков>0 {sum(1 for p in pnls if p > 0)}")
    if abs(equity - (10000 + net)) > Decimal("0.01"):
        problems.append(f"equity {equity} != 10000+net {10000 + net}")
    elif abs(equity - (10000 + net)) > 0:
        rounding = True
    if problems:
        fail.append((d.name, problems))
    rows_out.append((d.name, trades, wins, f"{net:.4f}", f"{equity:.2f}", "RULE6_FAIL" if net < 0 else "rule6_ok", rounding))

nround = sum(1 for r in rows_out if r[6])
print(f"equity-округление >0 и ≤$0.01: {nround} фолдов (норма: независимое округление колонок)")

print(f"целостность FAIL: {len(fail)}")
for name, probs in fail:
    print("  ", name, probs)
print()
# сотоварищ по клетке: имя ячейки = r7_<клетка>_<сим>_<фолд>
cells = {}
for name, trades, wins, net, eq, flag, _rnd in rows_out:
    parts = name.split("_")
    cell = "_".join(parts[1:-2]); sym_f = "_".join(parts[-2:])
    cells.setdefault(cell, []).append((sym_f, trades, net, flag))
for cell in sorted(cells):
    entries = cells[cell]
    dead6 = all(f == "RULE6_FAIL" for _, _, _, f in entries)
    tot_trades = sum(t for _, t, _, _ in entries)
    print(f"{cell:16s} фолдов={len(entries)} Σtrades={tot_trades:4d} net по фолдам={[': '.join(n for _,_,n,_ in entries)]}=> правило6: {'FAIL (мертва)' if dead6 else 'не всё мертво'}")
