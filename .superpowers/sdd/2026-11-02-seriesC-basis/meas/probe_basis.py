# -*- coding: utf-8 -*-
"""C-0 read-only зонд basis (перп/спот Bybit) — ПРЕЖДЕ заморозки порогов Series C.

Урок t20 из Series B: порог выше эпохального потолка = неработающая клетка.
Поэтому пороги excursions замороживаются ПОСЛЕ этого зонда, а не до.

Определения:
  b(t) = close_perp(t)/close_spot(t) − 1   по общей минутной сетке одного venue;
  эпика = максимальный отрезок подряд |b| >= θ;
  cross-симул: вход на тике close, где |b| пересёк θ вверх, выход на первом
  тике |b| <= θ_exit (= θ/2). gross = |b_входа| − |b_выхода| (доля нотинала).
  carry = сумма funding rate (Binance-поток funding_8h, конвенция B-1.1) по
  расчёткам строго внутри холда (entry_ts, exit_ts]; знак: вход при b>0 =
  шорт перпа → carry причитается, вход при b<0 = нужен шорт спота (borrow) —
  считаем отдельно, «торговать вслепую» нельзя.
  friction = 4 ноги × (taker 0.0006 + slip 0.0005) = 0.0044 нотиала за раунд.

Точность: статистики зонда — float64 (QA-уровень, не учёт); бэзис считается в
Decimal до перехода к float на перцентилях. Базы открываются mode=ro —
зонд физически не может ничего испортить.

Правило pre-code no-go (договорённость с владельцем): медианный честный gross
cross-симула < 2× friction (≈0.88%) → Series C НЕ пишется, владельцу
докладывается до кода симуляции.

Запуск:
  python .superpowers/sdd/2026-11-02-seriesC-basis/meas/probe_basis.py
"""

from __future__ import annotations

import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # meas → series-dir → sdd → .superpowers → репо
DBS = {"BTCUSDT": REPO / "data" / "btcusdt.db", "ETHUSDT": REPO / "data" / "ethusdt.db"}
FOLDS = [("f1", 1761523200, 1771113600), ("f2", 1771113600, 1780704000), ("f3", 1780704000, 1790294400)]
THRESHOLDS = [Decimal("0.0002"), Decimal("0.0005"), Decimal("0.001"), Decimal("0.002"), Decimal("0.005")]
LEG_COST = Decimal("0.0011")  # taker .0006 + slip .0005 на ногу
RT_COST = LEG_COST * 4  # спот-нога + перп-нога, вход и выход
BREAKEVEN_GROSS = 2 * float(RT_COST)  # pre-code no-go: медианный gross ниже — STOP
PRIMARY_TH = Decimal("0.0005")


def pctl(vals: list[float], p: float) -> float:
    """Float64 перцентиль с линейной интерполяцией — только для QA-отчёта."""
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def fmt5(vals: list[float]) -> str:
    if not vals:
        return "—"
    return f"n={len(vals)} p50={pctl(vals, 0.5):.5f} p90={pctl(vals, 0.9):.5f} макс={max(vals):.4f}"


def load_series(
    db: Path, symbol: str, start: int, end: int
) -> tuple[list[tuple[int, Decimal]], list[tuple[int, Decimal]], dict[str, int]]:
    """(общая 1m-сетка [(ts,b)], funding [(ts,rate)], счётчики покрытия)."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        perp = dict(conn.execute(
            "SELECT ts, close FROM bars_1m WHERE symbol=? AND ts>=? AND ts<?", (symbol, start, end)
        ))
        spot = dict(conn.execute(
            "SELECT ts, close FROM spot_1m WHERE symbol=? AND ts>=? AND ts<?", (symbol, start, end)
        ))
        funding_rows = conn.execute(
            "SELECT ts, rate FROM funding_8h WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
            (symbol, start, end),
        ).fetchall()
    finally:
        conn.close()
    common = sorted(set(perp) & set(spot))
    series = [(t, Decimal(perp[t]) / Decimal(spot[t]) - Decimal(1)) for t in common]
    gaps = sum(1 for a, b in zip(series, series[1:], strict=False) if b[0] - a[0] != 60)
    counters = {
        "перп": len(perp), "спот": len(spot), "общих": len(series), "гэпов": gaps,
        "funding": len(funding_rows),
        "покрытие%": round(100 * len(series) / max(len(perp), len(spot), 1), 2),
    }
    funding = [(t, Decimal(r)) for t, r in funding_rows]
    return series, funding, counters


def episodes(series: list[tuple[int, Decimal]], theta: Decimal) -> list[list[tuple[int, Decimal]]]:
    """Максимальные отрезки подряд с |b| >= theta по наличным тикам (разрыв>60с рвёт эпику)."""
    eps: list[list[tuple[int, Decimal]]] = []
    cur: list[tuple[int, Decimal]] = []
    prev_ts: int | None = None
    for ts, b in series:
        contiguous = prev_ts is not None and ts - prev_ts == 60
        if abs(b) >= theta and (cur and contiguous):
            cur.append((ts, b))
        elif abs(b) >= theta:
            if cur:
                eps.append(cur)
            cur = [(ts, b)]
        elif cur:
            eps.append(cur)
            cur = []
        prev_ts = ts
    if cur:
        eps.append(cur)
    return eps


def cross_trades(
    series: list[tuple[int, Decimal]],
    funding: list[tuple[int, Decimal]],
    theta: Decimal,
) -> list[dict[str, float | int | str]]:
    """Честный вход-на-пересечении / выход при |b|<=theta/2; gross и carry ДО friction."""
    theta_exit = theta / 2
    trades: list[dict[str, float | int | str]] = []
    entry: tuple[int, Decimal] | None = None
    for ts, b in series:
        ab = abs(b)
        if entry is None:
            if ab >= theta:
                entry = (ts, b)
            continue
        if ab <= theta_exit:
            gross = abs(entry[1]) - ab
            carry = sum((r for t, r in funding if entry[0] < t <= ts), Decimal(0))
            signed_carry = carry if entry[1] > 0 else -carry  # шорт перпа получает при r>0
            trades.append({
                "entry_ts": entry[0],
                "exit_ts": ts,
                "hold_min": (ts - entry[0]) // 60,
                "side": "perp_short_spot_long" if entry[1] > 0 else "NEEDS_SPOT_BORROW",
                "gross": float(gross),
                "carry": float(signed_carry),
                "net": float(gross + signed_carry - RT_COST),
            })
            entry = None
    return trades


def probe_fold(series: list[tuple[int, Decimal]], funding: list[tuple[int, Decimal]], label: str, days: float) -> None:
    bs = [float(b) for _, b in series]
    absbs = [abs(x) for x in bs]
    print(f"\n### {label}: {len(series)} тиков за {days:.0f} дн\n")
    qs = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
    print("| метрика | значения |")
    print("|---|---|")
    print(f"| b, перцентили | " + " ".join(f"p{int(q * 100)}={pctl(bs, q):+.6f}" for q in qs)
          + f" min={min(bs):+.6f} max={max(bs):+.6f} |")
    print(f"| \\|b\\| | p50={pctl(absbs, 0.5):.6f} p95={pctl(absbs, 0.95):.6f} "
          f"p99={pctl(absbs, 0.99):.6f} max={max(absbs):.6f} |")
    print(f"| доля тиков по theta | " + " ".join(
        f"{float(th):.4f}:{100 * sum(1 for x in absbs if x >= th) / len(absbs):.3f}%"
        for th in THRESHOLDS) + " |")
    cache = {th: (episodes(series, th), cross_trades(series, funding, th)) for th in THRESHOLDS}
    print("\n| theta | |эпик| | длительность, мин | peak \\|b\\| | сделок | gross p50 | carry p50 | net p50 | net>0 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for th in THRESHOLDS:
        eps, trades = cache[th]
        peaks = [float(max(abs(p[1]) for p in e)) for e in eps]
        durs = [float(e[-1][0] - e[0][0]) / 60 for e in eps]
        grosses = [t["gross"] for t in trades] if isinstance(trades, list) else []
        carries = [t["carry"] for t in trades]
        nets = [t["net"] for t in trades]
        wins = 100 * sum(1 for t in trades if float(t["net"]) > 0) / len(trades) if trades else float("nan")
        print(f"| {float(th):.4f} | n={len(eps)} | {fmt5(durs)} | {fmt5(peaks)} | {len(trades)} | "
              f"{pctl(grosses, 0.5):.5f} | {pctl(carries, 0.5):+.6f} | {pctl(nets, 0.5):+.5f} | "
              f"{wins:.1f}% |")
    primary = cache[PRIMARY_TH][1]
    sides: dict[str, int] = {}
    for t in primary:
        sides[str(t["side"])] = sides.get(str(t["side"]), 0) + 1
    print(f"\n- стороны событий (θ={float(PRIMARY_TH):.4f}): {sides or 'нет'}; "
          f"событий/сутки: {len(primary) / days:.2f}")


def probe_symbol(db: Path, symbol: str) -> None:
    print(f"\n# {symbol}\n")
    for fold, start, end in FOLDS:
        series, funding, c = load_series(db, symbol, start, end)
        print(f"- покрытие {fold}: перп={c['перп']} спот={c['спот']} общих={c['общих']} "
              f"гэпов={c['гэпов']} funding={c['funding']} пересечение={c['покрытие%']}%")
        if not series:
            print(f"**{fold}: нет пересечения перп∩спот — клетка неинтерпретируема**")
            continue
        probe_fold(series, funding, f"fold {fold}", (end - start) / 86400)
    series_all, funding_all, _ = load_series(db, symbol, FOLDS[0][1], FOLDS[-1][2])
    if series_all:
        probe_fold(series_all, funding_all, "POOLED (весь эпохальный период)",
                   (FOLDS[-1][2] - FOLDS[0][1]) / 86400)


def main() -> int:
    print("# C-0 basis probe — Bybit perp/spot (один venue), funding-поток Binance (конвенция B-1.1)")
    print(f"\nfriction 4 ноги = {float(RT_COST):.4f} нотиала за раунд; "
          f"pre-code no-go: медианный gross pooled < {BREAKEVEN_GROSS:.4f}")
    for sym, db in DBS.items():
        if not db.exists():
            print(f"\n# {sym}: БД {db} не найдена — сначала бэкфилл spot_1m")
            continue
        probe_symbol(db, sym)
    return 0


if __name__ == "__main__":
    sys.exit(main())
