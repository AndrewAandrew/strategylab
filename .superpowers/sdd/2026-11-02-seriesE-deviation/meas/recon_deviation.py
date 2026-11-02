# -*- coding: utf-8 -*-
"""Разведка №2 по методу канала profit_cryptoss: ЛОЖНЫЙ ПРОБОЙ зоны + объём (VSA-фильтр).

QA-минипроба (read-only, float64), НЕ серия и НЕ зонд E (бюджет не трогает).
Его дословные тезисы, которые проверяем:
  - «Боковик — лучше торговать только ложный пробой» (уровень = горизонтальная зона);
  - «девиация = ложный пробой диапазона... отлично работает и на тренде и в боковике»;
  - база системы — VSA: кульминация = бар с АНОМАЛЬНЫМ объёмом и wide-spread.
Отличие от мёртвых осей реестра: sweep_liquidity (серия A) — тень 1m-скальпа против
стопов на 1m-таймфрейме; spec_08 — объёмный фейд без контекста уровня; здесь —
пробой-и-возврат зоны на 1h-барах, холд 24–48h, низкий оборот.

Механика разведки:
  зоны — кластеры 1h-пивотов w=5, допуск 0.4%, >=4 пивотов (180 дней, как recon №1);
  событие support-deviation: low < L·(1−d), закрытие того же бара > L·(1+0.1%);
  зеркально resistance: high > R·(1+d) и close < R·(1−0.1%);
  фильтр объёма: объём бара события >= k×медианы (k ∈ {1,2}), wide spread: |close−open| >= 0.6×диапазона;
  реакция: forward return через h часов (24/48), signed по направлению отбоя;
  трение честного круга taker+slip = 0.22%.
Порог вывода: годный кандидат = медиана реакции − 0.22% > 0 УСТОЙЧИВО (оба k, обе глубины)
или с явным объёмным дифференциалом (k=2 лучше k=1) — тогда ось идёт в DRAFT.
"""
import sqlite3
from pathlib import Path
from statistics import median

REPO = Path(r"C:/Users/egoro/Documents/GitHub/strategylab")
END = 1790294400  # конец эпохи fit; holdout ts >= 1790294400 не читается (R-next-2)
START = END - 180 * 86400
RT = 0.0022
W = 5
CLUSTER = 0.004
MIN_PIV = 4
DEPTHS = [0.003, 0.005, 0.010]
VOL_K = [1.0, 2.0]
HOR = [24, 48]  # часов


def hourly(db, sym):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars_1m"
        " WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts", (sym, START, END)).fetchall()
    conn.close()
    b = {}
    for ts, o, h, l, c, v in rows:
        b.setdefault((ts // 3600) * 3600, []).append((float(o), float(h), float(l), float(c), float(v)))
    return [(hr, min(x[2] for x in v), max(x[1] for x in v), v[0][0], v[-1][3], sum(x[4] for x in v))
            for hr, v in sorted(b.items())]  # (ts, low, high, open, close, vol)


def run(db, sym):
    bars = hourly(db, sym)
    n = len(bars)
    piv = []
    for i in range(W, n - W):
        if all(bars[i][1] < bars[j][1] for j in range(i - W, i + W + 1) if j != i):
            piv.append((i, bars[i][1], "S"))
        if all(bars[i][2] > bars[j][2] for j in range(i - W, i + W + 1) if j != i):
            piv.append((i, bars[i][2], "R"))
    volmed = median(x[5] for x in bars)

    def zones(kind):
        pts = sorted([p for _, p, k in piv if k == kind])
        cl = []
        for p in pts:
            if cl and p <= cl[-1][-1] * (1 + CLUSTER):
                cl[-1].append(p)
            else:
                cl.append([p])
        return [median(c) for c in cl if len(c) >= MIN_PIV]

    print(f"\n# {sym}: {n} 1h-баров, зон S={len(zones('S'))}, R={len(zones('R'))}")
    print("| сторона | глубина | vol-фильтр | событий | медиана 24h | медиана 48h | win% (24h) | медиана−RT(24h) |")
    rows = []
    for kind, zs in (("support", zones("S")), ("resistance", zones("R"))):
        for d in DEPTHS:
            for k in VOL_K:
                f24, f48 = [], []
                last = -999
                for i in range(n):
                    ts, lo, hi, op, c, v = bars[i]
                    for L in zs:
                        if kind == "support":
                            ev = lo < L * (1 - d) and c > L * 1.001
                        else:
                            ev = hi > L * (1 + d) and c < L * 0.999
                        if not ev or i - last < 6 or i + 48 >= n:
                            continue
                        rng = max(hi - lo, 1e-9)
                        wide = abs(c - op) >= 0.6 * rng
                        if v < k * volmed or not wide:
                            continue
                        last = i
                        r24 = bars[i + 24][4] / c - 1
                        r48 = bars[i + 48][4] / c - 1
                        sgn = 1 if kind == "support" else -1
                        f24.append(sgn * r24)
                        f48.append(sgn * r48)
                if len(f24) < 5:
                    rows.append((kind, d, k, len(f24), None, None, None, None))
                    continue
                m24, m48 = median(f24), median(f48)
                win = 100 * sum(1 for x in f24 if x > 0) / len(f24)
                rows.append((kind, d, k, len(f24), m24, m48, win, m24 - RT))
    for kind, d, k, cnt, m24, m48, win, net in rows:
        if m24 is None:
            print(f"| {kind} | {d:.3f} | {k:.0f}× | {cnt} | мало событий | — | — | — |")
        else:
            print(f"| {kind} | {d:.3f} | {k:.0f}× | {cnt} | {100 * m24:+.2f}% | {100 * m48:+.2f}% "
                  f"| {win:.0f}% | {100 * net:+.2f}% |")


for sym, db in (("BTCUSDT", REPO / "data" / "btcusdt.db"), ("ETHUSDT", REPO / "data" / "ethusdt.db")):
    run(str(db), sym)
print("\nпорог честности: net = медиана 24h − 0.22% (круг taker). Событий/180д при ≤25/год — низкий оборот.")
