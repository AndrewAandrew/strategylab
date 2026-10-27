"""Тесты стратегии ФЛЭТ (flat_keltner): синтетические 1m-бары, никакой сети.

Бары строятся так, чтобы условия стратегии срабатывали предсказуемо:
пила без направления (ADX держится нулём, ATR положителен), плавный рост
(ADX упирается в 100) и резкая проколка вниз (касание нижней границы).
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.strategy.base import Strategy, StrategyState
from strategylab.strategy.examples import UnknownStrategy, build
from strategylab.strategy.flat_keltner import FlatKeltnerStrategy

START = 1_717_200_000  # 2024-06-01T00:00:00Z: граница часа, 15m и 5H
ONE = Decimal("1")
SPAN = Decimal("0.5")
EQUITY = Decimal("10000")
COSTS = CostModel()
SLIPPAGE = COSTS.slippage

# Короткие длины индикаторов: 3 бара 5H на прогрев вместо 27, суть сигнала
# та же — режем время прогонов, а не проверку.
PARAMS: dict[str, Decimal] = {
    "adx_len": Decimal("3"),
    "adx_max": Decimal("20"),
    "ema_len": Decimal("3"),
    "channel_mult": Decimal("2"),
    "atr_len_regime": Decimal("3"),
    "atr_len_exit": Decimal("2"),
    "risk_frac": Decimal("0.015"),
    "reentry_frac": Decimal("0.6"),
}

# Пила без направления: ходит, но не растёт и не падает.
FLAT_PRICE = Decimal("100")
# Граница канала после прогрева: EMA = 100, ATR = 1, Lower = 100 - 2 * 1.
LOWER = Decimal("98")
# Проколка вниз внутри 15m-бара: low = 96.5, high = 100.5, close = 100.
DIP_LOW = "96.5"


def _zigzag(count: int, first: int = 0, price: Decimal = FLAT_PRICE) -> list[Bar]:
    """Минутные бары без направления: TR = 1, направленного движения нет."""
    return [
        Bar(
            ts=START + 60 * (first + i),
            open=price,
            high=price + SPAN,
            low=price - SPAN,
            close=price,
            volume=ONE,
        )
        for i in range(count)
    ]


def _rising(count: int, first: int = 0, base: Decimal = FLAT_PRICE) -> list[Bar]:
    """Плавный рост: направленное движение есть, ADX упирается в 100."""
    step = Decimal("0.01")
    return [
        Bar(
            ts=START + 60 * (first + i),
            open=base + step * i,
            high=base + step * i + SPAN,
            low=base + step * i - SPAN,
            close=base + step * i,
            volume=ONE,
        )
        for i in range(count)
    ]


def _dip(index: int, low: str, close: str) -> Bar:
    """Один бар-отвержение: проколка вниз и закрытие у верха диапазона."""
    return Bar(
        ts=START + 60 * index,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(low),
        close=Decimal(close),
        volume=ONE,
    )


def _run(
    strategy: FlatKeltnerStrategy,
    bars: list[Bar],
    *,
    position: Decimal = Decimal("0"),
    fill: bool = False,
    flat_after_fill: bool = False,
    equity: Decimal = EQUITY,
) -> list[tuple[int, list[Order]]]:
    """Прогон стратегии по барам.

    `fill=True` эмулирует исполнение MARKET-входа: позиция открывается на
    следующем баре, как это делает движок. `flat_after_fill=True` закрывает её
    сразу после бара исполнения — так видно, что сигналы продолжаются, если
    позиции нет.
    """
    out: list[tuple[int, list[Order]]] = []
    current = position
    pending: Decimal | None = None
    flat_from: int | None = None
    for index, bar in enumerate(bars):
        if pending is not None:
            current = pending
            pending = None
        if flat_from is not None and index > flat_from:
            current = Decimal("0")
        state = StrategyState(position=current, equity=equity, bar_index=index)
        orders = strategy.on_bar(bar, state)
        if not orders:
            continue
        out.append((index, orders))
        if fill and orders[0].kind is OrderKind.MARKET:
            pending = current + orders[0].qty
            if flat_after_fill:
                flat_from = index + 1
    return out


def _signal_bars() -> list[Bar]:
    """Прогрев (5 окон 5H) и 15m-бар с проколкой: 1529 минут, проколка в конце."""
    bars = _zigzag(1529)
    bars.append(_dip(1529, DIP_LOW, "100"))
    bars += _zigzag(30, first=1530)
    return bars


# Реестр и параметры


def test_registry_builds_flat_keltner() -> None:
    strategy = build("flat_keltner", {})
    assert strategy.name == "flat_keltner"
    assert isinstance(strategy, FlatKeltnerStrategy)


def test_registry_passes_params() -> None:
    strategy = build("flat_keltner", {"adx_max": Decimal("35")})
    assert isinstance(strategy, Strategy)


def test_registry_rejects_unknown_strategy() -> None:
    with pytest.raises(UnknownStrategy):
        build("flat_keltner_typo", {})


def test_registry_rejects_unknown_param() -> None:
    with pytest.raises(ValueError, match="неизвестные параметры"):
        build("flat_keltner", {"adx_lenght": Decimal("14")})


def test_defaults_match_the_spec() -> None:
    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(FlatKeltnerStrategy.__init__).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    assert defaults == {
        "adx_len": Decimal("14"),
        "adx_max": Decimal("20"),
        "ema_len": Decimal("20"),
        "channel_mult": Decimal("2"),
        "atr_len_regime": Decimal("20"),
        "atr_len_exit": Decimal("14"),
        "exit_mult": Decimal("1.5"),
        "risk_frac": Decimal("0.015"),
        "reentry_frac": Decimal("0.6"),
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("adx_len", Decimal("0")),
        ("adx_len", Decimal("-1")),
        ("adx_len", Decimal("2.5")),
        ("adx_max", Decimal("0")),
        ("ema_len", Decimal("0")),
        ("ema_len", Decimal("3.5")),
        ("channel_mult", Decimal("0")),
        ("atr_len_regime", Decimal("0")),
        ("atr_len_exit", Decimal("0")),
        ("exit_mult", Decimal("0")),
        ("exit_mult", Decimal("-1")),
        ("risk_frac", Decimal("0")),
        ("risk_frac", Decimal("1.5")),
        ("reentry_frac", Decimal("0")),
        ("reentry_frac", Decimal("1.2")),
    ],
)
def test_invalid_params_are_rejected(name: str, value: Decimal) -> None:
    with pytest.raises(ValueError, match=name):
        build("flat_keltner", {name: value})


def test_strategy_satisfies_protocol() -> None:
    assert isinstance(FlatKeltnerStrategy(), Strategy)


# Режим: тренд не торгуется


def test_no_entry_in_a_trend() -> None:
    """Рост с проколкой к нижней границе: касание есть, режим — тренд."""
    bars = _rising(1529)
    bars.append(_dip(1529, "104", "115"))
    bars += _rising(30, first=1530, base=Decimal("115.29"))
    strategy = FlatKeltnerStrategy(**PARAMS)

    result = _run(strategy, bars)

    assert result == []
    assert strategy.last_adx is not None
    assert strategy.last_adx > PARAMS["adx_max"]


def test_same_bars_enter_when_adx_threshold_is_loose() -> None:
    """Та же серия без порога ADX: сигнал есть — значит, блокировал именно режим."""
    bars = _rising(1529)
    bars.append(_dip(1529, "104", "115"))
    bars += _rising(30, first=1530, base=Decimal("115.29"))
    strategy = FlatKeltnerStrategy(**{**PARAMS, "adx_max": Decimal("1000")})

    result = _run(strategy, bars)

    assert len(result) == 1
    assert result[0][1][0].side is Side.BUY


# Вход


def test_no_entry_without_a_touch() -> None:
    """Пила без проколки: цена не доходит до нижней границы канала."""
    strategy = FlatKeltnerStrategy(**PARAMS)

    result = _run(strategy, _zigzag(1560))

    assert result == []
    assert strategy.last_lower == LOWER


def test_entry_is_a_market_buy_with_size_by_risk() -> None:
    """Размер: equity * 1.5% / (1.5 * ATR15m). ATR15m на сигнальном баре = 2.5."""
    strategy = FlatKeltnerStrategy(**PARAMS)

    result = _run(strategy, _signal_bars())

    assert len(result) == 1
    index, orders = result[0]
    assert index == 1529
    assert len(orders) == 1
    order = orders[0]
    assert order.side is Side.BUY
    assert order.kind is OrderKind.MARKET
    assert order.reduce_only is False
    assert order.qty == Decimal("40")
    assert order.qty == EQUITY * PARAMS["risk_frac"] / (Decimal("1.5") * Decimal("2.5"))
    assert order.limit_price is None
    assert order.stop_price is None


def test_entry_is_skipped_without_capital() -> None:
    """Нулевой капитал: размер вышел бы нулевым, сделки нет."""
    strategy = FlatKeltnerStrategy(**PARAMS)

    result = _run(strategy, _signal_bars(), equity=Decimal("0"))

    assert result == []


def test_no_second_entry_while_the_position_is_open() -> None:
    """Позиция открыта — новых входов нет, даже когда условия снова выполняются."""
    bars = _zigzag(1529)
    bars.append(_dip(1529, DIP_LOW, "100"))
    bars += _zigzag(14, first=1530)
    bars.append(_dip(1544, DIP_LOW, "100"))
    bars += _zigzag(14, first=1545)
    bars.append(_dip(1559, DIP_LOW, "100"))
    bars += _zigzag(30, first=1560)

    held = _run(FlatKeltnerStrategy(**PARAMS), bars, fill=True)
    released = _run(
        FlatKeltnerStrategy(**PARAMS),
        bars,
        fill=True,
        flat_after_fill=True,
    )

    held_entries = [orders for _, orders in held if orders[0].kind is OrderKind.MARKET]
    released_entries = [orders for _, orders in released if orders[0].kind is OrderKind.MARKET]
    assert len(held_entries) == 1
    assert len(released_entries) == 3


# Выходы


def test_bracket_stops_and_target_appear_after_the_entry_fills() -> None:
    """Брекет ставится на баре после исполнения входа: стоп ниже, цель выше."""
    strategy = FlatKeltnerStrategy(**PARAMS)

    result = _run(strategy, _signal_bars(), fill=True)

    assert len(result) == 2
    entry_index, entry_orders = result[0]
    bracket_index, bracket = result[1]
    assert entry_index == 1529
    assert bracket_index == 1530
    assert len(bracket) == 2
    stop, target = bracket
    assert stop.side is Side.SELL
    assert stop.kind is OrderKind.STOP_MARKET
    assert stop.stop_price == Decimal("96.25")
    assert stop.limit_price is None
    assert stop.reduce_only is True
    assert target.side is Side.SELL
    assert target.kind is OrderKind.LIMIT
    assert target.limit_price == Decimal("103.75")
    assert target.stop_price is None
    assert target.reduce_only is True
    assert stop.qty == entry_orders[0].qty
    assert target.qty == entry_orders[0].qty


def test_exit_mult_moves_the_bracket_and_the_size() -> None:
    """exit_mult меняет и ширину брекета, и размер: риск на сделку — risk_frac.

    ATR15m на сигнальном баре = 2.5, вход = 100: стоп 100 - 2 * 2.5 = 95,
    цель 100 + 2 * 2.5 = 105, размер 10000 * 0.015 / (2 * 2.5) = 30.
    """
    strategy = FlatKeltnerStrategy(**{**PARAMS, "exit_mult": Decimal("2")})

    result = _run(strategy, _signal_bars(), fill=True)

    assert len(result) == 2
    entry_orders = result[0][1]
    assert entry_orders[0].qty == Decimal("30")
    stop, target = result[1][1]
    assert stop.stop_price == Decimal("95")
    assert target.limit_price == Decimal("105")


# Неполные группы


def test_incomplete_15m_group_is_not_a_signal() -> None:
    """Проколка попала в окно, но окно не закрылось — сигнала нет."""
    bars = _zigzag(1522)
    bars.append(_dip(1522, DIP_LOW, "100"))
    bars += _zigzag(6, first=1523)
    strategy = FlatKeltnerStrategy(**PARAMS)

    result = _run(strategy, bars)

    assert strategy.last_lower is not None
    assert Decimal(DIP_LOW) <= strategy.last_lower  # цена касалась границы
    assert result == []


def test_incomplete_5h_group_does_not_advance_the_channel() -> None:
    """Окно 5H без последнего 15m-бара не собирается и канал не двигает."""
    bars = _zigzag(1785)
    strategy = FlatKeltnerStrategy(**PARAMS)

    _run(strategy, bars[:1500])
    lower_before = strategy.last_lower
    _run(strategy, bars[1500:])

    assert lower_before is not None
    assert strategy.last_lower == lower_before


# Движок: исполнение входа, выход по стопу, одна сделка


class _ListFeed:
    """Лента из списка баров: стратегия работает на 1m-барах."""

    interval = Interval.MINUTE

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class _RecordingBroker(BrokerSimulator):
    """Брокер, запоминающий филлы, применённые через apply_bar."""

    def __init__(self, costs: CostModel, intrabar: IntrabarOrder) -> None:
        super().__init__(costs, intrabar)
        self.fills: list[Fill] = []

    def apply_bar(self, bar: Bar) -> list[Fill]:
        fills = super().apply_bar(bar)
        self.fills.extend(fills)
        return fills


def test_engine_fills_the_entry_and_stops_out_once() -> None:
    """Вход исполняется по закрытию бара, стоп срабатывает на следующем баре."""
    bars = _signal_bars()
    bars.append(_dip(1530, "95", "100"))
    broker = _RecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=EQUITY)

    Engine().run(
        _ListFeed(bars),
        FlatKeltnerStrategy(**PARAMS),
        broker,
        metrics,
    )

    assert [fill.reason for fill in broker.fills] == ["market", "stop"]
    entry, exit_fill = broker.fills
    assert entry.side is Side.BUY
    assert entry.price == Decimal("100") * (Decimal(1) + SLIPPAGE)
    assert entry.qty == Decimal("40")
    assert exit_fill.side is Side.SELL
    assert exit_fill.price == Decimal("96.25") * (Decimal(1) - SLIPPAGE)
    assert exit_fill.qty == Decimal("40")
    assert not broker.position.is_open
    assert metrics.trades == 1
    assert metrics.losses == 1


# Журнал канала: выключен по умолчанию, включение ничего не меняет


def test_channel_log_is_off_by_default_and_enabling_changes_nothing() -> None:
    """Без включения журнал пуст; с включением ордера те же, что без него."""
    bars = _signal_bars()

    plain = FlatKeltnerStrategy(**PARAMS)
    plain_orders = _run(plain, bars, fill=True)
    assert plain.channel_log == ()

    logged = FlatKeltnerStrategy(**PARAMS)
    logged.enable_channel_log()
    logged_orders = _run(logged, bars, fill=True)

    assert logged_orders == plain_orders
    assert logged.channel_log


# окно 15m (900 с) и окно 5H (18000 с): те же якоря по эпохе, что в стратегии
FIFTEEN_MIN_WIDTH = 900
FIVE_HOURS_WIDTH = 18000


def test_channel_log_points_lie_on_15m_bars_and_form_a_staircase() -> None:
    """Точки — целые 15m-бары; значения меняются только через закрытие 5H.

    Точка несёт канал последнего закрытого 5H-бара: между закрытиями 5H
    (границы окон по `ts // 18000` совпадают) значения не двигаются.
    """
    strategy = FlatKeltnerStrategy(**PARAMS)
    strategy.enable_channel_log()
    _run(strategy, _signal_bars(), fill=True)

    points = strategy.channel_log
    assert points
    for point in points:
        assert point.ts % FIFTEEN_MIN_WIDTH == 0
    for previous, current in zip(points, points[1:], strict=False):
        assert current.ts > previous.ts
        if previous.ts // FIVE_HOURS_WIDTH == current.ts // FIVE_HOURS_WIDTH:
            assert (previous.mid, previous.upper, previous.lower) == (
                current.mid,
                current.upper,
                current.lower,
            )


def test_channel_log_last_point_matches_the_strategy_channel() -> None:
    """Последняя точка — тот канал, по которому стратегия видит границу сейчас."""
    strategy = FlatKeltnerStrategy(**PARAMS)
    strategy.enable_channel_log()
    _run(strategy, _signal_bars(), fill=True)

    points = strategy.channel_log
    assert points
    if strategy.last_lower is not None:
        assert points[-1].lower == strategy.last_lower
