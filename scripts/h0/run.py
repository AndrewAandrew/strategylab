# -*- coding: utf-8 -*-
"""Машина прогона H-0 — Series H, низко-ADX volume-fade на ETH-форварде (предсборка).

Закон: .superpowers/sdd/2026-11-03-global-audit/H0-freeze-criteria-DRAFT.md (коммит
2026-11-03, до любых форвардных чисел). Прогон — РОВНО ОДИН, после 1798761600
(2027-01-01). До закрытия окна живой прогон с --live запрещён guard'ом (rc=3).

Объект (дословно из замороженного r1 M8, без right-sizing):
- ETHUSDT hourly: 1h-бары из bars_1m строгим агрегатом серии A (неполное окно
  минут = ошибка данных, а не бар);
- кульминация: vol_t > 3*SMA20(vol)_t И (high_t - low_t) > 3*ATR14_t, индикаторы
  считаются ВКЛЮЧАЯ событийный бар (как в r1 M8.1);
- фейд: up-кульминация (close_t > open_t) — шорт; иначе (включая doji) — лонг;
- ADX-гейт: круг жив только при ADX(14)_t < 25 (Wilder, первый ADX ≈ бар 28);
- вход боевого сима: open(t+1) + adverse slip (п.4.3 закона: строже r1 close(t+1),
  r1-числа не приравниваются); выход time-exit close(t+1+k), k ∈ {6, 12} — обе
  клетки фиксированы заранее; катастроф-стоп 4*ATR14 от цены входа-fill: шорт по
  high, лонг по low, гэп за стоп исполняется по открытию (хуже), стоп на баре
  выхода имеет приоритет (консервативно); круг помечается catastrophe и считается
  по факту;
- трение: FEE 0.0006/ногу + SLIP 0.0005/ногу (round-trip 0.22 %), CAPITAL $10k,
  lev=1 (нотикул входа = 10 000$, qty = CAPITAL/entry_fill);
- круги независимы, перекрытие разрешено (как r1-мерка: каждый событийный круг
  сам по себе); если close(t+1+k) не влезает в окно — круг отбрасывается.

Интерпретация §2 «минимальный объём ≥1,440·60» (записана ДО чисел): 1 440
последовательных 1h-баров окна, каждый собран из полных 60 1m-баров; при недоборе
— NO-START (rc=2), окно не сдвигается и не укорачивается.

Гейты живой клетки (H-a…H-e) и порядок — в evaluate_cell/build_report, константы
заморожены здесь и не двигаются.

Ручной запуск (после 2027-01-01):
    .venv\\Scripts\\python.exe -X utf8 scripts\\h0\\run.py --live
Смоук на синтетике (сегодня, безопасно):
    .venv\\Scripts\\python.exe -X utf8 scripts\\h0\\run.py --smoke
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from strategylab.core.bars import Bar, Interval  # noqa: E402
from strategylab.data.aggregate import IncompleteBarGroup, aggregate  # noqa: E402
from strategylab.data.schema import _decimal_or_text  # noqa: E402
from strategylab.data.store import CandleStore  # noqa: E402
from strategylab.strategy.indicators import Adx, Atr  # noqa: E402

# ---- замороженные константы закона H-0 (двигать нельзя) ----
WINDOW_START = 1793577600  # [2026-11-02 00:00 UTC)
WINDOW_END = 1798761600  # [2027-01-01 00:00 UTC) = START + 60 суток
REQUIRED_HOURS = 1440  # 60d * 24h — полное покрытие окна
Z_VOL = Decimal(3)  # vol > 3*SMA20(vol)
R_ATR = Decimal(3)  # range > 3*ATR14
SMA_VOL_LEN = 20
ATR_LEN = 14
ADX_LEN = 14
ADX_MAX = Decimal(25)  # круг жив только при ADX(14) < 25
STOP_MULT = Decimal(4)  # катастроф-стоп 4*ATR(14) от входа
K_CELLS = (6, 12)  # обе клетки фиксированы — НЕ выбор после взгляда
FEE = Decimal("0.0006")  # тейкер-нога
SLIP = Decimal("0.0005")  # adverse-слип на ногу
CAPITAL = Decimal(10000)
GATE_HA = Decimal("0.0030")  # pooled mean net-круга ≥ +0.30 %
GATE_HB = Decimal("0.03")  # account annualized ≥ +3.0 %/год (планка §8)
GATE_HD = 30  # n ≥ 30 кругов
GATE_HE = 2  # катастроф-стопов ≤ 2
THIRD_SECONDS = 20 * 86400  # три календарных 20d-трети
SYMBOL = "ETHUSDT"
DB_PATH = REPO / "data" / "ethusdt.db"


class GuardError(Exception):
    """Окно ещё не закрыто — живой прогон запрещён законом §2."""


class DataNotReady(Exception):
    """Покрытие окна хуже замороженного минимума — прогон не стартует (§2)."""


@dataclass(frozen=True, slots=True)
class Signal:
    event_index: int
    side: int  # -1 шорт ап-кульминации, +1 лонг даун-кульминации
    atr: Decimal  # ATR(14) событийного бара — база катастроф-стопа


@dataclass(frozen=True, slots=True)
class Round:
    event_ts: int
    side: int
    entry_ts: int
    entry_price: Decimal
    exit_ts: int
    exit_price: Decimal
    exit_reason: str  # "time" | "catastrophe"
    net_usd: Decimal
    net_pct: Decimal


def detect_signals(bars: list[Bar], adx_max: Decimal | None = ADX_MAX) -> list[Signal]:
    """M8.1 + ADX-гейт M8.3: кульминация объёма/размаха при низком ADX(14)."""
    atr = Atr(ATR_LEN)
    adx = Adx(ADX_LEN)
    vol_window: list[Decimal] = []
    out: list[Signal] = []
    for i, b in enumerate(bars):
        a = atr.update(b.high, b.low, b.close)
        x = adx.update(b.high, b.low, b.close)
        vol_window.append(b.volume)
        if len(vol_window) > SMA_VOL_LEN:
            vol_window.pop(0)
        if a is None or x is None or len(vol_window) < SMA_VOL_LEN:
            continue
        sma_vol = sum(vol_window, Decimal(0)) / Decimal(SMA_VOL_LEN)
        if not (b.volume > Z_VOL * sma_vol):
            continue
        if not (b.high - b.low > R_ATR * a):
            continue
        if adx_max is not None and x >= adx_max:
            continue
        out.append(Signal(event_index=i, side=-1 if b.close > b.open else 1, atr=a))
    return out


def simulate(bars: list[Bar], k: int, signals: list[Signal] | None = None) -> list[Round]:
    """Один круг на сигнал: вход open(t+1)+adverse, стоп 4*ATR_t, выход close(t+1+k)."""
    if signals is None:
        signals = detect_signals(bars)
    out: list[Round] = []
    for s in signals:
        entry_i = s.event_index + 1
        exit_i = entry_i + k
        if exit_i >= len(bars):
            continue  # close(t+1+k) за окном — круг отбрасывается
        e = bars[entry_i]
        if s.side < 0:
            entry_price = e.open * (1 - SLIP)  # продажа ниже — adverse
            stop = entry_price + STOP_MULT * s.atr
        else:
            entry_price = e.open * (1 + SLIP)  # покупка выше — adverse
            stop = entry_price - STOP_MULT * s.atr
        qty = CAPITAL / entry_price
        exit_ts = bars[exit_i].ts
        exit_fill: Decimal | None = None
        reason = "time"
        for j in range(entry_i, exit_i + 1):
            b = bars[j]
            if s.side < 0 and b.high >= stop:
                fill_raw = max(stop, b.open)  # гэп выше стопа — исполнение по открытию
                exit_fill, reason = fill_raw * (1 + SLIP), "catastrophe"
                exit_ts = b.ts
                break
            if s.side > 0 and b.low <= stop:
                fill_raw = min(stop, b.open)
                exit_fill, reason = fill_raw * (1 - SLIP), "catastrophe"
                exit_ts = b.ts
                break
        if exit_fill is None:
            c = bars[exit_i]
            exit_fill = c.close * (1 + SLIP) if s.side < 0 else c.close * (1 - SLIP)
        if s.side < 0:
            net_usd = qty * (entry_price - exit_fill) - qty * entry_price * FEE - qty * exit_fill * FEE
        else:
            net_usd = qty * (exit_fill - entry_price) - qty * entry_price * FEE - qty * exit_fill * FEE
        out.append(
            Round(
                event_ts=bars[s.event_index].ts,
                side=s.side,
                entry_ts=e.ts,
                entry_price=entry_price,
                exit_ts=exit_ts,
                exit_price=exit_fill,
                exit_reason=reason,
                net_usd=net_usd,
                net_pct=net_usd / CAPITAL,
            )
        )
    return out


def third_of(ts: int) -> int:
    """Календарная треть окна по ts событийного бара (0|1|2)."""
    return min((ts - WINDOW_START) // THIRD_SECONDS, 2) if ts >= WINDOW_START else -1


@dataclass(frozen=True, slots=True)
class CellVerdict:
    k: int
    n: int
    mean_net_pct: Decimal | None
    ann_pct: Decimal | None
    thirds: list[tuple[int, Decimal]]  # (nq, сумма net_pct) по каждой трети
    catastrophes: int
    gates: dict[str, bool] = field(default_factory=dict)
    live: bool = False


def evaluate_cell(rounds: list[Round], k: int) -> CellVerdict:
    """Гейты H-a…H-e закона §3 — клетка жива только если все прошли."""
    n = len(rounds)
    mean = sum((r.net_pct for r in rounds), Decimal(0)) / n if n else None
    ann = sum((r.net_usd for r in rounds), Decimal(0)) / CAPITAL * Decimal(365) / Decimal(60)
    thirds = []
    for t in range(3):
        rs = [r for r in rounds if third_of(r.event_ts) == t]
        thirds.append((len(rs), sum((r.net_pct for r in rs), Decimal(0))))
    cats = sum(1 for r in rounds if r.exit_reason == "catastrophe")
    gates = {
        "H-a": mean is not None and mean >= GATE_HA,
        "H-b": ann >= GATE_HB,
        "H-c": all(nq >= 3 and total >= 0 for nq, total in thirds),
        "H-d": n >= GATE_HD,
        "H-e": cats <= GATE_HE,
    }
    return CellVerdict(
        k=k, n=n, mean_net_pct=mean, ann_pct=ann, thirds=thirds,
        catastrophes=cats, gates=gates, live=all(gates.values()),
    )


def series_verdict(cells: dict[int, list[Round]]) -> str:
    """Вердикт серии: NO-GO, если ни одна клетка не прошла H-a…H-e целиком."""
    live = sorted(k for k, rounds in cells.items() if evaluate_cell(rounds, k).live)
    if not live:
        return "NO-GO — ни одна клетка не прошла H-a…H-e; серия H закрывается (do-not-resurrect)"
    return "GO — живые клетки: " + ", ".join(f"k={k}" for k in live) + \
        "; полный отчёт владельцу, freeze-решётка/Holdout только с «ок»"


def check_coverage(bars: list[Bar]) -> None:
    """§2: ровно REQUIRED_HOURS 1h-баров подряд, выровненных по окну."""
    if len(bars) != REQUIRED_HOURS:
        raise DataNotReady(f"1h-баров {len(bars)} из {REQUIRED_HOURS}")
    if bars[0].ts != WINDOW_START:
        raise DataNotReady(f"первый бар {bars[0].ts} != {WINDOW_START}")
    for idx in range(1, len(bars)):
        if bars[idx].ts != bars[idx - 1].ts + 3600:
            raise DataNotReady(f"разрыв перед баром {idx}: ts {bars[idx].ts}")


def run_cells(bars: list[Bar], signals: list[Signal] | None = None) -> dict[int, list[Round]]:
    if signals is None:
        signals = detect_signals(bars)
    return {k: simulate(bars, k, signals) for k in K_CELLS}


def _fmt(value: Decimal | None, places: str) -> str:
    return "n/a" if value is None else str(value.quantize(Decimal(places)))


def build_report(cells: dict[int, list[Round]], *, synthetic: bool) -> str:
    """Детерминированный отчёт: только замороженные константы и числа — ни секунды
    wall-clock, иначе двойной байт-identical прогон не сходится."""
    tag = " [SMOKE-SYNTHETIC — НЕ БОЕВЫЕ ДАННЫЕ]" if synthetic else " [LIVE]"
    lines = [
        f"H-0 FORWARD RUN — Series H low-ADX volume-fade ETHUSDT 1h{tag}",
        f"window=[{WINDOW_START},{WINDOW_END}) hours={REQUIRED_HOURS} "
        f"z=3 r=3 atr=14 adx_max=25 k_frozen=6,12 fee=0.0006 slip=0.0005 capital=10000",
    ]
    for k in K_CELLS:
        c = evaluate_cell(cells.get(k, []), k)
        flags = " ".join(f"{g}={'PASS' if p else 'FAIL'}" for g, p in sorted(c.gates.items()))
        thirds = " ".join(f"(nq={nq},sum={_fmt(total, '0.000001')}%)" for nq, total in c.thirds)
        lines += [
            f"cell k={k}: n={c.n} mean_net={_fmt(c.mean_net_pct, '0.00000001')} "
            f"ann={_fmt(c.ann_pct, '0.000001')} cats={c.catastrophes} thirds=[{thirds}]",
            f"  {flags} LIVE={'YES' if c.live else 'NO'}",
        ]
    lines.append(f"VERDICT: {series_verdict(cells)}")
    return "\n".join(lines) + "\n"


def guard(now_ts: int) -> None:
    if now_ts < WINDOW_END:
        raise GuardError(
            f"окно открыто до {WINDOW_END} (сейчас {now_ts}) — ни одного чтения форварда (§2)"
        )


def load_window(db_path: Path) -> list[Bar]:
    """Read-only загрузка окна [WINDOW_START, WINDOW_END) и строгая агрегация 1m→1h.

    Фит-эпоха (ts < WINDOW_START) запросом не затрагивается никогда.
    """
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        conn.text_factory = _decimal_or_text
        store = CandleStore(conn, SYMBOL)
        minute_bars = list(store.read_1m(SYMBOL, WINDOW_START, WINDOW_END))
    finally:
        conn.close()
    try:
        hourly = aggregate(minute_bars, Interval.HOUR)
    except IncompleteBarGroup as exc:
        raise DataNotReady(f"неполная 1h-группа: {exc}") from exc
    check_coverage(hourly)
    return hourly


def synthetic_window(seed: int) -> list[Bar]:
    """1440 1h-баров для smoke/TDD: ровный случайный дрейф + кульминации.

    Детерминирована по seed; боёвым прогонам недоступна (в отчёте метка SYNTHETIC).
    """
    rng = random.Random(seed)
    bars: list[Bar] = []
    price_cents = 10000
    for i in range(REQUIRED_HOURS):
        open_c = price_cents
        close_c = open_c + rng.randint(-20, 20)
        hi_c = max(open_c, close_c) + 50
        lo_c = min(open_c, close_c) - 50
        vol = Decimal(1000 + rng.randrange(0, 50) * 10)
        bars.append((open_c, hi_c, lo_c, close_c, vol))
        price_cents = close_c
    for idx in rng.sample(range(30, REQUIRED_HOURS - 15), 90):
        open_c, hi_c, lo_c, close_c, vol = bars[idx]
        up = rng.random() < 0.5
        bars[idx] = (
            open_c,
            hi_c + 400,
            lo_c - 400,
            close_c + (300 if up else -300),
            vol * Decimal(12),
        )
    out = []
    for i, (o, h, l, c, v) in enumerate(bars):
        cent = Decimal("0.01")
        out.append(
            Bar(ts=WINDOW_START + 3600 * i, open=Decimal(o) * cent, high=Decimal(h) * cent,
                low=Decimal(l) * cent, close=Decimal(c) * cent, volume=v)
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="H-0 one-shot forward run (замороженный закон)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true", help="синтетическое окно, база не трогается")
    mode.add_argument("--live", action="store_true", help="боевой прогон (только после 2027-01-01)")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--now", type=int, default=None, help="оверрайд времени для guard'а")
    args = parser.parse_args(argv)
    try:
        if args.smoke:
            report = build_report(run_cells(synthetic_window(42)), synthetic=True)
        else:
            guard(args.now if args.now is not None else int(time.time()))
            report = build_report(run_cells(load_window(Path(args.db))), synthetic=False)
    except GuardError as exc:
        print(f"H0-GUARD: {exc}")
        return 3
    except DataNotReady as exc:
        print(f"H0-DATA: {exc}")
        return 2
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
