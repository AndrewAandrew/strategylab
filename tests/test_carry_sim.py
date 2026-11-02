"""Тесты carry-симулятора Series B: замороженные конвенции B-1/B-1.1/B2-5 из
`.superpowers/sdd/2026-11-02-seriesB-carry/seriesB-carry-preregistration.md`.
Арифметика сверяется с ручными ожиданиями (Decimal, точные)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from strategylab.carry import (
    CarryLiquidationError,
    CarryParams,
    FundingSettle,
    run_carry,
)
from strategylab.core.bars import Bar

P = CarryParams(theta_entry=Decimal("0.00005"), theta_exit=Decimal("0.00003"))


def bar(ts: int, o: str, h: str | None = None, c: str | None = None) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal(o),
        high=Decimal(h or o),
        low=Decimal(o),
        close=Decimal(c or o),
        volume=Decimal("1"),
    )


def test_cash_flow_hand_computed() -> None:
    """Полный ручной прогон: вход после settlement(0), два accrual-такта, выход
    по сигналу settlement(180) на баре 240. Ожидания считаются в доке теста."""
    bars = [
        bar(0, "100"),
        bar(60, "100", h="100", c="101"),
        bar(120, "101", h="101", c="102"),
        bar(180, "102", h="102", c="103"),
        bar(240, "103", h="103", c="103"),
    ]
    settles = [
        FundingSettle(0, Decimal("0.0004")),
        FundingSettle(120, Decimal("0.0004")),
        FundingSettle(180, Decimal("0.0000001")),
    ]
    res = run_carry(bars, settles, P, end_ts=300)

    qty = Decimal("10000") / Decimal("100")  # 100
    entry_fill = Decimal("100") * Decimal("0.9995")  # 99.95
    entry_cost = Decimal("0.0006") * qty * entry_fill + qty * (Decimal("100") - entry_fill)
    s120 = Decimal("0.0004") * qty * Decimal("102")  # mark close(120)=102
    s180 = Decimal("0.0000001") * qty * Decimal("103")  # mark close(180)=103
    exit_ref = Decimal("103")
    exit_fill = exit_ref * Decimal("1.0005")
    exit_cost = Decimal("0.0006") * qty * exit_fill + qty * (exit_fill - exit_ref)

    expected_net = s120 + s180 - entry_cost - exit_cost
    assert res.holds == 1
    assert res.settles == 2
    assert res.funding_gross == s120 + s180
    assert res.friction == entry_cost + exit_cost
    assert res.net == expected_net
    assert res.equity == Decimal("10000") + expected_net
    assert [r.kind for r in res.journal] == ["entry", "settle", "settle", "exit"]
    assert res.journal[-1].cash_after == expected_net
    # риск ноги: max high на держании = 102 при entry_ref 100 -> 200/10000
    assert res.max_leg_dd_frac == Decimal("0.02")
    assert res.margin_call is False
    assert res.flat_fold is False


def test_settlement_boundaries_are_strict() -> None:
    """settle ровно в entry_ts и ровно в exit_fill_ts НЕ засчитываются:
    входнился на баре 60 — такт 60 мимо; выход на баре 180 — такт 180 мимо."""
    bars = [
        bar(0, "100"),
        bar(60, "100", c="101"),
        bar(120, "101", c="102"),
        bar(180, "102", c="103"),
    ]
    settles = [
        FundingSettle(0, Decimal("0.0002")),  # вход -> бар 60
        FundingSettle(60, Decimal("0.0002")),  # ровно entry_ts — excluded
        FundingSettle(120, Decimal("0.00005")),  # внутри — засчитан (>= theta_exit)
        FundingSettle(150, Decimal("0.00001")),  # < theta_exit -> выход на баре 180
        FundingSettle(180, Decimal("0.0002")),  # ровно exit_ts — excluded, и entry нет (flat)
    ]
    res = run_carry(
        bars,
        settles,
        CarryParams(theta_entry=Decimal("0.0002"), theta_exit=Decimal("0.00003")),
        end_ts=240,
    )
    assert res.holds == 1  # re-entry после 180 невозможен: баров после 180 нет (end 240)
    # interior (60,180): 120 и 150 (decision-такт выхода тоже засчитан — позиция
    # держалась в момент settlement); границы 60 и 180 — мимо
    assert res.settles == 2
    assert [r.ts for r in res.journal if r.kind == "settle"] == [120, 150]


def test_reentry_after_exit() -> None:
    """Выход по сигналу, следующий высокий rate открывает держание заново."""
    bars = [bar(t, "100") for t in range(0, 600, 60)]
    settles = [
        FundingSettle(0, Decimal("0.001")),
        FundingSettle(120, Decimal("0.0000001")),  # < theta_exit -> выход на баре 180
        FundingSettle(300, Decimal("0.001")),  # снова вход: держание 2 (exit forcing на баре 540)
    ]
    res = run_carry(bars, settles, P, end_ts=600)
    assert res.holds == 2
    # decision-такт exit-сигнала (120) ЗАСЧИТЫВАЕТСЯ: позиция держалась в момент
    # settlement, выход исполняется позже — на следующем баре (180).
    assert [(r.ts, r.kind) for r in res.journal] == [
        (60, "entry"),
        (120, "settle"),
        (180, "exit"),
        (360, "entry"),
        (540, "exit"),
    ]


def test_flat_fold_never_enters() -> None:
    bars = [bar(0, "100"), bar(60, "100"), bar(120, "100")]
    settles = [FundingSettle(0, Decimal("0.00004")), FundingSettle(60, Decimal("-0.001"))]
    res = run_carry(bars, settles, P, end_ts=180)
    assert res.holds == 0 and res.settles == 0 and res.net == 0
    assert res.flat_fold is True
    assert res.equity == Decimal("10000")


def test_forced_exit_at_last_bar_open() -> None:
    """Никогда не выходит по сигналу: закрытие на open последнего бара окна;
    settlement-такты после этого open (до end) не засчитываются."""
    bars = [bar(t, "100") for t in (0, 60, 120, 180)]
    settles = [
        FundingSettle(0, Decimal("0.001")),  # вход на баре 60
        FundingSettle(120, Decimal("0.001")),  # inside (60, 180)
        FundingSettle(180, Decimal("0.001")),  # ровно exit (open бара 180) — excluded
    ]
    res = run_carry(bars, settles, P, end_ts=240)
    assert res.holds == 1
    assert res.settles == 1
    assert res.journal[-1].kind == "exit" and res.journal[-1].ts == 180


def test_entry_on_last_bar_same_bar_roundtrip() -> None:
    """Вход последним баром: вход и выход по одному open; n=0, но holds=1,
    чистый убыток ровно на трении."""
    bars = [bar(0, "100"), bar(60, "100")]
    settles = [FundingSettle(0, Decimal("0.001"))]
    res = run_carry(bars, settles, P, end_ts=61)
    assert res.holds == 1 and res.settles == 0 and res.flat_fold is True
    qty = Decimal("100")
    entry_fill = Decimal("99.95")
    exit_fill = Decimal("100.05")
    expected = -(Decimal("0.0006") * qty * entry_fill + qty * Decimal("0.05")) - (
        Decimal("0.0006") * qty * exit_fill + qty * Decimal("0.05")
    )
    assert res.net == expected


def test_margin_call_at_half_margin() -> None:
    """Просадка ноги >= 50% маржи: margin_call без raise; >= 100%: CarryLiquidationError."""
    bars = [bar(0, "100"), bar(60, "100"), bar(120, "160", h="160"), bar(180, "160")]
    settles = [FundingSettle(0, Decimal("0.001"))]
    res = run_carry(bars, settles, P, end_ts=240)
    assert res.margin_call is True
    assert res.max_leg_dd_frac == Decimal("0.6")  # 100*(160-100)/10000

    bars_liq = [bar(0, "100"), bar(60, "100"), bar(120, "210", h="210"), bar(180, "210")]
    with pytest.raises(CarryLiquidationError):
        run_carry(bars_liq, settles, P, end_ts=240)


def test_negative_funding_while_holding() -> None:
    """Отрицательный rate внутри держания шорт платит: cash_delta отрицательный,
    net тянет вниз несмотря на positive funding на входе."""
    bars = [bar(t, "100") for t in (0, 60, 120, 180)]
    settles = [
        FundingSettle(0, Decimal("0.001")),  # вход на 60
        FundingSettle(120, Decimal("-0.0002")),  # платим шортом
        FundingSettle(150, Decimal("0.0001")),  # positive, держим дальше
    ]
    res = run_carry(bars, settles, P, end_ts=240)
    assert res.settles == 2
    deltas = {r.ts: r.cash_delta for r in res.journal if r.kind == "settle"}
    assert deltas[120] == Decimal("-0.0002") * Decimal("100") * Decimal("100")
    assert deltas[150] == Decimal("0.0001") * Decimal("100") * Decimal("100")
    assert res.funding_gross == deltas[120] + deltas[150]
    assert res.funding_gross < 0


def test_validation() -> None:
    bars = [bar(0, "100")]
    ok = CarryParams(theta_entry=Decimal("0.0001"), theta_exit=Decimal("0.00005"))
    with pytest.raises(ValueError):
        run_carry(
            bars,
            [],
            CarryParams(theta_entry=Decimal("0.0001"), theta_exit=Decimal("0.0002")),
            end_ts=60,
        )
    with pytest.raises(ValueError):
        run_carry(bars, [FundingSettle(70, Decimal("0.001"))], ok, end_ts=60)
    with pytest.raises(ValueError):
        run_carry([bar(0, "1"), bar(0, "2")], [], ok, end_ts=60)
    with pytest.raises(ValueError):
        dup = [FundingSettle(1, Decimal("0")), FundingSettle(1, Decimal("0"))]
        run_carry(bars, dup, ok, end_ts=60)
    with pytest.raises(ValueError):
        zero_cap = CarryParams(
            theta_entry=Decimal("0.0001"), theta_exit=Decimal("0"), capital=Decimal("0")
        )
        run_carry(bars, [], zero_cap, end_ts=60)


def test_determinism_module_level() -> None:
    bars = [
        bar(t, "100", h=str(100 + (t // 60 % 3)), c=str(100 + t // 60 % 2))
        for t in range(0, 1200, 60)
    ]
    settles = [
        FundingSettle(t, Decimal("0.0002" if t < 300 else "0.00000001"))
        for t in range(0, 1200, 120)
    ]
    r1 = run_carry(bars, settles, P, end_ts=1200)
    r2 = run_carry(bars, settles, P, end_ts=1200)
    assert r1.journal == r2.journal
    assert r1.net == r2.net and r1.equity == r2.equity
    assert r1.max_leg_dd_frac == r2.max_leg_dd_frac


def test_carry_module_has_no_kernel_imports() -> None:
    """B-5: carry.py не импортирует ядро engine/broker/strategy — только core.bars."""
    src = Path("strategylab/carry.py").read_text(encoding="utf-8")
    for forbidden in ("strategylab.core.engine", "strategylab.broker", "strategylab.strategy"):
        assert forbidden not in src
