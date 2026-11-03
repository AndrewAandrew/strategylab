# -*- coding: utf-8 -*-
"""T-0 read-only зонд daily trend (Donchian x ATR-stop) — ПРЕЖДЕ заморозки решётки Series T.

Правило (заморожено в T-preregistration-DRAFT.md коммитом bf4aef5 до чисел):
  1d OHLC из bars_1m (только полные UTC-сутки); сигнал по close_t:
  c_t > max(c_{t-N..t-1}) → want +1; c_t < min(...) → want −1; иначе want = тек. позиция;
  исполнение pending по OPEN t+1 (adverse slip); N ∈ {20,55,100}, ATR14 (SMA TR за 14
  завершённых дней до сигнала), стоп k·ATR, k ∈ {1,2,3}:
    шаг A: overnight gap — open пробил стоп → выход по open (pending-вход той же стороны
           отменяется);
    шаг B: pending-реверс/вход по open (2 ноги);
    шаг C: intraday стоп — low≤stop(long)/high≥stop(short) → выход по stop;
    шаг D: сигнал close_t → pending на t+1;
  конец эпохи: принудительный выход по close последнего полного дня.
  Funding перпа: interior-расчётки (start_дня_входа, start_дня_выхода), long платит при
  r>0 (D-0 конвенция). N0=$10k lev=1, FEE 0.0006/нога, SLIP 0.0005, float64, mode=ro.

Эпохи: A BTC [1704067200,1735689600) — первичная нетронутая (ПРАВИЛО no-go ключуется
именно на A); B-BTC и B-ETH [1761523200,1790294400) — корроборация. Трети: A — 4 мес
(01-01/05-01/09-01), B — f1/f2/f3 D-0. Сделка целиком в third дня выхода.

HARD-GUARD: чтения ts >= 1790294400 нет (SQL-фильтр + assert + счётчик).

Pre-code no-go (DRAFT §4): нет клетки с (все не-пустые трети A net>=0) ∧ (pooled A
>= +3.0%/год) ∧ (RT A >= 3) ∧ (MAE <= 50% N0) => серия T не пишется.

Запуск:
  python -X utf8 .superpowers/sdd/2026-11-03-seriesT-daily-trend/meas/t0_daily_trend_probe.py
"""

from __future__ import annotations

import bisect
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DBS = {"BTCUSDT": REPO / "data" / "btcusdt.db", "ETHUSDT": REPO / "data" / "ethusdt.db"}

FLOOR = 1790294400
CAPITAL = 10_000.0
FEE = 0.0006
SLIP = 0.0005
HURDLE = 0.03
NS = [20, 55, 100]
KS = [1.0, 2.0, 3.0]
ATR_N = 14
MIN_RT = 3  # T-c для probe (низкий пол: класс низкооборотный по построению)
MAX_MAE = 50.0

ERAS = {
    "A": ("BTCUSDT", 1704067200, 1735689600, [1704067200, 1714521600, 1725148800, 1735689600]),
    "B-BTC": ("BTCUSDT", 1761523200, 1790294400, [1761523200, 1771113600, 1780704000, 1790294400]),
    "B-ETH": ("ETHUSDT", 1761523200, 1790294400, [1761523200, 1771113600, 1780704000, 1790294400]),
}

READS_BEYOND_FLOOR = [0]
DAY = 86400


@dataclass
class Trade:
    side: int = 0
    entry_day: int = 0
    exit_day: int = 0
    q: float = 0.0
    ent_fill: float = 0.0
    exit_fill: float = 0.0
    stop: float = 0.0
    mae_pct: float = 0.0
    price: float = 0.0
    carry: float = 0.0

    def net(self) -> float:
        return self.price + self.carry - 2 * FEE * CAPITAL


@dataclass
class CellRes:
    rts: int = 0
    price: float = 0.0
    carry: float = 0.0
    net: float = 0.0
    mae_pct: float = 0.0
    days_in_pos: int = 0
    third_net: list[float] = field(default_factory=list)
    third_rt: list[int] = field(default_factory=list)


def load_daily(symbol: str, db: Path, start: int, end: int) -> list[tuple[int, float, float, float, float]]:
    """Полные UTC-сутки сегмента [start,end): (day_idx, open, high, low, close)."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ts, open, high, low, close FROM bars_1m WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
            (symbol, start, end),
        ).fetchall()
        funding = conn.execute(
            "SELECT ts, rate FROM funding_8h WHERE symbol=? AND ts<? ORDER BY ts",
            (symbol, end),
        ).fetchall()
    finally:
        conn.close()
    if rows and (rows[-1][0] >= FLOOR or (funding and funding[-1][0] >= FLOOR)):
        READS_BEYOND_FLOOR[0] += 1
    assert not rows or rows[-1][0] < FLOOR, "HARD-GUARD: бары за FLOOR"
    assert not funding or funding[-1][0] < FLOOR, "HARD-GUARD: funding за FLOOR"
    days: dict[int, list] = {}
    for ts, o, h, l, c in rows:
        di = ts // DAY
        rec = days.get(di)
        f = float
        if rec is None:
            days[di] = [f(o), f(h), f(l), f(c), 1]
        else:
            rec[1] = max(rec[1], f(h))
            rec[2] = min(rec[2], f(l))
            rec[3] = f(c)
            rec[4] += 1
    out = [(di, v[0], v[1], v[2], v[3]) for di, v in sorted(days.items()) if v[4] == 1440]
    return out


def load_funding(symbol: str, db: Path, end: int) -> tuple[list[int], list[float]]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ts, rate FROM funding_8h WHERE symbol=? AND ts<? ORDER BY ts",
            (symbol, end),
        ).fetchall()
    finally:
        conn.close()
    if rows and rows[-1][0] >= FLOOR:
        READS_BEYOND_FLOOR[0] += 1
    assert not rows or rows[-1][0] < FLOOR, "HARD-GUARD: funding за FLOOR"
    return [t for t, _ in rows], [float(r) for _, r in rows]


def simulate(daily: list[tuple[int, float, float, float, float]], fts: list[int], fr: list[float],
             era_start: int, era_end: int, thirds: list[int], n: int, k: float) -> CellRes:
    if len(daily) <= n + 1:
        return CellRes(third_net=[0.0] * 3, third_rt=[0] * 3)
    idx = [d[0] for d in daily]
    # day_idx → позиция в массиве; неполные сутки удалены — окна считаются по индексам
    o = [d[1] for d in daily]
    hi = [d[2] for d in daily]
    lo = [d[3] for d in daily]
    cl = [d[4] for d in daily]
    tr = [0.0] * len(daily)
    for t in range(1, len(daily)):
        tr[t] = max(hi[t] - lo[t], abs(hi[t] - cl[t - 1]), abs(lo[t] - cl[t - 1]))
    warm = max(n, ATR_N)  # первая полная история: signal day t>=warm (TR до t-1 включ. t? см. ниже)
    pos = 0
    pend = 0
    tr_open: Trade | None = None
    trades: list[Trade] = []

    def fsum(xs: list[float]) -> float:
        return math.fsum(xs)

    def carry_for(side: int, ent_i: int, exit_i: int) -> float:
        a = bisect.bisect_right(fts, idx[ent_i] * DAY)
        b = bisect.bisect_left(fts, idx[exit_i] * DAY)
        if b <= a:
            return 0.0
        return fsum(fr[a:b]) * CAPITAL * (-1 if side > 0 else 1)

    def close_trade(exit_day: int, exit_price_raw: float) -> None:
        nonlocal pos, tr_open
        assert tr_open is not None
        s = tr_open.side
        # adverse slip: long exit = продажа ниже, short exit = покупка выше
        fill = exit_price_raw * (1 - SLIP) if s > 0 else exit_price_raw * (1 + SLIP)
        tr_open.exit_day, tr_open.exit_fill = exit_day, fill
        tr_open.price = tr_open.q * (fill - tr_open.ent_fill) if s > 0 else tr_open.q * (tr_open.ent_fill - fill)
        tr_open.carry = carry_for(s, tr_open.entry_day, exit_day)
        worst = 0.0
        for j in range(tr_open.entry_day, exit_day + 1):
            adv = lo[j] if s > 0 else hi[j]
            loss = tr_open.q * (tr_open.ent_fill - adv) if s > 0 else tr_open.q * (adv - tr_open.ent_fill)
            worst = max(worst, loss)
        tr_open.mae_pct = 100 * worst / CAPITAL
        trades.append(tr_open)
        pos = 0
        tr_open = None

    def open_trade(side: int, day: int) -> None:
        nonlocal pos, tr_open
        raw = o[day]
        fill = raw * (1 + SLIP) if side > 0 else raw * (1 - SLIP)
        # ATR14 на момент входа: среднее TR за 14 завершённых дней ДО дня исполнения
        atr_days = tr[max(0, day - ATR_N):day]
        atr = fsum(atr_days) / len(atr_days)
        stop = fill - k * atr if side > 0 else fill + k * atr
        pos = side
        tr_open = Trade(side=side, entry_day=day, q=CAPITAL / raw, ent_fill=fill, stop=stop)

    warm_day = None  # (не используется; сигнал с d >= warm)
    for d in range(1, len(daily)):
        # шаг A: overnight gap для позиции, набранной до сегодняшнего open
        if pos != 0:
            breached = (pos > 0 and o[d] <= tr_open.stop) or (pos < 0 and o[d] >= tr_open.stop)
            if breached:
                s_closed = pos
                close_trade(d, o[d])
                if pend == s_closed:
                    pend = 0  # не перескакиваем обратно в тот же тренд на том же open
        # шаг B: pending-реверс/вход по open
        if pend != pos and pend != 0:
            if pos != 0:
                close_trade(d, o[d])
            open_trade(pend, d)
        pend = 0
        # шаг C: intraday-стоп (в т.ч. день входа)
        if pos != 0:
            hit = (pos > 0 and lo[d] <= tr_open.stop) or (pos < 0 and hi[d] >= tr_open.stop)
            if hit:
                close_trade(d, tr_open.stop)
        # шаг D: сигнал по close_d
        if d >= warm:
            window = cl[d - n:d]
            if cl[d] > max(window):
                want = 1
            elif cl[d] < min(window):
                want = -1
            else:
                want = pos  # hold тренда
            if want != pos:
                pend = want
    # конец эпохи: форс-выход по close последнего полного дня
    if pos != 0:
        close_trade(len(daily) - 1, cl[len(daily) - 1])

    res = CellRes()
    res.rts = len(trades)
    res.price = fsum(t.price for t in trades)
    res.carry = fsum(t.carry for t in trades)
    res.net = fsum(t.net() for t in trades)
    res.mae_pct = max((t.mae_pct for t in trades), default=0.0)
    res.days_in_pos = sum(t.exit_day - t.entry_day for t in trades)
    tn = [0.0, 0.0, 0.0]
    trt = [0, 0, 0]
    for t in trades:
        th = bisect.bisect_right(thirds[1:-1], idx[t.exit_day] * DAY)
        tn[th] += t.net()
        trt[th] += 1
    res.third_net = tn
    res.third_rt = trt
    return res


def tick(x: bool) -> str:
    return "V" if x else "X"


def era_report(era: str, daily, fts, fr) -> list[str]:
    sym, start, end, thirds = ERAS[era]
    days = (end - start) / DAY
    print(f"\n# Эпоха {era} ({sym}): полных суток {len(daily)} из {days:.0f}, "
          f"расчёток funding {sum(1 for t in fts if start <= t < end)}")
    print("| N | k | t1 $ (n) | t2 $ (n) | t3 $ (n) | pooled $ | %/год | RT | price $ | carry $ | "
          "MAE% | в-позиции % | T-a | T-b | T-c | T-d |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    surv: list[str] = []
    for n in NS:
        for k in KS:
            r = simulate(daily, fts, fr, start, end, thirds, n, k)
            yearly = r.net / CAPITAL / (days / 365)
            nonempty = [x for x in r.third_net]
            ta = all(x >= 0 for x in nonempty)
            tb = yearly >= HURDLE
            tc = r.rts >= MIN_RT
            td = r.mae_pct <= MAX_MAE
            if ta and tb and tc and td:
                surv.append(f"{era} N={n} k={k}: {r.net:+,.0f}$ ({100 * yearly:+.1f}%/год, RT={r.rts})")
            print(f"| {n} | {k} | {r.third_net[0]:+,.0f} ({r.third_rt[0]}) | {r.third_net[1]:+,.0f} "
                  f"({r.third_rt[1]}) | {r.third_net[2]:+,.0f} ({r.third_rt[2]}) | {r.net:+,.0f} | "
                  f"{100 * yearly:+.1f}% | {r.rts} | {r.price:+,.0f} | {r.carry:+,.0f} | "
                  f"{r.mae_pct:.1f} | {100 * r.days_in_pos / max(1, len(daily)):.0f} | "
                  f"{tick(ta)} | {tick(tb)} | {tick(tc)} | {tick(td)} |")
    return surv


def main() -> int:
    print("# T-0 daily trend probe (read-only, mode=ro, float64 QA)")
    print(f"\nкапитал/N0 $ {CAPITAL:,.0f} lev=1; fee 0.0006/нога + slip 0.0005; планка "
          f"+{100 * HURDLE:.1f}%/год; RT>={MIN_RT}; MAE<={MAX_MAE:.0f}%; ATR{ATR_N}; "
          f"N={NS} x k={KS}; вход/выход open t+1, intraday-стоп; funding interior-касса")
    survivors: list[str] = []
    survivors_a: list[str] = []
    cache: dict[str, tuple[list, list[int], list[float]]] = {}
    for era in ERAS:
        sym, start, end, thirds = ERAS[era]
        key = f"{sym}|{start}|{end}"
        if key not in cache:
            db = DBS[sym]
            cache[key] = (load_daily(sym, db, start, end), *load_funding(sym, db, end))
        daily, fts, fr = cache[key]
        if len(daily) <= max(NS) + 1:
            print(f"\n# {era}: суток {len(daily)} — мало для N={max(NS)}, клетка помечается warmup-skip")
            continue
        s = era_report(era, daily, fts, fr)
        survivors += s
        if era == "A":
            survivors_a += s
    print("\n# HOLDOUT-GUARD")
    print(f"чтений ts >= {FLOOR}: {READS_BEYOND_FLOOR[0]} (ожидаем 0)")
    print("\n# ИТОГ (no-go ключуется на эпохе A — DRAFT §4)")
    if survivors_a:
        print("ВЫЖИВШИЕ В A:")
        for s in survivors_a:
            print("  " + s)
        print("ВЕРДИКТ-КАНДИДАТ: доклад владельцу, freeze T-2 только после «ок»")
    else:
        print("ни одной выжившей клетки в нетронутой эпохе A (BTC-2024) => PRE-CODE NO-GO: "
              "Series T не пишется")
        if survivors:
            print("выжившие в B (корроборация, НЕ основание):")
            for s in survivors:
                print("  " + s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
