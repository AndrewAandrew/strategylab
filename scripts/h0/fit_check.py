# -*- coding: utf-8 -*-
"""H-0 fit-check: сверка конвенций детектора с замороженной r1-таблицей M8.

НЕ часть боевой машины: читает ТОЛЬКО r1 fit-окно [1761523200, 1790294400)
(съеденная эпоха-происхождение, assert len(hourly)==7992 как у r1). Форвард
[1793577600,..) не затрагивается: любой выход за границы окна — отказ rc=3.

Зачем: закон §1 переносит объект «дословно из r1-замера», а r1-методика
(письмо 2026-10-29, строка 26) определяет SMA20(vol) как среднее ПРЕДЫДУЩИХ
20 баров (t−20..t−1), без текущего. Боевой детектор трактует окно по-другому.
Скрипт меряет обе трактовки и сверяет с таблицей r1 (ETH z3r3: n=97, 1.214 %
баров, up/down 49/48; k6 mean +0.4168 %, k12 +0.3598 %; фолды k12 f1 +0.6896
(37) / f2 +0.2744 (27) / f3 +0.0600 (33)). Что совпадает с r1 — то и есть
дословная конвенция закона.

Заодно рисует SVG-карту событий (цена + маркеры кульминаций, объём с SMA20,
ADX с порогом 25) — визуальная самопроверка «похоже ли на кульминации».
"""
from __future__ import annotations

import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from strategylab.core.bars import Bar, Interval  # noqa: E402
from strategylab.data.aggregate import aggregate  # noqa: E402
from strategylab.data.schema import _decimal_or_text  # noqa: E402
from strategylab.data.store import CandleStore  # noqa: E402
from strategylab.strategy.indicators import Adx, Atr  # noqa: E402

FIT_START = 1761523200
FIT_END = 1790294400  # r1 fit-окно; >= этого — holdout/форвард, не читаем
FOLD2, FOLD3 = 1771113600, 1780704000
Z, R = Decimal(3), Decimal(3)
DB = REPO / "data" / "ethusdt.db"
SVG_PATH = Path("C:/Users/egoro/AppData/Local/Temp/opencode/h0_fit_chart.svg")


def load_hourly() -> list[Bar]:
    if FIT_END > 1790294400 or FIT_START < 1761523200:
        print("FIT-GUARD: окно вне r1 fit-эпохи — отказ")
        raise SystemExit(3)
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        conn.text_factory = _decimal_or_text
        minutes = list(CandleStore(conn, "ETHUSDT").read_1m("ETHUSDT", FIT_START, FIT_END))
    finally:
        conn.close()
    hourly = aggregate(minutes, Interval.HOUR)
    assert len(hourly) == 7992, f"r1 assert: ожидали 7992, получили {len(hourly)}"
    return hourly


def events(bars: list[Bar], sma_includes_event_bar: bool) -> list[int]:
    """M8.1 z3r3 без ADX-гейта (таблица r1 гейт не имеет)."""
    atr, adx = Atr(14), Adx(14)
    vols: list[Decimal] = []
    out: list[int] = []
    for i, b in enumerate(bars):
        a = atr.update(b.high, b.low, b.close)
        adx.update(b.high, b.low, b.close)
        if a is None:
            vols.append(b.volume)
            continue
        if sma_includes_event_bar:
            vols.append(b.volume)
            if len(vols) > 20:
                vols.pop(0)
            if len(vols) < 20:
                continue
            sma = sum(vols, Decimal(0)) / 20
        else:  # r1: среднее предыдущих 20 (t−20..t−1), доступно с индекса 20
            if len(vols) < 20:
                vols.append(b.volume)
                continue
            sma = sum(vols[-20:], Decimal(0)) / 20
            vols.append(b.volume)
        if b.volume > Z * sma and b.high - b.low > R * a:
            out.append(i)
    return out


def r1_m82(bars: list[Bar], idxs: list[int], k: int) -> dict:
    """r1 M8.2 cost-free: вход close(t+1), выход close(t+1+k), знак фейда."""
    vals = []
    for i in idxs:
        e, x = i + 1, i + 1 + k
        if x >= len(bars):
            continue
        entry = bars[e].close
        up = bars[i].close > bars[i].open
        pnl = (entry - bars[x].close) / entry if up else (bars[x].close - entry) / entry
        vals.append(pnl)
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    n = len(s)
    med = (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)
    mean = sum(s, Decimal(0)) / n
    return {"n": n, "mean": mean, "median": med, "pos": sum(1 for v in s if v > 0) / n}


def fold_means(bars: list[Bar], idxs: list[int], k: int) -> list[tuple[str, int, Decimal]]:
    edges = [("f1", FIT_START, FOLD2), ("f2", FOLD2, FOLD3), ("f3", FOLD3, FIT_END)]
    out = []
    for name, a, b in edges:
        vals = []
        for i in idxs:
            e, x = i + 1, i + 1 + k
            if x >= len(bars) or not (a <= bars[i].ts < b):
                continue
            entry = bars[e].close
            up = bars[i].close > bars[i].open
            vals.append((entry - bars[x].close) / entry if up else (bars[x].close - entry) / entry)
        m = sum(vals, Decimal(0)) / len(vals) if vals else Decimal("NaN")
        out.append((name, len(vals), m))
    return out


def pct(x: Decimal | None) -> str:
    if x is None or x != x:  # NaN
        return "  --   "
    return f"{x * 100:+.4f} %"


def draw_svg(bars: list[Bar], idxs: list[int], sma_excl: list[Decimal | None]) -> None:
    """Три фолд-панели: цена с маркерами, объём с SMA20, ADX с порогом 25."""
    atr, adx = Atr(14), Adx(14)
    adx_series: list[Decimal | None] = []
    for b in bars:
        atr.update(b.high, b.low, b.close)
        adx_series.append(adx.update(b.high, b.low, b.close))
    panels = [(FIT_START, FOLD2), (FOLD2, FOLD3), (FOLD3, FIT_END)]
    W = 1500
    # полосы внутри панели: цена 20..98, объём 110..170, ADX 180..240; панель 250px
    P_H, PRICE_TOP, PRICE_H, VOL_BASE, VOL_H, ADX_BASE, ADX_H = 250, 20, 78, 170, 60, 240, 60
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{P_H * 3 + 30}" viewBox="0 0 {W} {P_H * 3 + 30}" font-family="monospace" font-size="10">',
           f'<rect width="100%" height="100%" fill="#111"/>',
           f'<text x="8" y="14" fill="#eee">H-0 fit-check ETHUSDT 1h r1-окно [1761523200,1790294400) — z3r3 кульминации (r1-SMA): '
           f'красный ▼ над пиком = ап-кульминация (шорт-фейд), зелёный ▲ под впадиной = даун-кульминация (лонг-фейд)</text>']
    y0 = 22
    for (fa, fb) in panels:
        rng = [i for i, b in enumerate(bars) if fa <= b.ts < fb]
        lo_i, hi_i = rng[0], rng[-1]
        cs = [bars[i].close for i in range(lo_i, hi_i + 1)]
        vmin, vmax = min(cs), max(cs)
        pad = (vmax - vmin) * Decimal("0.05") + 1
        vmin, vmax = vmin - pad, vmax + pad
        vols = [bars[i].volume for i in range(lo_i, hi_i + 1)]
        vmaxv = max(vols)
        n = hi_i - lo_i + 1
        sx = W / n
        def X(i): return (i - lo_i) * sx
        def Yc(c): return PRICE_TOP + PRICE_H - float((c - vmin) / (vmax - vmin) * PRICE_H)
        pts = " ".join(f"{X(i):.1f},{Yc(bars[i].close):.1f}" for i in range(lo_i, hi_i + 1))
        svg.append(f'<g transform="translate(0,{y0})">')
        svg.append(f'<text x="4" y="12" fill="#8aa">[{fa}..{fb}) close {vmin:.0f}..{vmax:.0f}</text>')
        svg.append(f'<polyline points="{pts}" fill="none" stroke="#7ec8e3" stroke-width="0.8"/>')
        for i in idxs:
            if not (lo_i <= i <= hi_i):
                continue
            up = bars[i].close > bars[i].open
            if up:
                x, y = X(i), Yc(bars[i].high) - 3
                svg.append(f'<path d="M{x:.1f},{y:.1f} l4,-8 l-8,0 z" fill="#e05c5c" opacity="0.9"/>')
            else:
                x, y = X(i), Yc(bars[i].low) + 3
                svg.append(f'<path d="M{x:.1f},{y:.1f} l4,8 l-8,0 z" fill="#5ce07a" opacity="0.9"/>')
        # объём: базовая линия VOL_BASE, столбики вверх до VOL_H
        for i in range(lo_i, hi_i + 1):
            h = float(bars[i].volume / vmaxv * VOL_H)
            col = "#e0b04c" if i in idxs else "#3a4a55"
            svg.append(f'<rect x="{X(i):.1f}" y="{VOL_BASE - h:.1f}" width="{max(sx * 0.8, 0.4):.1f}" height="{h:.1f}" fill="{col}"/>')
        sma_pts = []
        for i in range(lo_i, hi_i + 1):
            s = sma_excl[i]
            if s is not None:
                sma_pts.append(f"{X(i):.1f},{VOL_BASE - float(s / vmaxv * VOL_H):.1f}")
        svg.append(f'<polyline points="{" ".join(sma_pts)}" fill="none" stroke="#e0e0e0" stroke-width="0.7"/>')
        # ADX: базовая 0 на ADX_BASE, 100 на ADX_BASE-ADX_H
        apts = []
        for i in range(lo_i, hi_i + 1):
            a = adx_series[i]
            if a is None:
                continue
            apts.append(f"{X(i):.1f},{ADX_BASE - float(a / 100 * ADX_H):.1f}")
        svg.append(f'<line x1="0" y1="{ADX_BASE - 25 / 100 * ADX_H:.1f}" x2="{W}" y2="{ADX_BASE - 25 / 100 * ADX_H:.1f}" stroke="#c05050" stroke-dasharray="4 3"/>')
        svg.append(f'<polyline points="{" ".join(apts)}" fill="none" stroke="#b08ae0" stroke-width="0.8"/>')
        svg.append("</g>")
        y0 += P_H
    svg.append("</svg>")
    SVG_PATH.write_text("\n".join(svg), encoding="utf-8")
    print(f"SVG: {SVG_PATH}")


def main() -> int:
    bars = load_hourly()
    for label, incl in (("INCL (боевой run.py)", True), ("EXCL (r1-методика)", False)):
        idxs = events(bars, sma_includes_event_bar=incl)
        up = sum(1 for i in idxs if bars[i].close > bars[i].open)
        print(f"\n=== SMA {label} ===")
        print(f"M8.1 z3r3: n={len(idxs)} ({Decimal(len(idxs)) / len(bars) * 100:.3f} % баров), up/down {up}/{len(idxs) - up}")
        for k in (6, 12):
            m = r1_m82(bars, idxs, k)
            if m["n"]:
                print(f"M8.2 k={k}: n={m['n']} mean={pct(m['mean'])} median={pct(m['median'])} >0={m['pos'] * 100:.2f} %")
        print("фолды k=12:", " | ".join(f"{name} {pct(mean_)} (n={n})" for name, n, mean_ in fold_means(bars, idxs, k=12)))
    # SMA20(excl) для графика
    atr, adx = Atr(14), Adx(14)
    vols: list[Decimal] = []
    sma_excl: list[Decimal | None] = []
    for b in bars:
        atr.update(b.high, b.low, b.close)
        adx.update(b.high, b.low, b.close)
        sma_excl.append(sum(vols[-20:], Decimal(0)) / 20 if len(vols) >= 20 else None)
        vols.append(b.volume)
    idxs_r1 = events(bars, sma_includes_event_bar=False)
    draw_svg(bars, idxs_r1, sma_excl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
