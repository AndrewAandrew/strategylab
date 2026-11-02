"""Аудит ведущего Series B carry, часть 2: пересчёт журнала строка-в-строку из
СЫРЫХ данных (bars_1m + funding_8h) по формулам замороженной спеки B-1.1 — без
вызовов strategylab.carry; последовательность держаний реконструируется из
funding-ряда независимо; плюс диагностика t20 (почему flat) и bootstrap B2-4.

Совпадение с артефактом = арифметика и привязка ко времени верны. Правила
конвенции пересобираются из текста спеки (B-1.1 п.1–4), а не импортом кода.
"""

from __future__ import annotations

import bisect
import csv
import random
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path("C:/Users/egoro/Documents/GitHub/strategylab")
sys.path.insert(0, str(REPO))
from strategylab.data.schema import connect  # noqa: E402

CALIB = REPO / "data" / "calib"
CELLS = {
    "t05": (Decimal("0.00005"), Decimal("0.00003")),
    "t10": (Decimal("0.00010"), Decimal("0.00005")),
    "t20": (Decimal("0.00020"), Decimal("0.00010")),
}
SYMBOLS = {"btc": ("BTCUSDT", "btcusdt"), "eth": ("ETHUSDT", "ethusdt")}
FOLDS = {"f1": (1761523200, 1771113600), "f2": (1771113600, 1780704000),
         "f3": (1780704000, 1790294400)}
TAKER = Decimal("0.0006")
SLIP = Decimal("0.0005")
CAPITAL = Decimal("10000")
ONE = Decimal(1)


def load(conn, symbol: str, s: int, e: int):
    bars = [
        (int(r[0]), r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT ts, open, high, close FROM bars_1m"
            " WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts", (symbol, s, e))
    ]
    settles = [
        (int(r[0]), r[1])
        for r in conn.execute(
            "SELECT ts, rate FROM funding_8h"
            " WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts", (symbol, s, e))
    ]
    return bars, settles


def reconstruct(bars, settles, theta_e, theta_x):
    """Держания по B-1.1: решение в такт, fill = open первого бара ts > такта."""
    bts = [b[0] for b in bars]
    holds = []
    cur = None
    for ts, rate in settles:
        nxt_i = bisect.bisect_right(bts, ts)
        if nxt_i >= len(bars):
            continue
        bar = bars[nxt_i]
        if cur is None:
            if rate >= theta_e:
                cur = (bar[0], bar[1], bar[0], bar[1])  # entry_ts, entry_ref, (выход позже)
        else:
            if rate < theta_x:
                cur = (cur[0], cur[1], bar[0], bar[1])
                holds.append(cur)
                cur = None
    if cur is not None:
        last = bars[-1]
        if last[0] > cur[0]:
            holds.append((cur[0], cur[1], last[0], last[1]))
        else:
            holds.append(cur)
    return holds


def main() -> None:  # noqa: C901
    problems: list[str] = []
    econ: list[str] = []
    daily: dict[str, dict[int, Decimal]] = {}
    for tc, (te, tx) in CELLS.items():
        for sc, (symbol, dbname) in SYMBOLS.items():
            conn = connect(REPO / "data" / f"{dbname}.db")
            cell_deltas: dict[int, Decimal] = {}
            try:
                for fold, (s, e) in FOLDS.items():
                    bars, settles = load(conn, symbol, s, e)
                    stem = f"carry_{symbol}_{s}_{e}"
                    jp = CALIB / f"rB_{tc}_{sc}_{fold}" / f"{stem}_journal_p.csv"
                    rows = list(csv.DictReader(jp.read_text(encoding="utf-8").splitlines()))

                    holds = reconstruct(bars, settles, te, tx)
                    # ожидаемый журнал
                    expect: list[tuple] = []
                    bts = [b[0] for b in bars]
                    sts = [t for t, _ in settles]
                    srate = dict(settles)
                    gross = Decimal(0)
                    fric = Decimal(0)
                    for (ets, eref, xts, xref) in holds:
                        qty = CAPITAL / eref
                        ef = eref * (ONE - SLIP)
                        xc = xref * (ONE + SLIP)
                        ecost = TAKER * qty * ef + qty * (eref - ef)
                        xcost = TAKER * qty * xc + qty * (xc - xref)
                        expect.append((ets, "entry", ef, qty, -ecost))
                        fric += ecost
                        a = bisect.bisect_right(sts, ets)
                        b = bisect.bisect_left(sts, xts)
                        for ts_j in sts[a:b]:
                            k = bisect.bisect_right(bts, ts_j) - 1
                            mark = bars[k][3]
                            notional = qty * mark
                            delta = srate[ts_j] * notional
                            expect.append((ts_j, "settle", None, qty, delta))
                            gross += delta
                        expect.append((xts, "exit", xc, qty, -xcost))
                        fric += xcost

                    if len(expect) != len(rows):
                        problems.append(f"{tc}_{sc}_{fold}: строк {len(rows)} != {len(expect)}")
                        continue
                    for exp, got in zip(expect, rows):
                        ets, kind, fill, qty, delta = exp
                        if int(got["ts"]) != ets or got["kind"] != kind:
                            problems.append(f"{tc}_{sc}_{fold}: ts/kind {got['ts']}/{got['kind']}"
                                            f" != {ets}/{kind}")
                            break
                        if fill is not None and Decimal(got["fill"]) != fill:
                            problems.append(f"{tc}_{sc}_{fold}: fill {got['fill']} != {fill}")
                        if Decimal(got["qty"]) != qty:
                            problems.append(f"{tc}_{sc}_{fold}: qty {got['qty']} != {qty}")
                        if abs(Decimal(got["cash_delta"]) - delta) > Decimal("1e-24"):
                            problems.append(f"{tc}_{sc}_{fold}: delta {got['cash_delta']} != {delta}")
                        if kind == "settle":
                            cell_deltas[int(ets) // 86400] = \
                                cell_deltas.get(int(ets) // 86400, Decimal(0)) + delta
                    for r in rows:
                        if r["kind"] != "settle":
                            cell_deltas[int(r["ts"]) // 86400] = \
                                cell_deltas.get(int(r["ts"]) // 86400, Decimal(0)) \
                                + Decimal(r["cash_delta"])

                    if tc == "t20":
                        mx = max((rt for _, rt in settles), default=None)
                        cnt = sum(1 for _, rt in settles if rt >= te)
                        econ.append(f"t20_{sc}_{fold}: settlements={len(settles)}"
                                    f" max_rate={mx} >=theta_entry={cnt}")
                    else:
                        econ.append(f"{tc}_{sc}_{fold}: holds={len(holds)}"
                                    f" gross={gross:.2f}$ fric={fric:.2f}$"
                                    f" fric/hold={fric / max(len(holds), 1):.2f}$"
                                    f" duty={sum(1 for _ in holds)},{gross:.2f}")
            finally:
                conn.close()
            daily[f"{tc}_{sc}"] = cell_deltas

    print(f"РАСХОЖДЕНИЙ С АРТЕФЕКТАМИ: {len(problems)}")
    for p in problems[:40]:
        print("  FAIL", p)
    print()
    for line in econ:
        print(line)

    # B2-4 bootstrap: блоки 30 дней, 1000 ресэмплов, seed=42, 2.5-й перцентиль годовой
    print("\nB2-4 bootstrap (30d blocks, seed=42, n=1000, 95% CI lower annualized %):")
    for cell, cds in daily.items():
        if cell.startswith("t20"):
            print(f"  {cell}: не считается — плоская клетка (DEAD по C)")
            continue
        days = (FOLDS["f3"][1] - FOLDS["f1"][0]) // 86400
        series = [float(cds.get(FOLDS["f1"][0] // 86400 + i, 0)) for i in range(days)]
        blocks = [series[i:i + 30] for i in range(0, days - 29, 30)]  # 10 полных блоков
        flat = [sum(b) for b in blocks]
        rng = random.Random(42)
        angs = sorted(
            sum(rng.choices(flat, k=len(flat))) / (len(flat) * 30) * 365 / 10000 * 100
            for _ in range(1000)
        )
        print(f"  {cell}: lower={angs[25]:.3f}%/yr upper={angs[974]:.3f}%/yr"
              f" -> {'PASS' if angs[25] > 0 else 'FAIL'}")


if __name__ == "__main__":
    main()
