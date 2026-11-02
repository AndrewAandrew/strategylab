# -*- coding: utf-8 -*-
"""D-0 read-only зонд low-turnover levered carry — ПРЕЖДЕ заморозки решётки Series D.

Варианты (оба считают ЧЕСТНУЮ цену ноги — то, чего не делала серия B):
  V1 naked flip : шорт перпа при r>=+δ, лонг при r<=−δ, флэт в |r|<δ;
                  net = ценовой PnL перпа + carry − fee·ног; lev ∈ {1,3,5,10}.
  V2 hedged     : лонг спота Bybit + шорт перпа Bybit при r>=+δ, иначе флэт;
                  net = PnL обеих ног (базис площадки платится реально) + carry − fee·ног;
                  lev ∈ {1,3} (маржа перп-ноги N0/lev, спот-кэш изолирован).

Конвенции D-1/D-1.1 (зеркало B/C, честные):
  решение — только по закрытой расчётке ts_j (без предсказания r_{j+1});
  исполнение — open первого бара с ts > ts_j, adverse: покупка по open·(1+slip),
  продажа по open·(1−slip); carry — расчёткам строго interior холда
  (entry_fill_ts < ts_j < exit_fill_ts): расчётка, породившая выход, ЕЩЁ
  начисляется (выход исполняется позже), расчётка входа — нет; принудительный
  выход последним open < end фолда; fee = 0.0006·N0 на каждую ногу.

Точность: float64 (QA-уровень зонда — он выбирает оси; боевой sim после freeze
Decimal-детерминированный). Базы mode=ro: зонд физически не может ничего испортить.

Pre-code no-go (зафиксирован в DRAFT до чисел): если НИ ОДНА клетка (V×δ×lev×символ)
не даёт одновременно (a) net≥0 в каждом f1..f3, (b) pooled ≥ +3.0%/год на капитал
клетки, (c) 0 ликвидаций, (d) кругов на фолд ≤ 25 — Series D не пишется, доклад
владельцу с числами зонда.

Запуск:
  python .superpowers/sdd/2026-11-02-seriesD-levered-carry/meas/probe_carry.py
"""

from __future__ import annotations

import bisect
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[4]  # meas → series → sdd → .superpowers → репо
DBS = {"BTCUSDT": REPO / "data" / "btcusdt.db", "ETHUSDT": REPO / "data" / "ethusdt.db"}
FOLDS = [
    ("f1", 1761523200, 1771113600),
    ("f2", 1771113600, 1780704000),
    ("f3", 1780704000, 1790294400),
    ("pooled", 1761523200, 1790294400),
]
ERA = (FOLDS[0][1], FOLDS[-1][2])

CAPITAL = 10_000.0
FEE = 0.0006
SLIP = 0.0005
MM = 0.005  # D2-5: изолированная маржа N0/lev, d(ликвида) = 1/lev − MM
HURDLE = 0.03  # D2-2 — заморожена в DRAFT до любых чисел зонда/сетки
V1_DELTAS = [0.0, 0.00005, 0.0001]  # за расчётку 8h
V1_LEVS = [1, 3, 5, 10]
V2_DELTAS = [0.0, 0.00005]
V2_LEVS = [1, 3]
MAX_RTS_PER_FOLD = 25


class Venue:
    """Отсортированные 1m-бары: next-open после ts, last-open до ts, high/low для MAE."""

    def __init__(self, rows: list[tuple[int, float, float, float]]) -> None:
        self.ts = [r[0] for r in rows]
        self.o = [r[1] for r in rows]
        self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]

    def next_bar(self, after: int, before: int) -> int | None:
        i = bisect.bisect_right(self.ts, after)
        return i if i < len(self.ts) and self.ts[i] < before else None

    def last_bar_before(self, ts: int) -> int | None:
        i = bisect.bisect_left(self.ts, ts) - 1
        return i if i >= 0 else None


@dataclass
class Result:
    price: float = 0.0
    carry: float = 0.0
    legs: int = 0
    hold_s: int = 0
    mae_pct: float = 0.0  # max adverse перп-ноги по холдам, % от N0
    liq: int = 0
    basis_open: float = 0.0  # Σ (perp_raw/spot_raw − 1) на входах V2
    basis_close: float = 0.0
    n_hedge_opens: int = 0
    positions: int = 0


def load(db: Path, symbol: str) -> tuple[Venue, Venue | None, list[tuple[int, float]]]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        # колонки цены — Decimal-строки collector'а (first-write-wins сырьё): float на чтении, QA-уровень
        perp = Venue([(t, float(o), float(h), float(l)) for t, o, h, l in conn.execute(
            "SELECT ts, open, high, low FROM bars_1m WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
            (symbol, *ERA),
        ).fetchall()])
        spot_rows = [(t, float(o), float(h), float(l)) for t, o, h, l in conn.execute(
            "SELECT ts, open, high, low FROM spot_1m WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
            (symbol, *ERA),
        ).fetchall()]
        funding = [
            (t, float(r))
            for t, r in conn.execute(
                "SELECT ts, rate FROM funding_8h WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
                (symbol, *ERA),
            ).fetchall()
        ]
    finally:
        conn.close()
    return perp, (Venue(spot_rows) if spot_rows else None), funding


def simulate(
    perp: Venue,
    spot: Venue | None,  # None ⇒ V1 naked; Venue ⇒ V2 hedged
    funding: list[tuple[int, float]],
    start: int,
    end: int,
    delta: float,
    lev: int,
    capital: float,
) -> Result:
    """Один прогон клетка×фолд. Возвращает сырые слагаемые; net = price+carry−FEE·n0·legs."""
    hedged = spot is not None
    n0 = capital if hedged else capital * lev
    margin = n0 / lev
    res = Result()

    def want_side(r: float) -> int:
        if r >= delta:
            return -1
        if not hedged and r <= -delta:
            return 1
        return 0

    side = 0
    ent_i = ent_ts = 0
    ent_fill = q = 0.0
    s_fill = qs = 0.0

    def open_pos(dec_ts: int, new_side: int) -> None:
        nonlocal side, ent_i, ent_ts, ent_fill, q, s_fill, qs
        pi = perp.next_bar(dec_ts, end)
        if pi is None:
            return
        raw = perp.o[pi]
        side, ent_i, ent_ts = new_side, pi, perp.ts[pi]
        q = n0 / raw
        ent_fill = raw * (1 - SLIP) if new_side < 0 else raw * (1 + SLIP)
        res.legs += 1
        s_fill = qs = 0.0
        if hedged and new_side < 0:
            si = spot.next_bar(dec_ts, end) if spot else None
            if si is None:
                side = 0  # хедж без спот-ноги запрещён: контракт V2 = ОБЕ ноги или флэт
                res.legs -= 1
                return
            qs = n0 / spot.o[si]
            s_fill = spot.o[si] * (1 + SLIP)
            res.legs += 1
            res.basis_open += raw / spot.o[si] - 1
            res.n_hedge_opens += 1

    def close_pos(dec_ts: int | None) -> None:
        """dec_ts=None ⇒ форс-выход последним open < end. side≠0 на входе."""
        nonlocal side, qs
        xi = perp.next_bar(dec_ts, end) if dec_ts is not None else None
        if xi is None or xi <= ent_i:
            xi = perp.last_bar_before(end)
        if xi is None or xi < ent_i:
            xi = ent_i  # вход и конец фолда в одном баре: выход по цене входа, PnL_цены=0
        x_raw, x_ts = perp.o[xi], perp.ts[xi]
        x_fill = x_raw * (1 + SLIP) if side < 0 else x_raw * (1 - SLIP)  # выход шорта = покупка
        price = q * (ent_fill - x_fill) if side < 0 else q * (x_fill - ent_fill)
        exit_legs = 1
        if qs > 0.0 and spot is not None:
            si = spot.last_bar_before(x_ts + 1)
            if si is not None:
                price += qs * (spot.o[si] * (1 - SLIP) - s_fill)
                res.basis_close += x_raw / spot.o[si] - 1
                exit_legs += 1
        worst = 0.0
        for k in range(ent_i + 1, xi + 1):
            adv = perp.h[k] if side < 0 else perp.l[k]
            loss = q * (adv - ent_fill) if side < 0 else q * (ent_fill - adv)
            if loss > worst:
                worst = loss
        res.mae_pct = max(res.mae_pct, 100 * worst / n0)
        if worst >= margin:
            res.liq += 1
        res.price += price
        res.legs += exit_legs
        res.hold_s += max(0, x_ts - ent_ts)
        res.positions += 1
        side = 0
        qs = 0.0

    for ts_j, r_j in [(t, r) for t, r in funding if start <= t < end]:
        if side != 0 and ts_j > ent_ts:  # расчётка interior холда (включая породившую выход)
            res.carry += r_j * n0 * (1 if side < 0 else -1)  # короткая нога получает при r>0
        want = want_side(r_j)
        if want == side:
            continue
        if side != 0:
            close_pos(ts_j)
        if want != 0:
            open_pos(ts_j, want)
    if side != 0:
        close_pos(None)
    return res


def fmt_cell(r: Result, n0: float) -> float:
    return r.price + r.carry - FEE * n0 * r.legs


def probe_symbol(symbol: str, perp: Venue, spot: Venue | None, funding: list[tuple[int, float]]) -> None:
    print(f"\n# {symbol}\n")
    print("## контекст funding-режима (Binance-поток, конвенция B-1.1)")
    print("| фолд | расчёток | средн.%/8h | медиана | доля r≥+0.00005 | смена знака |")
    print("|---|---|---|---|---|---|")
    for label, s, e in FOLDS:
        fr = [r for t, r in funding if s <= t < e]
        if not fr:
            print(f"| {label} | 0 | — | — | — | — |")
            continue
        flips = sum(1 for a, b in zip(fr, fr[1:], strict=False) if (a >= 0) != (b >= 0))
        print(f"| {label} | {len(fr)} | {100 * sum(fr) / len(fr):.4f} | {100 * median(fr):.4f} | "
              f"{100 * sum(1 for r in fr if r >= 0.00005) / len(fr):.1f}% | {flips} |")

    variants = [("V1 naked", None, V1_DELTAS, V1_LEVS, CAPITAL)]
    if spot is not None:
        variants.append(("V2 hedged", spot, V2_DELTAS, V2_LEVS, 2 * CAPITAL))
    for name, spot_v, deltas, levs, cellcap in variants:
        hedged = name.startswith("V2")
        print(f"\n## {name} (капитал клетки ${cellcap:,.0f}; N0="
              f"{'' if hedged else 'lev×'}$10k на ногу; fee 0.0006·N0 на ногу, slip в ценах fill)")
        print("| δ | lev | f1 $ | f2 $ | f3 $ | pooled $ | %/год | carry $ | price $ | "
              "ног | кругов/макс-фолд | холд% | MAE% | ликив | D2-1 | D2-2 | D2-5 | оборот |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for d in deltas:
            for lev in levs:
                n0 = CAPITAL if hedged else CAPITAL * lev  # N0 ноги для fee-арифметики
                fold_r = {}
                for label, s, e in FOLDS:
                    fold_r[label] = simulate(perp, spot_v, funding, s, e, d, lev,
                                             CAPITAL if not hedged else CAPITAL)
                folds3 = [fmt_cell(fold_r[x], n0) for x in ("f1", "f2", "f3")]
                pooled = fmt_cell(fold_r["pooled"], n0)
                days = (FOLDS[-1][2] - FOLDS[0][1]) / 86400
                yearly = pooled / cellcap / (days / 365)
                legs_p = fold_r["pooled"].legs
                rts = legs_p / (4 if hedged else 2)
                rt_fold_max = max(fold_r[x].legs for x in ("f1", "f2", "f3")) / (4 if hedged else 2)
                hold_pct = 100 * fold_r["pooled"].hold_s / (days * 86400)
                liq_p = fold_r["pooled"].liq
                mae_p = fold_r["pooled"].mae_pct
                g1 = "✓" if min(folds3) >= 0 else "✗"
                g2 = "✓" if yearly >= HURDLE else "✗"
                g5 = "✓" if liq_p == 0 else "✗"
                go = "✓" if rt_fold_max <= MAX_RTS_PER_FOLD else "✗"
                print(f"| {d:.5f} | {lev} | {folds3[0]:+,.0f} | {folds3[1]:+,.0f} | {folds3[2]:+,.0f} | "
                      f"{pooled:+,.0f} | {100 * yearly:+.1f}% | {fold_r['pooled'].carry:+,.0f} | "
                      f"{fold_r['pooled'].price:+,.0f} | {legs_p} | {rt_fold_max:.0f} | "
                      f"{hold_pct:.0f}% | {mae_p:.1f} | {liq_p} | {g1} | {g2} | {g5} | {go} |")
        if hedged:
            d0 = simulate(perp, spot_v, funding, *FOLDS[-1][1:], V2_DELTAS[0], 1, CAPITAL)
            if d0.n_hedge_opens:
                print(f"\n- V2 базисная диагностика (δ={V2_DELTAS[0]:.5f}, lev=1, pooled): средний "
                      f"базис на входах {10000 * d0.basis_open / d0.n_hedge_opens:+.1f}bp, "
                      f"на выходах {10000 * d0.basis_close / d0.n_hedge_opens:+.1f}bp, "
                      f"холдов {d0.positions}")


def main() -> int:
    print("# D-0 carry probe — low-turnover levered carry (read-only, bases mode=ro)")
    print(f"\nкапитал $ {CAPITAL:,.0f}; планка D2-2 = +{100 * HURDLE:.1f}%/год (заморожена до чисел); "
          f"no-go: ни одной клетки с (D2-1 ✓ ∧ D2-2 ✓ ∧ D2-5 ✓ ∧ оборот ✓) ⇒ серия не пишется")
    for sym, db in DBS.items():
        if not db.exists():
            print(f"\n# {sym}: БД {db} не найдена")
            continue
        perp, spot, funding = load(db, sym)
        if not perp.ts or not funding:
            print(f"\n# {sym}: нет данных (перп баров {len(perp.ts)}, funding {len(funding)})")
            continue
        probe_symbol(sym, perp, spot, funding)
    print("\n# ИТОГ")
    print("клетки, прошедшие все 4 гейта (D2-1, D2-2, D2-5, оборот ≤25), перечислены строками "
          "с четырьмя ✓ выше; если таких нет ни одной — PRE-CODE NO-GO по правилу DRAFT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
