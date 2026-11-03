# -*- coding: utf-8 -*-
"""F-0 read-only зонд funding-contrarian lev=1 — ПРЕЖДЕ заморозки решётки Series F.

Правило (заморожено в F-preregistration-DRAFT.md до чисел; наследник r3 + конвенции D-0):
  z_j = (r_j − mu)/sd по расчёткам строго из (ts_j − 90d, ts_j), минимум 20, sd population;
  z_j ≥ +θ → SHORT N0, z_j ≤ −θ → LONG N0 (контр-перегретой толпы), θ ∈ {1.5, 2.0, 2.5};
  вход close первого 1m-бара с ts ≥ ts_j + 1h (close_{T+1h}, r3), adverse slip;
  холд ровно H ∈ {24, 48, 96} ч (выход close последнего бара с ts ≤ entry+H; у конца
  эпохи — принудительный выход последним доступным close < end); одна позиция, сигнал
  во время холда пропускается (r3 position-based); carry — interior-расчёткам холда
  (D-0: entry_fill < ts_j < exit_fill; короткая нога получает при r>0);
  fee 0.0006·N0 на ногу, N0 = $10k (lev=1).

Эпохи (всё строго до холдаута): B1 BTC-2024 (нетронутая, первичное OOS), B2 BTC-фит,
E1 ETH-фит; thirds B1 = 4 мес, B2/E1 = f1/f2/f3 D-0.

HARD-GUARD: ни один бар/расчётка с ts >= FLOOR не читается (SQL-фильтр + assert +
счётчик в stdout). BTC-окно [1790294400, 1793577600) зарезервировано под one-shot F-7.

Pre-code no-go (зафиксирован в DRAFT §4): выжившая клетка эпохи = fee-net >= 0 в каждом
third И pooled >= +3.0%/год И кругов >= 15 И max MAE <= 50% N0. Нет ни одной => серия
не пишется. Выжившие только на ETH => NO-GO «ETH-привязка» (E-7).

Точность: float64 QA-уровень. Базы mode=ro. Детерминизм: двойной прогон байт-в-байт.

Запуск:
  python -X utf8 .superpowers/sdd/2026-11-03-seriesF-funding/meas/f0_funding_contrarian_probe.py
"""

from __future__ import annotations

import bisect
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # meas → seriesF → sdd → .superpowers → репо
DBS = {"BTCUSDT": REPO / "data" / "btcusdt.db", "ETHUSDT": REPO / "data" / "ethusdt.db"}

FLOOR = 1790294400  # HOLDOUT-гард: читать ts >= FLOOR запрещено этим прогоном
FUND_MIN = 1704067200  # старт funding-истории (2024-01-01)

# эпоха → (символ, start, end, [границы thirds, включая end])
ERAS = {
    "B1": ("BTCUSDT", 1704067200, 1735689600, [1704067200, 1714521600, 1725148800, 1735689600]),
    "B2": ("BTCUSDT", 1761523200, 1790294400, [1761523200, 1771113600, 1780704000, 1790294400]),
    "E1": ("ETHUSDT", 1761523200, 1790294400, [1761523200, 1771113600, 1780704000, 1790294400]),
}

CAPITAL = 10_000.0
FEE = 0.0006
SLIP = 0.0005
HURDLE = 0.03  # планка §8, +3.0%/год — не ослабляется
THETAS = [1.5, 2.0, 2.5]
HOLDS_H = [24, 48, 96]
Z_WIN = 90 * 86400
Z_MIN_N = 20
MIN_RTS = 15  # F-c (пол probe-стадии; n>=30 — гейт sim-стадии)
MAX_MAE = 50.0  # F-d, % от N0 (lev1-стресс)

READS_BEYOND_FLOOR = [0]  # мутабельный счётчик для stdout


class Venue:
    """Отсортированные 1m-бары (ts, open, high, low, close); цена решений/исполнения — close."""

    def __init__(self, rows: list[tuple[int, float, float, float, float]]) -> None:
        self.ts = [r[0] for r in rows]
        self.h = [r[1] for r in rows]
        self.l = [r[2] for r in rows]
        self.c = [r[3] for r in rows]

    def bar_at_or_after(self, ts: int, before: int) -> int | None:
        i = bisect.bisect_left(self.ts, ts)
        return i if i < len(self.ts) and self.ts[i] < before else None

    def last_bar_at_or_before(self, ts: int) -> int | None:
        i = bisect.bisect_right(self.ts, ts) - 1
        return i if i >= 0 else None


@dataclass
class Cell:
    price: float = 0.0
    carry: float = 0.0
    rts: int = 0
    mae_pct: float = 0.0
    net: float = 0.0
    sig_in_pos: int = 0  # сигналы, пропущенные во время холда
    sig_no_bar: int = 0  # сигналы без входного бара в окне


def load(symbol: str, db: Path) -> tuple[Venue, list[tuple[int, float]]]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ts, high, low, close FROM bars_1m WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
            (symbol, FUND_MIN, FLOOR),
        ).fetchall()
        bars = Venue([(t, float(h), float(l), float(c)) for t, h, l, c in rows])
        funding = [
            (t, float(r))
            for t, r in conn.execute(
                "SELECT ts, rate FROM funding_8h WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
                (symbol, FUND_MIN, FLOOR),
            ).fetchall()
        ]
    finally:
        conn.close()
    if not bars.ts or not funding:
        return bars, funding
    max_ts = max(bars.ts[-1], funding[-1][0])
    if max_ts >= FLOOR:
        READS_BEYOND_FLOOR[0] += 1
    assert max_ts < FLOOR, "HARD-GUARD: чтение за FLOOR"
    return bars, funding


def z_scores(funding: list[tuple[int, float]]) -> dict[int, float]:
    """ts_j → z_j (trailing 90d, population sd, >=20 историч. расчёток; только строго раньше)."""
    ts = [t for t, _ in funding]
    rates = [r for _, r in funding]
    out: dict[int, float] = {}
    for j in range(len(funding)):
        lo = bisect.bisect_left(ts, ts[j] - Z_WIN)
        hist = rates[lo:j]
        if len(hist) < Z_MIN_N:
            continue
        if max(hist) == min(hist):  # exact flat regime: sd=0 без float-шума
            continue
        mu = math.fsum(hist) / len(hist)
        var = math.fsum((x - mu) ** 2 for x in hist) / len(hist)
        sd = math.sqrt(var)
        if sd > 0.0:
            out[ts[j]] = (rates[j] - mu) / sd
    return out


def simulate(
    bars: Venue,
    funding: list[tuple[int, float]],
    zs: dict[int, float],
    fts: list[int],
    start: int,
    end: int,
    theta: float,
    hold_h: int,
) -> Cell:
    """Один прогон клетка θ×H × окно [start,end). N0 = CAPITAL (lev=1)."""
    n0 = CAPITAL
    hold_s = hold_h * 3600
    res = Cell()
    pos_until = -1  # exit_fill_ts текущей позиции
    for ts_j, r_j in funding:
        if not (start <= ts_j < end):
            continue
        z = zs.get(ts_j)
        tail = z is not None and abs(z) >= theta
        if ts_j < pos_until:  # холд идёт: сигнал пропускается (не флип)
            if tail:
                res.sig_in_pos += 1
            continue
        if not tail:
            continue
        side = -1 if z >= theta else 1  # контр-толпа: +хвост → шорт, −хвост → лонг
        ei = bars.bar_at_or_after(ts_j + 3600, end)
        if ei is None:
            res.sig_no_bar += 1
            continue
        ent_ts, ent_raw = bars.ts[ei], bars.c[ei]
        ent_fill = ent_raw * (1 + SLIP) if side > 0 else ent_raw * (1 - SLIP)
        q = n0 / ent_raw
        xi = bars.last_bar_at_or_before(min(ent_ts + hold_s, end - 1))
        if xi is None or xi < ei:
            xi = ei  # вход и конец окна в одном баре: PnL_цены=0
        exit_ts, x_raw = bars.ts[xi], bars.c[xi]
        x_fill = x_raw * (1 - SLIP) if side > 0 else x_raw * (1 + SLIP)
        res.price += q * (x_fill - ent_fill) if side > 0 else q * (ent_fill - x_fill)
        # carry: interior-расчётки (ent_ts, exit_ts); короткая нога получает при r>0
        lo = bisect.bisect_right(fts, ent_ts)
        hi = bisect.bisect_left(fts, exit_ts)
        for k2 in range(lo, hi):
            res.carry += funding[k2][1] * n0 * (1 if side < 0 else -1)
        worst = 0.0
        for k in range(ei + 1, xi + 1):
            adv = bars.h[k] if side > 0 else bars.l[k]
            loss = q * (adv - ent_fill) if side > 0 else q * (ent_fill - adv)
            if loss > worst:
                worst = loss
        res.mae_pct = max(res.mae_pct, 100 * worst / n0)
        res.rts += 1
        pos_until = exit_ts
    res.net = res.price + res.carry - 2 * FEE * n0 * res.rts
    return res


def tick(x: bool) -> str:
    return "V" if x else "X"


def era_report(
    era: str, bars: Venue, funding: list[tuple[int, float]], zs: dict[int, float]
) -> list[str]:
    sym, start, end, thirds = ERAS[era]
    fts = [t for t, _ in funding]
    days = (end - start) / 86400
    surv: list[str] = []
    n_set = sum(1 for t in fts if start <= t < end)
    n_sig15 = sum(1 for t in fts if start <= t < end and t in zs and abs(zs[t]) >= 1.5)
    n_sig20 = sum(1 for t in fts if start <= t < end and t in zs and abs(zs[t]) >= 2.0)
    print(f"\n# Эпоха {era} ({sym}, {days:.0f}д), расчёток {n_set}, "
          f"сигналов |z|>=1.5: {n_sig15}, |z|>=2.0: {n_sig20}")
    print("| θ | H | t1 $ (n) | t2 $ (n) | t3 $ (n) | pooled $ | %/год | RT | price $ | "
          "carry $ | MAE% | F-a | F-b | F-c | F-d | SKIP | NOBAR |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for theta in THETAS:
        for h in HOLDS_H:
            tr = [simulate(bars, funding, zs, fts, a, b, theta, h)
                  for a, b in zip(thirds, thirds[1:], strict=False)]
            pooled = simulate(bars, funding, zs, fts, start, end, theta, h)
            yearly = pooled.net / CAPITAL / (days / 365)
            fa = min((t.net for t in tr), default=0.0) >= 0.0
            fb = yearly >= HURDLE
            fc = pooled.rts >= MIN_RTS
            fd = pooled.mae_pct <= MAX_MAE
            go = fa and fb and fc and fd
            if go:
                surv.append(f"{era} theta={theta} H={h}h: {pooled.net:+,.0f}$ "
                            f"({100 * yearly:+.1f}%/год, RT={pooled.rts})")
            print(f"| {theta} | {h} | {tr[0].net:+,.0f} ({tr[0].rts}) | {tr[1].net:+,.0f} "
                  f"({tr[1].rts}) | {tr[2].net:+,.0f} ({tr[2].rts}) | {pooled.net:+,.0f} | "
                  f"{100 * yearly:+.1f}% | {pooled.rts} | {pooled.price:+,.0f} | "
                  f"{pooled.carry:+,.0f} | {pooled.mae_pct:.1f} | {tick(fa)} | {tick(fb)} | "
                  f"{tick(fc)} | {tick(fd)} | {pooled.sig_in_pos} | {pooled.sig_no_bar} |")
    return surv


def main() -> int:
    print("# F-0 funding-contrarian probe (read-only, bases mode=ro, float64 QA)")
    print(f"\nкапитал $ {CAPITAL:,.0f}; lev=1 (N0=$10k); fee 0.0006/нога + slip 0.0005 в ценах; "
          f"планка +{100 * HURDLE:.1f}%/год; RT >= {MIN_RTS}; MAE <= {MAX_MAE:.0f}% N0; "
          f"theta={THETAS} x H={HOLDS_H}ч; z: trailing 90d, min {Z_MIN_N}, population sd")
    print("вход close_{T+1h}; холды неперекрывающиеся; carry interior-расчёткам; "
          "контр-толпа: z>=+theta шорт, z<=-theta лонг")
    by_sym: dict[str, tuple[Venue, list[tuple[int, float]], dict[int, float]]] = {}
    loaded: dict[str, tuple[Venue, list[tuple[int, float]], dict[int, float]]] = {}
    for era, (sym, _s, _e, _t) in ERAS.items():
        db = DBS[sym]
        if not db.exists():
            print(f"\n# {era}: БД {db} не найдена")
            continue
        if sym not in by_sym:
            bars, funding = load(sym, db)
            by_sym[sym] = (bars, funding, z_scores(funding))
        loaded[era] = by_sym[sym]
    survivors: list[str] = []
    for era in ERAS:
        if era not in loaded:
            continue
        bars, funding, zs = loaded[era]
        if not bars.ts or not funding:
            print(f"\n# {era}: нет данных (bars {len(bars.ts)}, funding {len(funding)})")
            continue
        survivors += era_report(era, bars, funding, zs)
    print("\n# HOLDOUT-GUARD")
    print(f"чтений ts >= {FLOOR}: {READS_BEYOND_FLOOR[0]} (ожидаем 0); BTC-окно "
          f"[1790294400,1793577600) не тронуто; ETH >=1790294400 не читался")
    print("\n# ИТОГ")
    if survivors:
        btc_alive = any(s.startswith("B") for s in survivors)
        print("выжившие клетки (F-a ^ F-b ^ F-c ^ F-d):")
        for s in survivors:
            print("  " + s)
        if not btc_alive:
            print("ВЕРДИКТ-КАНДИДАТ: NO-GO — выжившие только ETH (E-7 прецедент, "
                  "«ETH-привязка»); пересмотр только форвардными данными")
        else:
            print("ВЕРДИКТ-КАНДИДАТ: есть BTC-выжившая — доклад владельцу с числами, "
                  "F-2-FREEZE только после «ок»")
    else:
        print("ни одной выжившей клетки ни в одной эпохе => PRE-CODE NO-GO по правилу "
              "DRAFT §4: Series F не пишется")
    return 0


if __name__ == "__main__":
    sys.exit(main())
