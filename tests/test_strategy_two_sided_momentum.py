"""Тесты стратегии ДВУСТОРОННИЙ ИМПУЛЬС (two_sided_momentum): синтетические 1m-бары.

Бары строятся так, чтобы пробой читался геометрией ленты, а не настройкой:
`_bars` — плато без единого выхода цены за границы (ATR = 0.5),
`_rising_1m_bars` — монотонный рост, где каждый close выше всех предыдущих
хаёв. Ордеров на каркасной задаче стратегия не выдаёт вовсе, поэтому тесты
смотрят на состояние (`_hh`, `_ll`, `_atr_value`) — приватные атрибуты
стратегии в тестах, как `_lower`/`_adx` у flat_keltner.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from strategylab.broker.orders import Order
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState
from strategylab.strategy.two_sided_momentum import TwoSidedMomentumStrategy

START = 1_717_200_000  # 2024-06-01T00:00:00Z: граница часа, 15m и 5H
ONE = Decimal("1")
EQUITY = Decimal("10000")
FLAT_PRICE = Decimal("100")
FLAT_HALF = Decimal("0.25")  # плато: high 100.25, low 99.75 — true range 0.5, ATR 0.5
RISE_STEP = Decimal("0.01")


# Помощники прогона: объявлены один раз, переиспользуются задачами 7–10.


def _bars(count: int, first: int = 0) -> list[Bar]:
    """Плато: `count` подрядных 1m-баров с ценой 100 и коридором ±0.25."""
    return [
        Bar(
            ts=START + 60 * (first + i),
            open=FLAT_PRICE,
            high=FLAT_PRICE + FLAT_HALF,
            low=FLAT_PRICE - FLAT_HALF,
            close=FLAT_PRICE,
            volume=ONE,
        )
        for i in range(count)
    ]


def _rising_1m_bars(count: int, first: int = 0, base: Decimal = FLAT_PRICE) -> list[Bar]:
    """Монотонный рост: каждый close выше хаёв всех предыдущих баров."""
    return [
        Bar(
            ts=START + 60 * (first + i),
            open=base + RISE_STEP * i,
            high=base + RISE_STEP * (i + 1),
            low=base + RISE_STEP * i,
            close=base + RISE_STEP * (i + 1),
            volume=ONE,
        )
        for i in range(count)
    ]


def _flat_state(bar_index: int = 0) -> StrategyState:
    """Плоское состояние: позиции нет, капитал есть (для размерных расчётов)."""
    return StrategyState(position=Decimal("0"), equity=EQUITY, bar_index=bar_index)


def _orders(
    strategy: TwoSidedMomentumStrategy, bar: Bar, state: StrategyState
) -> list[Order]:
    """Один бар через on_bar: список ордеров, который он породил."""
    return strategy.on_bar(bar, state)


def _run_strategy(
    strategy: TwoSidedMomentumStrategy, bars: list[Bar]
) -> list[list[Order]]:
    """Последовательность баров через on_bar: списки ордеров по барам, позиция flat."""
    return [strategy.on_bar(bar, _flat_state(index)) for index, bar in enumerate(bars)]


# Параметры


def test_constructor_validates_params() -> None:
    with pytest.raises(ValueError, match="donch_len"):
        TwoSidedMomentumStrategy(donch_len=Decimal("0"))
    with pytest.raises(ValueError, match="atr_mult"):
        TwoSidedMomentumStrategy(atr_mult=Decimal("-1"))


def test_bias_len_accepts_zero_as_disabled() -> None:
    s = TwoSidedMomentumStrategy(bias_len=Decimal("0"))
    assert s.bias_len == 0


# Реестр


def test_from_params_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="two_sided_momentum"):
        TwoSidedMomentumStrategy.from_params({"atr_len": Decimal("14")})


# Warmup: граница max(donch_len, _ATR_LEN)


def test_warmup_exposes_channel_only_when_both_windows_are_full() -> None:
    s = TwoSidedMomentumStrategy(donch_len=Decimal("3"), bias_len=Decimal("0"))
    bars = _rising_1m_bars(count=15 * 15)  # монотонно растущие 1m-бары: 15 завершённых 15m
    for bar in bars[: 14 * 15]:
        s.on_bar(bar, _flat_state())
    assert s._hh is None and s._ll is None and s._atr_value is None  # на баре 13 окно ещё неполно
    for bar in bars[14 * 15 :]:
        s.on_bar(bar, _flat_state())
    assert s._hh is not None and s._atr_value is not None  # на баре 15 полны оба окна
