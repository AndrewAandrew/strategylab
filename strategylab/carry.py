"""Симулятор funding carry — Series B (дельта-нейтральный кэрри против positive funding).

Контракт заморожен в `.superpowers/sdd/2026-11-02-seriesB-carry/
seriesB-carry-preregistration.md` (B-1, B-1.1, B2-5). Чистые данные на входе
(бары 1m, settlement-ряд фондинга), детерминированный Decimal-расчёт, без
доступа к БД и без касания J/S-ядра (B-5).

Модель (дословно спеке):
- шорт перпа фиксированным qty = N0/entry_ref, N0 = lev*capital (lev=1 первично);
- решение в settlement-такты: flat и rate >= theta_entry -> вход на open след.
  бара; holding и rate < theta_exit -> выход на open след. бара; принудительный
  выход на open последнего бара окна;
- фондинг засчитывается за settlement-такты strictly внутри держания
  (entry_ts < ts < exit_ts) по маркированному notional qty*mark, где mark = close
  последнего бара с ts <= данного;
- доллары: +rate*notional, минус трение входа/выхода (taker с фактической цены
  ноги + slippage от reference-open); нетто-PnL цены сети = 0 (хедж, B-1);
- риск ноги (B2-5): максимальная просадка qty*(bar.high - entry_ref) на держании
  (включая входной бар; бар выхода — только когда это тот же бар входа) против
  маржи N0: >= 0.5 -> margin call (клетка мертва по правилу), >= 1.0 ->
  CarryLiquidationError (провал правил, артефактов нет — идиом серии A).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from strategylab.core.bars import Bar

ZERO = Decimal("0")


class CarryLiquidationError(RuntimeError):
    """Просадка короткой ноги съела маржу рукава: провал правил, исход, не баг."""


@dataclass(frozen=True, slots=True)
class FundingSettle:
    """Один settlement фондинга: ts — секунды границы 8h, rate — доля (Decimal)."""

    ts: int
    rate: Decimal


@dataclass(frozen=True, slots=True)
class CarryParams:
    """Параметры клетки. theta_exit обязан быть строго меньше theta_entry (гистерезис)."""

    theta_entry: Decimal
    theta_exit: Decimal
    capital: Decimal = Decimal("10000")
    lev: Decimal = Decimal("1")
    taker_fee: Decimal = Decimal("0.0006")
    slippage: Decimal = Decimal("0.0005")


@dataclass(frozen=True, slots=True)
class JournalRow:
    """Строка cash-flow: kind = entry|settle|exit; cash_delta — в долларах."""

    ts: int
    kind: str
    ref: Decimal | None
    fill: Decimal | None
    qty: Decimal | None
    rate: Decimal | None
    notional: Decimal | None
    cash_delta: Decimal
    cash_after: Decimal


@dataclass(frozen=True, slots=True)
class Hold:
    """Держание: reference-цены — open баров исполнения; fill цены — ref*(1 ∓ slip)."""

    entry_ts: int
    entry_ref: Decimal
    exit_ts: int
    exit_ref: Decimal
    qty: Decimal


@dataclass(slots=True)
class CarryResult:
    journal: list[JournalRow] = field(default_factory=list)
    holds: int = 0
    settles: int = 0
    funding_gross: Decimal = ZERO
    friction: Decimal = ZERO
    net: Decimal = ZERO
    equity: Decimal = ZERO
    max_leg_dd_frac: Decimal = ZERO
    margin_call: bool = False
    flat_fold: bool = True


def run_carry(
    bars: Sequence[Bar],
    settles: Sequence[FundingSettle],
    params: CarryParams,
    end_ts: int,
) -> CarryResult:
    """Прогон одной клетки-фолда. bars и settles — окно с end_ts exclusive.

    Pessimistic-only мир: внутрибарный пессимизм уже в модели (adverse = high);
    оптимистичного форка у carry нет (решения не зависят от порядка внутри бара),
    поэтому артефакт один, `fork=pessimistic`.
    """
    if params.theta_exit >= params.theta_entry:
        raise ValueError("theta_exit обязан быть строго меньше theta_entry (гистерезис)")
    if params.capital <= ZERO or params.lev <= ZERO:
        raise ValueError("capital и lev обязаны быть положительными")
    if any(b.ts >= end_ts for b in bars):
        raise ValueError(f"бар за пределами окна: bar.ts >= end_ts ({end_ts})")
    if any(s.ts >= end_ts for s in settles):
        raise ValueError(f"settlement за пределами окна: ts >= end_ts ({end_ts})")
    bars = tuple(sorted(bars, key=lambda b: b.ts))
    settles = tuple(sorted(settles, key=lambda s: s.ts))
    if len({b.ts for b in bars}) != len(bars):
        raise ValueError("дубликат bar.ts")
    if len({s.ts for s in settles}) != len(settles):
        raise ValueError("дубликат settle ts")

    res = CarryResult(equity=params.capital)
    if not bars:
        return res

    bar_ts = [b.ts for b in bars]
    settle_ts = [s.ts for s in settles]

    def first_bar_after(ts: int) -> Bar | None:
        i = bisect_right(bar_ts, ts)
        return bars[i] if i < len(bars) else None

    def last_bar_at_or_before(ts: int) -> Bar:
        i = bisect_right(bar_ts, ts) - 1
        assert i >= 0, "mark-бар отсутствует до settlement внутри держания"
        return bars[i]

    n0 = params.lev * params.capital
    cash = ZERO

    def emit(
        ts: int,
        kind: str,
        ref: Decimal | None,
        fill: Decimal | None,
        qty: Decimal | None,
        rate: Decimal | None,
        notional: Decimal | None,
        delta: Decimal,
    ) -> None:
        nonlocal cash
        cash += delta
        res.journal.append(JournalRow(ts, kind, ref, fill, qty, rate, notional, delta, cash))

    # --- проход 1: держания из последовательности settlement-решений ---
    holds: list[Hold] = []
    open_hold: Hold | None = None
    for s in settles:
        if open_hold is None:
            if s.rate >= params.theta_entry:
                b = first_bar_after(s.ts)
                if b is not None:
                    open_hold = Hold(
                        entry_ts=b.ts,
                        entry_ref=b.open,
                        exit_ts=b.ts,
                        exit_ref=b.open,
                        qty=n0 / b.open,
                    )
        elif s.rate < params.theta_exit and s.ts >= open_hold.entry_ts:
            # «holding» считается с филла входа: такт между решением о входе и
            # самим филлом (достижим только при дыре в 1m-данных >= 8h) позицию
            # не закрывает — держания ещё нет
            b = first_bar_after(s.ts)
            if b is not None:
                holds.append(
                    Hold(
                        entry_ts=open_hold.entry_ts,
                        entry_ref=open_hold.entry_ref,
                        exit_ts=b.ts,
                        exit_ref=b.open,
                        qty=open_hold.qty,
                    )
                )
                open_hold = None
    if open_hold is not None:
        # принудительный выход: open последнего бара окна (или то же окно входа,
        # если входнился последним баром — тогда вход и выход по одному open)
        last = bars[-1]
        if last.ts > open_hold.entry_ts:
            holds.append(
                Hold(
                    entry_ts=open_hold.entry_ts,
                    entry_ref=open_hold.entry_ref,
                    exit_ts=last.ts,
                    exit_ref=last.open,
                    qty=open_hold.qty,
                )
            )
        else:
            holds.append(open_hold)

    # --- проход 2: cash-flow и риск ноги по держаниям ---
    for h in holds:
        entry_fill = h.entry_ref * (1 - params.slippage)
        exit_fill = h.exit_ref * (1 + params.slippage)
        entry_cost = params.taker_fee * h.qty * entry_fill + h.qty * (h.entry_ref - entry_fill)
        exit_cost = params.taker_fee * h.qty * exit_fill + h.qty * (exit_fill - h.exit_ref)
        emit(
            h.entry_ts,
            "entry",
            h.entry_ref,
            entry_fill,
            h.qty,
            None,
            h.qty * h.entry_ref,
            -entry_cost,
        )

        # риск короткой ноги: high баров держания, включая входной; бар выхода
        # не считаем (закрылись на его open), кроме same-bar входа-выхода
        i0 = bisect_left(bar_ts, h.entry_ts)
        i1 = bisect_left(bar_ts, h.exit_ts)
        if i1 == i0:
            i1 += 1
        for b in bars[i0:i1]:
            dd_frac = h.qty * (b.high - h.entry_ref) / n0
            if dd_frac > res.max_leg_dd_frac:
                res.max_leg_dd_frac = dd_frac
        if res.max_leg_dd_frac >= Decimal("1"):
            raise CarryLiquidationError(
                f"margin exhausted: short leg down {res.max_leg_dd_frac} of margin N0"
            )
        if res.max_leg_dd_frac >= Decimal("0.5"):
            res.margin_call = True

        lo = bisect_right(settle_ts, h.entry_ts)
        hi = bisect_left(settle_ts, h.exit_ts)
        for s in settles[lo:hi]:
            mark = last_bar_at_or_before(s.ts).close
            notional = h.qty * mark
            emit(s.ts, "settle", None, None, h.qty, s.rate, notional, s.rate * notional)

        emit(h.exit_ts, "exit", h.exit_ref, exit_fill, h.qty, None, h.qty * h.exit_ref, -exit_cost)
        res.holds += 1

    res.settles = sum(1 for r in res.journal if r.kind == "settle")
    res.funding_gross = sum((r.cash_delta for r in res.journal if r.kind == "settle"), ZERO)
    res.friction = sum((-r.cash_delta for r in res.journal if r.kind != "settle"), ZERO)
    res.net = cash
    res.equity = params.capital + cash
    res.flat_fold = res.settles == 0
    return res
