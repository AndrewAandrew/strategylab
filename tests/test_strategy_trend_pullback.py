"""Тесты стратегии ТРЕНД (trend_pullback): синтетические 1m-бары, никакой сети.

Бары строятся как «ступени»: внутри каждого 5H-окна цена плоская, между
окнами уровень шагает на `step`. Тогда 5H-закрытия лежат точно на линии
регрессии (σ = 0, наклон = `step`), ADX упирается в 100, а 15m-лозы хаёв
задаются явно — касание границы, higher low и слом хаёв собираются по одному.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
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
from strategylab.strategy.trend_pullback import TrendPullbackStrategy

START = 1_717_200_000  # 2024-06-01T00:00:00Z: граница часа, 15m и 5H
ONE = Decimal("1")
EQUITY = Decimal("10000")
COSTS = CostModel()
SLIPPAGE = COSTS.slippage

# Окна агрегации те же, что внутри стратегии: якоря по эпохе.
FIFTEEN_MIN_WIDTH = 900
FIVE_HOURS_WIDTH = 18000
FIVE_HOURS_BARS = 20

# Короткие длины индикаторов: 5 баров 5H на прогрев ADX вместо 27, 4 на
# регрессию вместо 50. Суть сигналов та же — режем время прогонов, а не проверку.
PARAMS: dict[str, Decimal] = {
    "adx_len": Decimal("3"),
    "adx_min": Decimal("25"),
    "lr_len": Decimal("4"),
    "sigma_mult": Decimal("2"),
    "atr_len": Decimal("2"),
    "swing_len": Decimal("4"),
    "stop_atr": Decimal("1.0"),
    "tp_mult": Decimal("2.5"),
    "risk_frac": Decimal("0.015"),
}

# Плато ADX в коридоре после прогрева (5 закрытий 5H): +DM чередуется с −DM,
# сглаживания оседают ≠, и DX держится на этой величине — она ниже порога 25.
CORRIDOR_ADX = Decimal("19.92063492063492063492063492")


def _minute(index: int, p: Decimal, hi: Decimal, lo: Decimal) -> Bar:
    """Минутный бар внутри плоского 15m-бара: open = close = p ± смещения."""
    return Bar(
        ts=START + 60 * index,
        open=p,
        high=p + hi,
        low=p - lo,
        close=p,
        volume=ONE,
    )


def _fifteen_minutes(k: int, level: Decimal, hi: Decimal, lo: Decimal) -> list[Bar]:
    """15 минутных баров одного 15m-бара k: все несут одни hi/lo."""
    return [_minute(15 * k + i, level, hi, lo) for i in range(15)]


def _staircase(
    count_15m: int,
    *,
    step: Decimal = Decimal("0.5"),
    base: Decimal = Decimal("100"),
    overrides: Mapping[int, tuple[Decimal, Decimal]] | None = None,
) -> list[Bar]:
    """Ступени между 5H-окнами: внутри окна цена плоская, окна шагают на `step`.

    5H-закрытия идут по линии точно (σ = 0, наклон = `step`), ADX = 100.
    В каждом окне вход-кандидат — первый 15m-бар: его лоза касается границы
    прошлого окна (low = lower), дальше внутри окна цена выше, а higher low
    и слом держатся на равенствах. Оверрайды ({индекс 15m-бара: (hi, lo)})
    правят один 15m-бар целиком — так изолируется одно из трёх условий входа.
    """
    crafted = overrides if overrides is not None else {}
    bars: list[Bar] = []
    for k in range(count_15m):
        window = k // FIVE_HOURS_BARS
        level = base + step * window
        hi, lo = crafted.get(k, (Decimal("0.5"), Decimal("0.5")))
        bars.extend(_fifteen_minutes(k, level, hi, lo))
    return bars


def _corridor(
    count_15m: int,
    *,
    overrides: Mapping[int, tuple[Decimal, Decimal]] | None = None,
) -> list[Bar]:
    """Коридор: закрытия растут (0.2 за окно), хаи и лозы отражаются между окнами.

    Чётные окна (кроме нулевого) поднимают хай на 2, нечётные опускают лозу
    на 2.2: +DM чередуется с −DM, ADX оседает на плато 19.92 — ниже порога 25,
    хотя наклон положителен. Режиму мешает только ADX.
    """
    crafted = overrides if overrides is not None else {}
    bars: list[Bar] = []
    # Окно 0: хай = 101, лоза = 99 (offset по 1). Дальше рекуррентно по окнам.
    high_base = Decimal("101")
    low_base = Decimal("99")
    highs: dict[int, Decimal] = {}
    lows: dict[int, Decimal] = {}
    for window in range((count_15m + FIVE_HOURS_BARS - 1) // FIVE_HOURS_BARS + 1):
        level = Decimal("100") + Decimal("0.2") * window
        if window % 2:
            low_base -= Decimal("2.2")
            high_base = level
        elif window:
            high_base += Decimal("2")
            low_base = level - Decimal("1")
        highs[window] = high_base - level
        lows[window] = level - low_base
    for k in range(count_15m):
        window = k // FIVE_HOURS_BARS
        level = Decimal("100") + Decimal("0.2") * window
        if k in crafted:
            hi, lo = crafted[k]
        else:
            hi, lo = highs[window], lows[window]
        bars.extend(_fifteen_minutes(k, level, hi, lo))
    return bars


def _run(
    strategy: TrendPullbackStrategy,
    bars: list[Bar],
    *,
    position: Decimal = Decimal("0"),
    fill: bool = False,
    flat_after_fill: bool = False,
    equity: Decimal = EQUITY,
) -> list[tuple[int, list[Order]]]:
    """Прогон стратегии по барам (эмуляция движка).

    `fill=True` открывает позицию на следующем баре после MARKET-входа,
    `flat_after_fill=True` закрывает её сразу после бара исполнения.
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


def _corridor_bars() -> list[Bar]:
    """Коридор с крафтовой парой: k=100 проваливает higher low, k=101 — кандидат.

    У k=101 все три условия входа выполнены: лоза 100 <= lower 100.8,
    higher low 100 > 99.8, слом хаёв 102.8 > 101.8. Сигнал блокирует ADX.
    """
    return _corridor(110, overrides={101: (Decimal("1.8"), Decimal("1"))})


# Реестр и параметры


def test_registry_builds_trend_pullback() -> None:
    strategy = build("trend_pullback", {})
    assert strategy.name == "trend_pullback"
    assert isinstance(strategy, TrendPullbackStrategy)


def test_registry_passes_params() -> None:
    strategy = build("trend_pullback", {"adx_min": Decimal("30")})
    assert isinstance(strategy, Strategy)


def test_registry_rejects_unknown_strategy() -> None:
    with pytest.raises(UnknownStrategy):
        build("trend_pullback_typo", {})


def test_registry_rejects_unknown_param() -> None:
    with pytest.raises(ValueError, match="неизвестные параметры"):
        build("trend_pullback", {"atr_lenght": Decimal("14")})


def test_defaults_match_the_spec() -> None:
    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(TrendPullbackStrategy.__init__).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    assert defaults == {
        "adx_len": Decimal("14"),
        "adx_min": Decimal("25"),
        "lr_len": Decimal("50"),
        "sigma_mult": Decimal("2"),
        "atr_len": Decimal("14"),
        "swing_len": Decimal("10"),
        "stop_atr": Decimal("1.0"),
        "tp_mult": Decimal("2.5"),
        "risk_frac": Decimal("0.015"),
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("adx_len", Decimal("0")),
        ("adx_len", Decimal("-1")),
        ("adx_len", Decimal("2.5")),
        ("adx_min", Decimal("0")),
        ("lr_len", Decimal("0")),
        ("lr_len", Decimal("1")),
        ("lr_len", Decimal("2.5")),
        ("sigma_mult", Decimal("0")),
        ("atr_len", Decimal("0")),
        ("atr_len", Decimal("3.5")),
        ("swing_len", Decimal("0")),
        ("swing_len", Decimal("4.5")),
        ("stop_atr", Decimal("0")),
        ("stop_atr", Decimal("-1")),
        ("tp_mult", Decimal("0")),
        ("risk_frac", Decimal("0")),
        ("risk_frac", Decimal("1.5")),
    ],
)
def test_invalid_params_are_rejected(name: str, value: Decimal) -> None:
    with pytest.raises(ValueError, match=name):
        build("trend_pullback", {name: value})


def test_strategy_satisfies_protocol() -> None:
    assert isinstance(TrendPullbackStrategy(), Strategy)


# Режим: наклон и ADX смотрят на 5H


def test_no_entry_when_slope_is_not_positive() -> None:
    """Снижающиеся закрытия: наклон −0.5, ADX 100 — вход блокирует наклон.

    Крафтовые бары k=99 (хай = предыдущему, лоза ниже) и k=100 (выше первого
    из них) делают так, что у k=100 касание, higher low и слом выполнены.
    """
    bars = _staircase(
        108,
        step=Decimal("-0.5"),
        overrides={
            99: (Decimal("0"), Decimal("1")),
            100: (Decimal("0.6"), Decimal("0")),
        },
    )
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, bars)

    assert result == []
    assert strategy.last_slope == Decimal("-0.5")
    assert strategy.last_adx == Decimal("100")
    assert strategy.last_lower == Decimal("98")


def test_no_entry_when_adx_is_below_the_threshold() -> None:
    """Коридор: наклон 0.2, ADX 19.92 < 25 — вход блокирует режим, не наклон."""
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, _corridor_bars())

    assert result == []
    assert strategy.last_adx == CORRIDOR_ADX
    assert strategy.last_slope == Decimal("0.2")
    assert strategy.last_lower == Decimal("100.8")


def test_same_bars_enter_when_adx_threshold_is_loose() -> None:
    """Та же серия с порогом 15: сигнал есть — значит, блокировал именно ADX."""
    strategy = TrendPullbackStrategy(**{**PARAMS, "adx_min": Decimal("15")})

    result = _run(strategy, _corridor_bars())

    assert len(result) == 1
    index, orders = result[0]
    assert index == 1529
    order = orders[0]
    assert order.side is Side.BUY
    assert order.kind is OrderKind.MARKET
    assert order.reduce_only is False
    assert order.qty > 0


# Вход: откат, higher low, слом


def test_no_entry_without_a_touch() -> None:
    """Шаг 0.75: лозы остаются над границей прошлого окна."""
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, _staircase(108, step=Decimal("0.75")))

    assert result == []
    assert strategy.last_lower == Decimal("103")
    assert strategy.last_slope == Decimal("0.75")


def test_no_entry_without_a_higher_low() -> None:
    """Крафтовый k=100 с глубокой лозой: higher low не формируется."""
    bars = _staircase(110, overrides={100: (Decimal("0.5"), Decimal("1"))})
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, bars)

    assert result == []
    assert strategy.last_lower == Decimal("102")


def test_no_entry_without_a_break_of_the_high() -> None:
    """Крафтовый k=100 без роста хаёв: микро-структура не сломана."""
    bars = _staircase(110, overrides={100: (Decimal("0"), Decimal("0.5"))})
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, bars)

    assert result == []
    assert strategy.last_lower == Decimal("102")


def test_entry_is_a_market_buy_with_size_by_risk() -> None:
    """Размер: equity * 1.5% / риск. Риск = 102.5 − (SwingLow 101.5 − ATR 1)."""
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, _staircase(110))

    assert len(result) == 1
    index, orders = result[0]
    assert index == 1514
    assert len(orders) == 1
    order = orders[0]
    assert order.side is Side.BUY
    assert order.kind is OrderKind.MARKET
    assert order.reduce_only is False
    assert order.limit_price is None
    assert order.stop_price is None
    assert strategy.last_lower == Decimal("102")
    assert strategy.last_slope == Decimal("0.5")
    assert strategy.last_adx == Decimal("100")
    assert strategy.last_atr15m == Decimal("1")
    assert order.qty == Decimal("75")
    assert order.qty == EQUITY * PARAMS["risk_frac"] / Decimal("2")


def test_entry_is_skipped_without_capital() -> None:
    """Нулевой капитал: размер вышел бы нулевым, сделки нет."""
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, _staircase(110), equity=Decimal("0"))

    assert result == []


def test_no_second_entry_while_the_position_is_open() -> None:
    """Позиция открыта — новых входов нет, даже когда условия выполняются."""

    bars = _staircase(122)  # 1830 минут: окна 5 и 6 целиком, старт сигнала окна 6

    held = _run(TrendPullbackStrategy(**PARAMS), bars, fill=True)
    released = _run(
        TrendPullbackStrategy(**PARAMS),
        bars,
        fill=True,
        flat_after_fill=True,
    )

    assert [(index, len(orders)) for index, orders in held] == [
        (1514, 1),
        (1515, 2),
    ]
    held_entries = [orders for _, orders in held if orders[0].kind is OrderKind.MARKET]
    assert len(held_entries) == 1
    assert [(index, len(orders)) for index, orders in released] == [
        (1514, 1),
        (1515, 2),
        (1814, 1),
        (1815, 2),
    ]


# Выходы: брекет на закрытии бара исполнения


def test_bracket_stops_and_target_appear_after_the_entry_fills() -> None:
    """Брекет ставится на баре после исполнения входа: стоп ниже, цель выше."""
    strategy = TrendPullbackStrategy(**PARAMS)

    result = _run(strategy, _staircase(110), fill=True)

    assert len(result) == 2
    entry_index, entry_orders = result[0]
    bracket_index, bracket = result[1]
    assert entry_index == 1514
    assert bracket_index == 1515
    assert len(bracket) == 2
    stop, target = bracket
    assert stop.side is Side.SELL
    assert stop.kind is OrderKind.STOP_MARKET
    assert stop.stop_price == Decimal("100.5")
    assert stop.limit_price is None
    assert stop.reduce_only is True
    assert target.side is Side.SELL
    assert target.kind is OrderKind.LIMIT
    assert target.limit_price == Decimal("107.5")
    assert target.stop_price is None
    assert target.reduce_only is True
    assert stop.qty == entry_orders[0].qty
    assert stop.qty == Decimal("75")
    assert target.qty == Decimal("75")


def test_stop_atr_moves_the_stop_the_size_and_the_target() -> None:
    """stop_atr = 1.5: стоп 101.5 − 1.5 = 100, риск 2.5, qty 60, цель 106.25."""
    strategy = TrendPullbackStrategy(**{**PARAMS, "stop_atr": Decimal("1.5")})

    result = _run(strategy, _staircase(110), fill=True)

    assert result[0][1][0].qty == Decimal("60")
    stop, target = result[1][1]
    assert stop.stop_price == Decimal("100")
    assert target.limit_price == Decimal("108.75")


def test_tp_mult_scales_the_target_but_not_the_size() -> None:
    """tp_mult меняет только цель: риск-единица и размер остаются теми же."""
    strategy = TrendPullbackStrategy(**{**PARAMS, "tp_mult": Decimal("2")})

    result = _run(strategy, _staircase(110), fill=True)

    assert result[0][1][0].qty == Decimal("75")
    stop, target = result[1][1]
    assert stop.stop_price == Decimal("100.5")
    assert target.limit_price == Decimal("106.5")


# Движок: исполнение входа, выход по стопу / по цели


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
    """Вход по закрытию бара 1515, стоп срабатывает на баре с проколкой 99."""
    bars = _staircase(103)
    bars[1516] = Bar(
        ts=START + 60 * 1516,
        open=Decimal("102.5"),
        high=Decimal("102.5"),
        low=Decimal("99"),
        close=Decimal("99"),
        volume=ONE,
    )
    broker = _RecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=EQUITY)

    Engine().run(
        _ListFeed(bars),
        TrendPullbackStrategy(**PARAMS),
        broker,
        metrics,
    )

    assert [fill.reason for fill in broker.fills] == ["market", "stop"]
    entry, exit_fill = broker.fills
    assert entry.side is Side.BUY
    assert entry.price == Decimal("102.5") * (Decimal(1) + SLIPPAGE)
    assert entry.qty == Decimal("75")
    assert exit_fill.side is Side.SELL
    assert exit_fill.price == Decimal("100.5") * (Decimal(1) - SLIPPAGE)
    assert exit_fill.qty == Decimal("75")
    assert not broker.position.is_open
    assert metrics.trades == 1
    assert metrics.losses == 1


def test_engine_fills_the_entry_and_takes_profit_once() -> None:
    """Бар 1516 без гэпа вниз, но с гэпом вверх: цель исполняется по уровню."""
    bars = _staircase(103)
    bars[1516] = Bar(
        ts=START + 60 * 1516,
        open=Decimal("108.5"),
        high=Decimal("108.5"),
        low=Decimal("102.5"),
        close=Decimal("107.5"),
        volume=ONE,
    )
    broker = _RecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=EQUITY)

    Engine().run(
        _ListFeed(bars),
        TrendPullbackStrategy(**PARAMS),
        broker,
        metrics,
    )

    assert [fill.reason for fill in broker.fills] == ["market", "take_profit"]
    entry, exit_fill = broker.fills
    assert entry.side is Side.BUY
    assert entry.price == Decimal("102.5") * (Decimal(1) + SLIPPAGE)
    assert entry.qty == Decimal("75")
    assert exit_fill.side is Side.SELL
    assert exit_fill.price == Decimal("107.5")
    assert exit_fill.qty == Decimal("75")
    assert not broker.position.is_open
    assert metrics.trades == 1
    assert metrics.wins == 1


# Журнал канала: выключен по умолчанию, включение ничего не меняет


def test_channel_log_is_off_by_default_and_enabling_changes_nothing() -> None:
    """Без включения журнал пуст; с включением ордера те же, что без него."""
    bars = _staircase(122)

    plain = TrendPullbackStrategy(**PARAMS)
    plain_orders = _run(plain, bars, fill=True)
    assert plain.channel_log == ()

    logged = TrendPullbackStrategy(**PARAMS)
    logged.enable_channel_log()
    logged_orders = _run(logged, bars, fill=True)

    assert logged_orders == plain_orders
    assert logged.channel_log


def test_channel_log_points_lie_on_15m_bars_and_form_a_staircase() -> None:
    """Точки — целые 15m-бары; канал поднимается один раз за закрытие 5H.

    Окно 4 закрывается на первом 15m-баре окна 5, поэтому первая точка —
    15m-бар k=99 (минута 1485). Значения не меняются от закрытия до закрытия
    5H, кроме баров, чьи 15 минут упираются в границу окна: закрывающий
    пространство 18000 − 900 15m-бар уже несёт канал нового окна.
    """
    strategy = TrendPullbackStrategy(**PARAMS)
    strategy.enable_channel_log()
    _run(strategy, _staircase(122), fill=True)

    points = strategy.channel_log
    assert points
    assert points[0].ts == START + 60 * 1485
    assert points[-1].ts == START + 60 * 1815
    assert [point.lower for point in points] == [Decimal("102")] * 20 + [Decimal("102.5")] * 3
    for point in points:
        assert point.ts % FIFTEEN_MIN_WIDTH == 0
    for previous, current in zip(points, points[1:], strict=False):
        assert current.ts > previous.ts
        if previous.ts // FIVE_HOURS_WIDTH != current.ts // FIVE_HOURS_WIDTH:
            continue
        if current.ts % FIVE_HOURS_WIDTH == FIVE_HOURS_WIDTH - FIFTEEN_MIN_WIDTH:
            continue
        assert (previous.mid, previous.upper, previous.lower) == (
            current.mid,
            current.upper,
            current.lower,
        )


def test_channel_log_last_point_matches_the_strategy_channel() -> None:
    """Последняя точка — тот канал, по которому стратегия видит границу сейчас."""
    strategy = TrendPullbackStrategy(**PARAMS)
    strategy.enable_channel_log()
    _run(strategy, _staircase(122), fill=True)

    points = strategy.channel_log
    assert points
    if strategy.last_lower is not None:
        assert points[-1].lower == strategy.last_lower
