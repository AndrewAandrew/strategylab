# -*- coding: utf-8 -*-
"""Recon №2 по фолдам: ложный пробой зоны — режимно-устойчив или одна треть эпохи делает цифры?

QA-уровень (float64, read-only). Три 60-дневных фолда последних 180 дней.
Клетки-кандидаты: support-deviation->long BTC d∈{0.003,0.005,0.010} vol>=2x;
resistance-deviation->short d=0.010 оба символа; плюс support ETH для контраста.
"""
import sqlite3
from pathlib import Path
from statistics import median

REPO = Path(r"C:/Users/egoro/Documents/GitHub/strategylab")
EPOCH_END = 1790294400
RT = 0.0022
W, CLUSTER, MIN_PIV = 5, 0.004, 4
FOLDS = [(f"q{i}", EPOCH_END - (4 - i) * 60 * 86400, EPOCH_END - (3 - i) * 60 * 86400)
         for i in (1, 2, 3)]


def hourly(db, sym, start, end):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars_1m"
        " WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts", (sym, start, end)).fetchall()
    conn.close()
    b = {}
    for ts, o, h, l, c, v in rows:
        b.setdefault((ts // 3600) * 3600, []).append((float(o), float(h), float(l), float(c), float(v)))
    return [(hr, min(x[2] for x in v), max(x[1] for x in v), v[0][0], v[-1][3], sum(x[4] for x in v))
            for hr, v in sorted(b.items())]


def cell(bars, kind, d, kvol):
    n = len(bars)
    lows, highs = [], []
    for i in range(W, n - W):
        if all(bars[i][1] < bars[j][1] for j in range(i - W, i + W + 1) if j != i):
            lows.append(bars[i][1])
        if all(bars[i][2] > bars[j][2] for j in range(i - W, i + W + 1) if j != i):
            highs.append(bars[i][2])
    def cluster_med(pts):
        pts = sorted(pts)
        cl = []
        for p in pts:
            if cl and p <= cl[-1][-1] * (1 + CLUSTER):
                cl[-1].append(p)
            else:
                cl.append([p])
        return [median(c) for c in cl if len(c) >= MIN_PIV]
    zs = {"support": cluster_med(lows), "resistance": cluster_med(highs)}
    volmed = median(x[5] for x in bars)
    out = []
    last = -999
    for i in range(n):
        ts, lo, hi, op, c, v = bars[i]
        if i + 24 >= n or i - last < 6:
            continue
        for L in zs[kind]:
            if kind == "support":
                ev = lo < L * (1 - d) and c > L * 1.001
                sgn = 1
            else:
                ev = hi > L * (1 + d) and c < L * 0.999
                sgn = -1
            if not ev:
                continue
            rng = max(hi - lo, 1e-9)
            if v < kvol * volmed or abs(c - op) < 0.6 * rng:
                continue
            last = i
            out.append(sgn * (bars[i + 24][4] / c - 1))
            break
    return out


def show(sym, kind, d, kvol):
    db = str(REPO / "data" / ("btcusdt.db" if sym == "BTCUSDT" else "ethusdt.db"))
    line = [f"{sym} {kind} d={d:.3f} vol>={kvol:.0f}x:"]
    for label, s, e in FOLDS:
        rs = cell(hourly(db, sym, s, min(e, EPOCH_END)), kind, d, kvol)
        if len(rs) < 4:
            line.append(f"{label}: n={len(rs)} мало")
        else:
            line.append(f"{label}: n={len(rs)} med={100 * median(rs):+.2f}% net={100 * (median(rs) - RT):+.2f}%")
    print("  |  ".join(line))


for spec in (("BTCUSDT", "support", 0.003, 2.0), ("BTCUSDT", "support", 0.005, 2.0),
             ("BTCUSDT", "support", 0.010, 2.0), ("BTCUSDT", "resistance", 0.010, 2.0),
             ("ETHUSDT", "support", 0.005, 2.0), ("ETHUSDT", "resistance", 0.010, 2.0)):
    show(*spec)
