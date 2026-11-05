# -*- coding: utf-8 -*-
"""Гейты живости V-5 (§4 V2-SIM-AMENDMENT-FROZEN, FROZEN до чисел) — по артефактам.

Decimal-пересчёт с нуля из journal_p.csv (пары entry/exit; ts круга = entry_ts):
 (V-a') все три календарных 60d-трети пула: nq >= 3 И net >= 0 в каждой;
 (V-b') pooled annualized >= +3.0 %/год (net/10000 * 365/180 * 100);
 (V-c') n кругов <= 75.
Контроль сверки: net из journal == metrics-csv == Decimal-строке stdout (raw-лог).
Инварианты: net == price_gross - friction; equity == 10000 + net.

Запуск: python .superpowers/sdd/2026-11-03-seriesV-altcoins/meas/gates_V5.py
stdout — таблица гейтов (для V5-grid-RESULTS); выход 0 всегда, вердикт словами.
"""
from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SER = REPO / ".superpowers" / "sdd" / "2026-11-03-seriesV-altcoins"
CALIB = REPO / "data" / "calib"

START, END = 1774742400, 1790294400
THIRD = 60 * 86400
CAPITAL = Decimal(10000)
HURDLE = Decimal("3.0")
MAX_N = 75
MIN_NQ = 3

MARKETS = ("SOXLUSDT", "SOLUSDT", "ZECUSDT")
CELLS = ("V-a1", "V-a2", "V-a3", "V-b1", "V-b2", "V-b3", "V-c1", "V-c2", "V-c3")


def rows_of(path: Path) -> list[dict[str, str]]:
    # metrics-csv: мета-строки до header-строки "fork,..." — ищем header явно
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read().splitlines()
    hi = next(i for i, ln in enumerate(text) if ln.startswith("fork,"))
    reader = csv.DictReader(text[hi:])
    return list(reader)


def cell_verdict(cell: str, sym: str) -> dict | None:
    out = CALIB / f"dev_{cell}_{sym}"
    stem = f"dev_{cell}_{sym}_{sym}_{START}_{END}"  # --cell уже содержит sym -> двойной символ в stem
    mpath, jpath = out / f"{stem}.csv", out / f"{stem}_journal_p.csv"
    if not mpath.exists() or not jpath.exists():
        return None
    m = rows_of(mpath)[0]
    with open(jpath, encoding="utf-8", newline="") as f:
        lines = f.read().splitlines()
    hi = next(i for i, ln in enumerate(lines) if ln.startswith("ts,kind,"))
    jrows = list(csv.DictReader(lines[hi:]))
    assert len(jrows) % 2 == 0, f"{cell}_{sym}: нечётный journal"
    thirds_net = [Decimal(0)] * 3
    thirds_nq = [0] * 3
    net = Decimal(0)
    for k in range(0, len(jrows), 2):
        e, x = jrows[k], jrows[k + 1]
        assert e["kind"] == "entry" and x["kind"] == "exit"
        t = int((int(e["ts"]) - START) // THIRD)
        assert 0 <= t < 3, f"{cell}_{sym}: entry_ts {e['ts']} вне трети {t}"
        r_net = Decimal(e["cash_delta"]) + Decimal(x["cash_delta"])
        thirds_net[t] += r_net
        thirds_nq[t] += 1
        net += r_net
    n = len(jrows) // 2
    ann = net / CAPITAL * Decimal(365) / Decimal(180) * Decimal(100)
    ok_a = all(thirds_nq[i] >= MIN_NQ and thirds_net[i] >= 0 for i in range(3))
    ok_b = ann >= HURDLE
    ok_c = n <= MAX_N
    live = ok_a and ok_b and ok_c
    chk_net = net == Decimal(m["net_pnl"])
    chk_inv = net == Decimal(m["price_gross"]) - Decimal(m["friction"])
    chk_eq = CAPITAL + net == Decimal(m["equity"])
    return dict(cell=cell, sym=sym, regime=m["regime"], exit=m["exit_mode"], n=n,
                ann=ann, net=net, nq=tuple(thirds_nq), q=tuple(thirds_net),
                ok_a=ok_a, ok_b=ok_b, ok_c=ok_c, live=live,
                chk=(chk_net, chk_inv, chk_eq), version=m["deviation_version"])


def main() -> int:
    print("# V-5 gates (§4 amendment, FROZEN): V-a' nq>=3 и net>=0 в каждой 60d-трети, "
          "V-b' ann>=+3.0%/год, V-c' n<=75 | трение Decimal из journal_p")
    res: list[dict] = []
    for sym in MARKETS:
        for cell in CELLS:
            r = cell_verdict(cell, sym)
            if r is None:
                print(f"# НЕТ АРТЕФАКТОВ {cell}_{sym}")
                continue
            res.append(r)
    bad_inv = [r for r in res if not all(r["chk"])]
    if bad_inv:
        print("# ИНВАРИАНТ-FAIL:", [(r["cell"], r["sym"], r["chk"]) for r in bad_inv])
    q = lambda xs: "/".join(f"{x:+,.0f}" for x in xs)  # noqa: E731
    nq = lambda xs: "/".join(str(x) for x in xs)  # noqa: E731
    surv: dict[str, list[dict]] = {}
    for sym in MARKETS:
        print(f"\n== {sym} ==")
        for r in sorted((x for x in res if x["sym"] == sym),
                        key=lambda x: (-x["live"], -x["ann"])):
            flag = "LIVE" if r["live"] else "dead"
            print(f"{r['cell']} reg{r['regime']} {r['exit']:6s} n={r['n']:3d} "
                  f"nq=({nq(r['nq'])}) ann={r['ann']:+7.2f}% net$={r['net']:+9,.2f} "
                  f"q$=({q(r['q'])}) [a={r['ok_a']} b={r['ok_b']} c={r['ok_c']}] {flag}")
            if r["live"]:
                surv.setdefault(sym, []).append(r)
    total_live = sum(len(v) for v in surv.values())
    print(f"\nИТОГ: живых клеток {total_live} по рынкам: "
          + (", ".join(f"{s}={len(v)}" for s, v in surv.items()) if surv else "ни одной"))
    if not total_live:
        print("ВЕРДИКТ-КАНДАТ: 0 живых => Series V закрывается на freeze-стадии (V-7 не вооружается)")
    else:
        print("ВЕРДИКТ-КАНДАТ: живые есть => V-7 one-shot на SOL по §5 (клетки: только живые SOL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
