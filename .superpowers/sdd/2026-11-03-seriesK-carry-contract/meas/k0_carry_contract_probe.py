# -*- coding: utf-8 -*-
"""K-0 read-only зонд carry с контракт-холдом — ПРЕЖДЕ заморозки решётки Series K.

Правило (заморожено в K-preregistration-DRAFT.md ДО чисел; аудит 7ad653e §3):
  delta-neutral: long spot N + short perp N, N = m x CAPITAL, m in {3,10,20};
  вход = open первого 1m-бара эпохи, выход = close последнего бара < end эпохи;
  непрерывный холд, ноль сигналов; carry = ВСЕ расчётки funding_8h ts in [start,end):
  короткая нога получает r*N при r>0, платит при r<0; трение = 4 ноги x (FEE+SLIP) x N;
  price-PnL = 0 по конструкции (одна ценовая нога, объявленное QA-упрощение: базис
  не меряется); стресс-ноги: RISE = max(high)/entry_raw - 1 -> L_max = N*RISE при
  «хедж = нулевой» (консервативно), RUN = минимум бегущей суммы carry за эпоху.

Окна: первичные B-BTC/B-ETH [1761523200,1790294400) thirds 1771113600/1780704000;
корроборация A (BTC-2024) — funding-класс там уже «известен» после F-0; форвард
[1793577600,+38д) объявлен пустым (K-й OOS, прогон когда data дорастёт).

HARD-GUARD: чтения ts >= 1790294400 нет (SQL-фильтр + assert + счётчик stdout).

Pre-code no-go (DRAFT §4): выжившая клетка B = (K-a net>=0 в каждой трети с трением/3)
^ (K-b pooled>=+3.0%/год на $10k) ^ (K-c L_max<=50% капитала) ^ (K-d RUN>=-50% капитала).
Нет ни одной в B-BTC/B-ETH => K не пишется. Есть => GO-PENDING-FORWARD + отдельное ок
владельца на m>1.

Запуск:
  python -X utf8 .superpowers/sdd/2026-11-03-seriesK-carry-contract/meas/k0_carry_contract_probe.py
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DBS = {"BTCUSDT": REPO / "data" / "btcusdt.db", "ETHUSDT": REPO / "data" / "ethusdt.db"}

FLOOR = 1790294400
CAPITAL = 10_000.0
FEE = 0.0006
SLIP = 0.0005
HURDLE = 0.03
MS = [3, 10, 20]
STRESS_FRAC = 0.5  # K-c/K-d: 50% капитала

ERAS = {
    "B-BTC": ("BTCUSDT", 1761523200, 1790294400, [1761523200, 1771113600, 1780704000, 1790294400]),
    "B-ETH": ("ETHUSDT", 1761523200, 1790294400, [1761523200, 1771113600, 1780704000, 1790294400]),
    "A": ("BTCUSDT", 1704067200, 1735689600, [1704067200, 1714521600, 1725148800, 1735689600]),
}
PRIMARY = ("B-BTC", "B-ETH")

READS_BEYOND_FLOOR = [0]


@dataclass
class Cell:
    gross: float = 0.0
    friction: float = 0.0
    net: float = 0.0
    n_set: int = 0
    rise: float = 0.0
    l_max: float = 0.0
    run: float = 0.0
    third: list[float] = None  # type: ignore[assignment]


def load_window(db: Path, sym: str, start: int, end: int) -> tuple[list[tuple[int, float]], float, float]:
    """(funding ts,rate), max high окна, entry open первого бара. Всё строго < FLOOR."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        fun = [(t, float(r)) for t, r in conn.execute(
            "SELECT ts, rate FROM funding_8h WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
            (sym, start, min(end, FLOOR))).fetchall()]
        hi_row = conn.execute(
            "SELECT MAX(high) FROM bars_1m WHERE symbol=? AND ts>=? AND ts<?",
            (sym, start, min(end, FLOOR))).fetchone()
        first = conn.execute(
            "SELECT open FROM bars_1m WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts LIMIT 1",
            (sym, start, min(end, FLOOR))).fetchone()
    finally:
        conn.close()
    hi_max = float(hi_row[0]) if hi_row and hi_row[0] is not None else 0.0
    entry_raw = float(first[0]) if first else 0.0
    if fun and fun[-1][0] >= FLOOR:
        READS_BEYOND_FLOOR[0] += 1
    assert not fun or fun[-1][0] < FLOOR, "HARD-GUARD: funding за FLOOR"
    assert end <= FLOOR, "окно зонда не может залезать за холдаут"
    return fun, hi_max, entry_raw


def simulate(fun: list[tuple[int, float]], hi_max: float, entry_raw: float,
             thirds: list[int], m: int) -> Cell:
    n = m * CAPITAL
    friction = 4.0 * (FEE + SLIP) * n
    carry = [r * n for _t, r in fun]
    gross = sum(carry)
    net = gross - friction
    # K-a: attribution третям (трение равными частями)
    third = [0.0, 0.0, 0.0]
    for (t, r), c in zip(fun, carry, strict=True):
        th = 0
        while th < 2 and t >= thirds[th + 1]:
            th += 1
        third[th] += c
    third = [x - friction / 3.0 for x in third]
    # стресс: RISE против короткого перпа (хедж=нулевой)
    rise = (hi_max / entry_raw - 1.0) if entry_raw > 0 else 0.0
    l_max = n * rise
    # K-d: наихудший бегущий кассовый отток (без трения: cash-flow в моменте)
    run = 0.0
    best = 0.0
    for c in carry:
        run += c
        best = min(best, run)
    return Cell(gross=gross, friction=friction, net=net, n_set=len(fun),
                rise=rise, l_max=l_max, run=best, third=third)


def tick(x: bool) -> str:
    return "V" if x else "X"


def era_report(era: str, sym: str, start: int, end: int, thirds: list[int],
               fun: list[tuple[int, float]], hi_max: float, entry_raw: float) -> list[str]:
    days = (end - start) / 86400
    print(f"\n# Эпоха {era} ({sym}, {days:.0f}д): расчёток {len(fun)}, entry_raw={entry_raw:.2f}, "
          f"max high={hi_max:.2f}")
    print("| m | N $ | t1 $ | t2 $ | t3 $ | gross carry $ | friction $ | pooled $ | %/год | "
          "RISE% | Lmax $ | RUN $ | K-a | K-b | K-c | K-d |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    surv: list[str] = []
    for m in MS:
        c = simulate(fun, hi_max, entry_raw, thirds, m)
        yearly = c.net / CAPITAL / (days / 365)
        ka = all(x >= 0 for x in c.third)
        kb = yearly >= HURDLE
        kc = c.l_max <= STRESS_FRAC * CAPITAL
        kd = c.run >= -STRESS_FRAC * CAPITAL
        if ka and kb and kc and kd:
            surv.append(f"{era} m={m}: {c.net:+,.0f}$ ({100 * yearly:+.1f}%/год, "
                        f"RISE={100 * c.rise:.1f}%, RUN={c.run:+,.0f}$)")
        print(f"| {m} | {m * CAPITAL:,.0f} | {c.third[0]:+,.0f} | {c.third[1]:+,.0f} | "
              f"{c.third[2]:+,.0f} | {c.gross:+,.0f} | {c.friction:,.0f} | {c.net:+,.0f} | "
              f"{100 * yearly:+.1f}% | {100 * c.rise:.1f}% | {c.l_max:,.0f} | {c.run:+,.0f} | "
              f"{tick(ka)} | {tick(kb)} | {tick(kc)} | {tick(kd)} |")
    return surv


def main() -> int:
    print("# K-0 carry-contract probe (read-only, mode=ro, float64 QA)")
    print(f"\nкапитал $ {CAPITAL:,.0f}; N = m x капитал, m={MS}; трение 4 x "
          f"{FEE + SLIP:.4f} x N; планка +{100 * HURDLE:.1f}%/год; стресс K-c/K-d = "
          f"{100 * STRESS_FRAC:.0f}% капитала; carry = все расчётки окна, шорт получает при r>0")
    survivors: list[str] = []
    surv_primary: list[str] = []
    for era, (sym, start, end, thirds) in ERAS.items():
        db = DBS[sym]
        if not db.exists():
            print(f"\n# {era}: БД {db} не найдена")
            continue
        fun, hi_max, entry_raw = load_window(db, sym, start, end)
        if not fun or hi_max <= 0.0 or entry_raw <= 0.0:
            print(f"\n# {era}: нет данных (расчёток {len(fun)})")
            continue
        s = era_report(era, sym, start, end, thirds, fun, hi_max, entry_raw)
        survivors += s
        if era in PRIMARY:
            surv_primary += s
    print("\n# HOLDOUT-GUARD")
    print(f"чтений ts >= {FLOOR}: {READS_BEYOND_FLOOR[0]} (ожидаем 0); BTC-окно "
          f"[1790294400,1793577600) не тронуто; форвард [1793577600,+38д) пуст по декларации")
    print("\n# ИТОГ (no-go ключуется на первичных B-эпохах — DRAFT §4)")
    if surv_primary:
        print("ВЫЖИВШИЕ В ПЕРВИЧНЫХ B:")
        for s in surv_primary:
            print("  " + s)
        print("ВЕРДИКТ-КАНДИДАТ: GO-PENDING-FORWARD — freeze невозможен до (1) данных "
              "форварда [1793577600,+38д) и (2) отдельного «ок» владельца на m>1 "
              "(новый риск-контракт)")
    else:
        print("ни одной выжившей клетки в B-BTC/B-ETH => NO-GO AT PROBE: Series K не пишется")
        if survivors:
            print("выжившие в A (корроборация, НЕ основание):")
            for s in survivors:
                print("  " + s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
