"""Тесты стратегии ДВУСТОРОННИЙ ИМПУЛЬС (two_sided_momentum): синтетические 1m-бары.

Бары строятся так, чтобы пробой читался геометрией ленты, а не настройкой:
`_bars` — плато без единого выхода цены за границы (ATR = 0.5),
`_rising_1m_bars` — монотонный рост, где каждый close выше всех предыдущих
хаёв, `_bars_then_breakout` — плато, чей close растёт, не задевая границы
Дончиана, и завершается одним баром пробоя. Тесты каркаса смотрят на состояние (`_hh`,
`_ll`, `_atr_value`) — приватные атрибуты стратегии в тестах, как
`_lower`/`_adx` у flat_keltner; тесты входа/брекета смотрят на ордера.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from strategylab.broker.orders import Order, OrderKind, Side
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


def _bars_then_breakout() -> list[Bar]:
    """Плато из 17 закрытых 15m-баров, пробой вверх на закрытом баре №18.

    Плато: high=100.25, low=99.75 — истинный диапазон каждого бара ровно
    Decimal("0.5"), поэтому ATR Уайлдера сходится к 0.5 с 14-го закрытого
    бара и на баре сигнала равен ровно 0.5. Close плато растёт на 0.01 за бар
    (выше предыдущего close), но до 100.25 не доходит — двухбарный хаёв
    Дончиана (donch_len=2, max предыдущих high) не пробивается. Финальный бар:
    close=high=100.5, low=100 — строго выше hh=100.25, диапазон по-прежнему
    0.5 (гэпа нет: 100.5 − 100.16 = 0.34 < 0.5). Закрытых 15m-баров 18; граница
    warmup для donch_len=2 — бар №15 (max(2, 14) + 1), пробой значительно после
    неё.
    """
    bars: list[Bar] = []
    for index in range(17):
        bars.extend(_bars(14, first=len(bars)))
        bars.append(
            Bar(
                ts=START + 60 * len(bars),
                open=FLAT_PRICE,
                high=FLAT_PRICE + FLAT_HALF,
                low=FLAT_PRICE - FLAT_HALF,
                close=FLAT_PRICE + RISE_STEP * index,
                volume=ONE,
            )
        )
    step = Decimal("0.5")
    for _ in range(14):
        bars.append(
            Bar(
                ts=START + 60 * len(bars),
                open=FLAT_PRICE,
                high=FLAT_PRICE,
                low=FLAT_PRICE,
                close=FLAT_PRICE,
                volume=ONE,
            )
        )
    bars.append(
        Bar(
            ts=START + 60 * len(bars),
            open=FLAT_PRICE,
            high=FLAT_PRICE + step,
            low=FLAT_PRICE,
            close=FLAT_PRICE + step,
            volume=ONE,
        )
    )
    return bars


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


# Лонг-вход и стоп-брекет (задача 7)


def test_breakout_above_donchian_opens_long_market_order() -> None:
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    bars = _bars_then_breakout()
    orders = _run_strategy(s, bars)
    assert orders[-1] == [Order(Side.BUY, OrderKind.MARKET, Decimal("100"), None, None, False)]


def test_stop_bracket_is_placed_on_the_fill_bar() -> None:
    # вход исполнен на баре с close=100, ATR frozen=0.5, atr_mult=2 → stop=99
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    bars = _bars_then_breakout()
    orders = _run_strategy(s, bars)  # прогон плоским состоянием до сигнала включительно
    assert orders[-1] == [Order(Side.BUY, OrderKind.MARKET, Decimal("100"), None, None, False)]
    # Движок исполнил MARKET: следующий 1m-бар приходит с позицией 100, close=100.
    fill_bar = _bars(1, first=len(bars))[0]
    filled = StrategyState(position=Decimal("100"), equity=EQUITY, bar_index=len(bars))
    bracket = _orders(s, fill_bar, filled)
    assert bracket == [
        Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("99"), True)
    ]
    #Pending израсходован: на следующем баре брекет не дублируется и вход не повторяется.
    repeat = _orders(s, _bars(1, first=len(bars) + 1)[0], filled)
    assert repeat == []
    # Никакого LIMIT-ордера стратегия не выдаёт вовсе: фиксированной цели в спеке нет.
    emitted = [order for group in orders + [bracket, repeat] for order in group]
    assert all(order.kind is not OrderKind.LIMIT for order in emitted)
