"""Тесты стратегии ДВУСТОРОННИЙ ИМПУЛЬС (two_sided_momentum): синтетические 1m-бары.

Бары строятся так, чтобы пробой читался геометрией ленты, а не настройкой:
`_bars` — плато без единого выхода цены за границы (ATR = 0.5),
`_rising_1m_bars` — монотонный рост, где каждый close выше всех предыдущих
хаёв, `_bars_then_breakout` / `_bars_then_breakdown` — плато, чей close
растёт/падает, не задевая границы Дончиана, и завершается одним баром пробоя
(зеркально), `_downward_breakout` — лента 15m-баров из явного ряда закрытий,
`_signals` — все выпущенные за прогон ордера плоским списком. Тесты каркаса
смотрят на состояние (`_hh`, `_ll`, `_atr_value`) — приватные атрибуты
стратегии в тестах, как `_lower`/`_adx` у flat_keltner; тесты входа/брекета
смотрят на ордера.
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


def _bars_then_breakdown() -> list[Bar]:
    """Плато из 17 закрытых 15m-баров, пробой вниз на закрытом баре №18.

    Зеркало `_bars_then_breakout`: плато high=100.25, low=99.75 — истинный
    диапазон каждого бара ровно Decimal("0.5"), поэтому ATR Уайлдера сходится
    к 0.5 с 14-го закрытого бара и на баре сигнала равен ровно 0.5. Close
    плато падает на 0.01 за бар (ниже предыдущего close), но до 99.75 не
    доходит — двухбарный лоу Дончиана (donch_len=2, min предыдущих low) вниз
    не пробивается, а растущие хаи плато не дают случайного лонга. Финальный
    бар: close=low=99.5, high=100 — строго ниже ll=99.75, диапазон по-прежнему
    0.5 (гэпа нет: 99.84 − 99.5 = 0.34 < 0.5). Закрытых 15m-баров 18; граница
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
                close=FLAT_PRICE - RISE_STEP * index,
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
            high=FLAT_PRICE,
            low=FLAT_PRICE - step,
            close=FLAT_PRICE - step,
            volume=ONE,
        )
    )
    return bars


def _downward_breakout(closes: list[Decimal]) -> list[Bar]:
    """Лента 15m-баров из явного ряда закрытий: по 15 подрядных 1m-баров на бар.

    Коридор каждого 15m-бара: low на 0.25 под своим close, high — фитиль на
    0.25 над max(своего close, close следующего бара). Верхний фитиль,
    накрывающий следующий close, готовит подъём цены внутри чужого high,
    поэтому шаг вверх сам не пробивает хаёв Дончиана (нужно строго больше).
    Последний бар ряда — тот самый шаг вниз, у которого close ниже лоу двух
    предыдущих баров.
    """
    bars: list[Bar] = []
    for index, close in enumerate(closes):
        following = closes[index + 1] if index + 1 < len(closes) else close
        high = max(close, following) + FLAT_HALF
        low = close - FLAT_HALF
        for minute in range(15):  # полная 15m-группа: 1m-бары идут подряд
            spike = minute == 0
            bars.append(
                Bar(
                    ts=START + 900 * index + 60 * minute,
                    open=close,
                    high=high if spike else close,
                    low=low if spike else close,
                    close=close,
                    volume=ONE,
                )
            )
    return bars


def _flat_state(bar_index: int = 0) -> StrategyState:
    """Плоское состояние: позиции нет, капитал есть (для размерных расчётов)."""
    return StrategyState(position=Decimal("0"), equity=EQUITY, bar_index=bar_index)


def _long_state(bar_index: int = 0) -> StrategyState:
    """Смоделированный лонг 100 с бара 0: без заявки стратегии — ни брекета,
    ни pending-истории: список ордеров прогона несёт только сигнал переворота."""
    return StrategyState(position=Decimal("100"), equity=EQUITY, bar_index=bar_index)


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


def _signals(strategy: TwoSidedMomentumStrategy, bars: list[Bar]) -> list[Order]:
    """Все ордера, выпущенные за прогон, одним плоским списком (позиция flat).

    Generic-помощник задач 8–10: фильтру и выходам достаточно знать, что
    стратегия вообще сказала за ленту, с привязкой к барам.
    """
    return [order for group in _run_strategy(strategy, bars) for order in group]


def _signals_while_long(strategy: TwoSidedMomentumStrategy) -> list[Order]:
    """Прогон встречного пробоя вниз при смоделированном лонге: все ордера списком.

    Лента — `_bars_then_breakdown()`: 17 закрытых плато-баров и пробой вниз на
    закрытом баре №18 (граница warmup для donch_len=2 — бар №15, сигнал далеко
    после неё), позиция +100 смонирована с бара 0. Единственный легальный
    отклик — переворачивающий MARKET: снять лонг и открыть шорт одним ордером.
    """
    return [
        order
        for group in (
            strategy.on_bar(bar, _long_state(index))
            for index, bar in enumerate(_bars_then_breakdown())
        )
        for order in group
    ]


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
    # Pending израсходован: на следующем баре брекет не дублируется и вход не повторяется.
    repeat = _orders(s, _bars(1, first=len(bars) + 1)[0], filled)
    assert repeat == []
    # Никакого LIMIT-ордера стратегия не выдаёт вовсе: фиксированной цели в спеке нет.
    emitted = [order for group in orders + [bracket, repeat] for order in group]
    assert all(order.kind is not OrderKind.LIMIT for order in emitted)


# Шорт-вход и зеркальный стоп (задача 8)


def test_breakdown_below_donchian_opens_short() -> None:
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    orders = _run_strategy(s, _bars_then_breakdown())
    assert orders[-1] == [Order(Side.SELL, OrderKind.MARKET, Decimal("100"), None, None, False)]


def test_short_bracket_stop_is_above_entry() -> None:
    # шорт исполнен на баре с close=100, ATR frozen=0.5, atr_mult=2 → стоп=101 над входом
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    bars = _bars_then_breakdown()
    orders = _run_strategy(s, bars)  # прогон плоским состоянием до сигнала включительно
    assert orders[-1] == [Order(Side.SELL, OrderKind.MARKET, Decimal("100"), None, None, False)]
    # Движок исполнил MARKET знаковым позиционированием: следующий 1m-бар приходит
    # с позицией −100 (шорт), close=100; лонговая ветка брекета при этом молчит.
    fill_bar = _bars(1, first=len(bars))[0]
    filled = StrategyState(position=Decimal("-100"), equity=EQUITY, bar_index=len(bars))
    bracket = _orders(s, fill_bar, filled)
    assert bracket == [
        Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("101"), True)
    ]
    # Pending израсходован: на следующем баре брекет не дублируется и вход не повторяется.
    repeat = _orders(s, _bars(1, first=len(bars) + 1)[0], filled)
    assert repeat == []
    # И снова никакого LIMIT: зеркало лонгового теста, фиксированной цели нет.
    emitted = [order for group in orders + [bracket, repeat] for order in group]
    assert all(order.kind is not OrderKind.LIMIT for order in emitted)


def test_bias_filter_blocks_short_above_ema() -> None:
    s = TwoSidedMomentumStrategy(donch_len=Decimal("2"), bias_len=Decimal("2"))
    # 16 плато-баров по 100 (bias = 100), два верхних бара с close=110 — после
    # них bias = (100+110)/2 = 105, затем (105+110)/2 = 107.5, — и финальный
    # бар с close=108: ниже лоу двух предыдущих (109.75 = ll), но ВЫШЕ bias,
    # который на сигнальном баре = (107.5+108)/2 = 107.75. Шорт требует
    # close < bias, значит вход отрезан; фитиль предыдущего бара накрывает
    # следующий close, поэтому лонг не выстреливает ни на одном баре ленты.
    closes_above_bias = [FLAT_PRICE] * 16 + [Decimal("110"), Decimal("110"), Decimal("108")]
    assert _signals(s, _downward_breakout(closes_above_bias)) == []
    # Контраст на тех же барах с выключенным фильтром: пробой живой и открывает
    # ровно один шорт-маркет — пустой список выше заслуга фильтра, а не мёртвой ленты.
    unfiltered = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    emitted = _signals(unfiltered, _downward_breakout(closes_above_bias))
    assert [order.side for order in emitted] == [Side.SELL]
    assert [order.kind for order in emitted] == [OrderKind.MARKET]


# Переворот встречным сигналом (задача 9)


def test_opposite_signal_reverses_with_a_single_order() -> None:
    s = TwoSidedMomentumStrategy(donch_len=Decimal("2"), bias_len=Decimal("0"))
    orders = _signals_while_long(s)
    assert len(orders) == 1
    assert orders[0].side is Side.SELL and orders[0].qty > Decimal("100")


def test_same_direction_signal_while_position_is_blocked() -> None:
    # Сигнал того же направления, что позиция: не наращиваем, ордеров нет.
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    bars = _bars_then_breakout()
    long_run = [
        order
        for group in (s.on_bar(bar, _long_state(index)) for index, bar in enumerate(bars))
        for order in group
    ]
    assert long_run == []  # восходящий пробой при лонге +100 отрезан
    mirrored = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    short_run = [
        order
        for group in (
            mirrored.on_bar(
                bar, StrategyState(position=Decimal("-100"), equity=EQUITY, bar_index=index)
            )
            for index, bar in enumerate(_bars_then_breakdown())
        )
        for order in group
    ]
    assert short_run == []  # зеркало: нисходящий пробой при шорте −100 отрезан


def test_reversal_lifecycle_brackets_new_leg_on_fill_bar() -> None:
    # атр_mult=2 и ATR 0.5 → new_qty=100: переворачивающий MARKET несёт 200.
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    bars = _bars_then_breakdown()
    orders = [
        group
        for group in (s.on_bar(bar, _long_state(index)) for index, bar in enumerate(bars))
        if group
    ]
    assert orders == [[Order(Side.SELL, OrderKind.MARKET, Decimal("200"), None, None, False)]]
    # Движок исполнил SELL 200 по знаковой лонговой 100: закрытая часть сняла
    # лонг, остаток открыл шорт 100 (`Position.apply_fill`, ветка переворота) —
    # следующий 1m-бар приходит с позицией −100, и существующий брекетный путь
    # вешает стоп НОВОЙ ноги над входом: новой коды для этого нет.
    fill_bar = _bars(1, first=len(bars))[0]
    flipped = StrategyState(position=Decimal("-100"), equity=EQUITY, bar_index=len(bars))
    bracket = _orders(s, fill_bar, flipped)
    assert bracket == [
        Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("101"), True)
    ]
    # Pending израсходован на баре исполнения: второй брекет не дублируется.
    repeat = _orders(s, _bars(1, first=len(bars) + 1)[0], flipped)
    assert repeat == []


def test_second_reversal_signal_while_pending_is_blocked() -> None:
    # Первый переворачивающий MARKET ещё не исполнен (смоделированный лонг
    # живёт всю ленту): встречный сигнал следующего 15m-бара не должен давать
    # второй ордер — иначе комиссии входа разойдутся с net_pnl (Review Focus 2).
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    bars = _bars_then_breakdown()
    emitted = [
        order
        for group in (s.on_bar(bar, _long_state(index)) for index, bar in enumerate(bars))
        for order in group
    ]
    assert len(emitted) == 1  # сигнал бара №18 в силе, pending не израсходован
    # Доводка группы №19: закрытие 99 — ниже обновлённого ll=99.5, т.е. живой
    # встречный сигнал, а не пустой бар.
    price = Decimal("99")
    extra = [
        Bar(
            ts=START + 60 * (len(bars) + minute),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=ONE,
        )
        for minute in range(15)
    ]
    more = [
        order
        for group in (
            s.on_bar(bar, _long_state(len(bars) + index)) for index, bar in enumerate(extra)
        )
        for order in group
    ]
    assert more == []


def test_bias_filter_blocks_reversal_as_it_blocks_entry() -> None:
    # Та же лента, что в тесте фильтра шорта (T8), но с позиция long +100:
    # встречный сигнал блокируется фильтром уклона ровно как свежий вход.
    s = TwoSidedMomentumStrategy(donch_len=Decimal("2"), bias_len=Decimal("2"))
    bars = _downward_breakout(
        [FLAT_PRICE] * 16 + [Decimal("110"), Decimal("110"), Decimal("108")]
    )
    filtered = [
        order
        for group in (s.on_bar(bar, _long_state(index)) for index, bar in enumerate(bars))
        for order in group
    ]
    assert filtered == []
    # Контраст с выключенным фильтром: переворот живой — ровно один SELL.
    unfiltered = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    emitted = [
        order
        for group in (
            unfiltered.on_bar(bar, _long_state(index)) for index, bar in enumerate(bars)
        )
        for order in group
    ]
    assert len(emitted) == 1 and emitted[0].side is Side.SELL


def test_bracket_flat_call_emits_nothing() -> None:
    # м16: знаковая обработка ветки брекета явная (`> 0` / `< 0` / иначе),
    # плоскому вызову соответствует пустой список, а не стоп «наугад».
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    assert s._bracket_orders(_bars(1)[0], _flat_state(), Decimal("0.5")) == []


# Трейлинг ATR-стопа после входа (задача 10)


def _group_orders(
    strategy: TwoSidedMomentumStrategy,
    first: int,
    members: list[tuple[Decimal, Decimal, Decimal, Decimal]],
    position: Decimal,
) -> list[Order]:
    """Прогон 1m-баров группы (o, h, l, c), идущих подряд с индекса `first`.

    Возвращает все ордера группы одним списком: позиция в состоянии одна и та
    же на всю группу — движок в этих тестах смонирован externally.
    """
    emitted: list[Order] = []
    state = StrategyState(position=position, equity=EQUITY, bar_index=first)
    for minute, (open_, high, low, close) in enumerate(members):
        emitted.append(
            strategy.on_bar(
                Bar(
                    ts=START + 60 * (first + minute),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=ONE,
                ),
                state,
            )
        )
    return [order for group in emitted for order in group]


def _trail_long_to_high_105(s: TwoSidedMomentumStrategy) -> list[Order]:
    """Реальный вход лонга и лестница трейла до закрытого 15m-бара с high 105.

    Вход: `_bars_then_breakout()` (сигнал №18, замороженный ATR 0.5). Исполнение
    — нулевая минута группы №19 плоским 1m-баром close=100: брекет SELL на 99
    там же заводит состояние трейлинга (уровень 99, экстремумы 100/100). Далее
    группа №19 закрывается баром H=100.5/L=100/C=100.5 (TR ровно 0.5 — ATR
    Уайлдера остаётся ровно 0.5), а восемь лестничных групп №20–№28 поднимают
    high на 0.5 за бар до 105: каждый шаг — истинный диапазон 0.5, ATR 0.5 без
    изменения. Возвращает ордера финальной лестницы (bar high 105).
    """
    bars = _bars_then_breakout()
    _run_strategy(s, bars)
    flat = FLAT_PRICE
    h105 = Decimal("105")
    step = Decimal("0.5")
    # Нулевая минута группы №19: close 100 → брекет 99 и инициализация трейла.
    assert _group_orders(s, len(bars), [(flat, flat, flat, flat)], Decimal("100")) == [
        Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("99"), True)
    ]
    # Закрытие группы №19: H=100.5, L=100, C=100.5 — TR 0.5, трейл на 99.5.
    assert _group_orders(
        s,
        len(bars) + 1,
        [(Decimal("100"), Decimal("100.5"), Decimal("100"), Decimal("100.5"))] * 14,
        Decimal("100"),
    ) == [Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("99.5"), True)]
    # Лестница №20–№28: close каждого бара = high = prev close + 0.5 (TR 0.5);
    # встречных сигналов нет (шаг вверх — тот же блок one-order-per-fill уже
    # закреплял в тесте блокировки лонга при лонге).
    orders: list[Order] = []
    index = len(bars) + 15
    price = Decimal("100.5")
    for _ in range(9):
        top = price + step
        orders = _group_orders(
            s,
            index,
            [(price, top, price, top)] * 15,
            Decimal("100"),
        )
        price = top
        index += 15
    # price == 105: финальный бар — тот самый high 105 при ATR 0.5.
    assert price == h105
    return orders


def test_long_stop_trails_up_only() -> None:
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    orders = _trail_long_to_high_105(s)
    # Закрытый 15m-бар с high 105 при ATR ровно 0.5: candidate 104 = 105 − 2×0.5
    # (живой ATR, не замороженный) лучше уровня 103.5 → единственный ордер.
    assert orders == [
        Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("104"), True)
    ]
    # Следующий бар не лучше: high остаётся 105 (экстремум не уменьшается),
    # TR 0.5 → ATR 0.5 → candidate ровно 104 = уровень: строгое «лучше» не
    # выполнено, повторной заявки нет — стоп не переставляется на месте.
    flat_again = _group_orders(
        s,
        270 + 15 * 10,
        [(Decimal("104.5"), Decimal("105"), Decimal("104.5"), Decimal("105"))] * 15,
        Decimal("100"),
    )
    assert flat_again == []


def test_short_stop_trails_down_only() -> None:
    # Зеркало: вход шорта, брекет 101, лестница вниз до low 95, candidate
    # 96 = 95 + 2×0.5 (живой ATR 0.5) — ровно pinned-уровень.
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    bars = _bars_then_breakdown()
    _run_strategy(s, bars)
    flat = FLAT_PRICE
    step = Decimal("0.5")
    assert _group_orders(s, len(bars), [(flat, flat, flat, flat)], Decimal("-100")) == [
        Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("101"), True)
    ]
    assert _group_orders(
        s,
        len(bars) + 1,
        [(Decimal("99.5"), Decimal("100"), Decimal("99.5"), Decimal("99.5"))] * 14,
        Decimal("-100"),
    ) == [Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("100.5"), True)]
    orders: list[Order] = []
    index = len(bars) + 15
    price = Decimal("99.5")
    for _ in range(9):
        bottom = price - step
        orders = _group_orders(s, index, [(price, price, bottom, bottom)] * 15, Decimal("-100"))
        price = bottom
        index += 15
    assert price == Decimal("95")  # финальный бар: low 95 при ATR 0.5
    assert orders == [
        Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("96"), True)
    ]
    # Зеркальный «не лучше»: low остаётся 95, candidate ровно 96 = уровень —
    # заявка не повторяется.
    again = _group_orders(
        s,
        270 + 15 * 10,
        [(Decimal("95"), Decimal("95.5"), Decimal("95"), Decimal("95.5"))] * 15,
        Decimal("-100"),
    )
    assert again == []


def test_flatten_clears_trailing_state() -> None:
    # Плоское состояние без ожидания снимает экстремумы и уровень: мёртвых
    # полей нет, новая нога не может унаследовать старые значения.
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    _trail_long_to_high_105(s)
    assert s._stop_level is not None and s._since_high is not None
    for bar in _bars(15, first=270 + 15 * 10):  # группа №29 при позиции 0
        s.on_bar(bar, _flat_state())
    assert s._stop_level is None and s._since_high is None and s._since_low is None


def test_reversal_reinitialises_trailing_on_new_leg() -> None:
    # R2: переворот перезапускает трейлинг с экстремумов и уровня НОВОЙ ноги.
    # Старый лонг оттрейлен до high 105 (уровень стопа 104); переворот — баром
    # с истинным диапазоном 7.5 (ATR после него ровно 1.0), qty = 100 + 50;
    # исполнение — flat-баром close 97.5: брекет BUY на 99.5 = 97.5 + 2×1.0
    # и сброс экстремумов на 97.5. Первый закрытый бар новой ноги: L=97.5,
    # H=98.5, C=98 (TR 1.0 — ATR остаётся ровно 1.0) → candidate 97.5 + 2×1.0
    # = 99.5 = новый уровень: строго «лучше» нет → [] — со старым уровнем 104
    # стратегия ошибочно переставила бы стоп новой ноги на 99.5.
    s = TwoSidedMomentumStrategy(
        donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0")
    )
    _trail_long_to_high_105(s)
    assert s._stop_level == Decimal("104") and s._since_high == Decimal("105")
    # Бар переворота (группа №29): close 97.5 < ll=104, TR=7.5 → ATR ровно 1.0.
    reversed_bar = _group_orders(
        s,
        270 + 15 * 10,
        [(Decimal("97.5"), Decimal("104"), Decimal("97.5"), Decimal("97.5"))] * 15,
        Decimal("100"),
    )
    assert reversed_bar == [Order(Side.SELL, OrderKind.MARKET, Decimal("150"), None, None, False)]
    # Исполнение переворота: +100 − 150 = −50, брекет новой ноги на 99.5.
    fill = _group_orders(
        s,
        270 + 15 * 11,
        [(Decimal("97.5"), Decimal("97.5"), Decimal("97.5"), Decimal("97.5"))],
        Decimal("-50"),
    )
    assert fill == [
        Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("50"), None, Decimal("99.5"), True)
    ]
    assert s._since_high == Decimal("97.5") and s._stop_level == Decimal("99.5")
    # Закрытие группы №30 (14 минут): H=98.5, L=97.5, C=98 → TR 1.0, ATR 1.0;
    # candidate 99.5 = уровень — повторной заявки нет (со старым уровнем 104
    # была бы — это и есть отравление экстремумами прежней ноги).
    hold = _group_orders(
        s,
        270 + 15 * 11 + 1,
        [(Decimal("97.5"), Decimal("98.5"), Decimal("97.5"), Decimal("98"))] * 14,
        Decimal("-50"),
    )
    assert hold == []
    # Новый экстремум шортовой ноги (low 97) даёт candidate 99 < 99.5: заявка
    # идёт от экстремумов новой ноги, старый high 105 в расчёт не попал.
    trail = _group_orders(
        s,
        270 + 15 * 12,
        [(Decimal("97.5"), Decimal("98"), Decimal("97"), Decimal("97.5"))] * 15,
        Decimal("-50"),
    )
    assert trail == [
        Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("50"), None, Decimal("99"), True)
    ]
