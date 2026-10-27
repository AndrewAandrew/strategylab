"""Тесты ЗАЧИСТКИ (sweep_liquidity): синтетические 1m-бары, никакой сети.

Бары строятся «часами»: 60 одинаковых минут дают ровно нужный 1H-бар с
заданным OHLC. Плоские часы (±1 от уровня) дают TR = 2 и минимум диапазона
99 при уровне 100; сигнальный час — проколка Low на 2 ниже уровня (98) при
закрытии на уровне (100): смётка фитилём и возврат внутрь.

Короткие длины: lookback = 2 (вместо 20), atr_len = 2 (вместо 14 — суть
Wilder-сглаживания не режется, время прогонов экономится). Числа выходов
получаются целыми: SL = 98 − 1.5·2 = 95, R = 100 − 95 = 5,
qty = 10000·0.01 / 5 = 20, TP1 = 107.5 на половину позиции, TP2 = 115.
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
from strategylab.core.trades import TradeLog
from strategylab.strategy.base import Strategy, StrategyState
from strategylab.strategy.examples import UnknownStrategy, build
from strategylab.strategy.sweep_liquidity import SweepLiquidityStrategy

START = 1_717_200_000  # 2024-06-01T00:00:00Z: граница часа и 1m
ONE = Decimal("1")
HALF = Decimal("0.5")
FLAT_LEVEL = Decimal("100")
EQUITY = Decimal("10000")
COSTS = CostModel()
SLIPPAGE = COSTS.slippage

PARAMS: dict[str, Decimal] = {
    "lookback": Decimal("2"),
    "atr_len": Decimal("2"),
    "atr_mult": Decimal("1.5"),
    "tp1_r": Decimal("1.5"),
    "tp2_r": Decimal("3.0"),
    "risk_frac": Decimal("0.01"),
}

# Замороженные уровни сценария: следствия плоских часов и проколки на 2.
STOP_LEVEL = FLAT_LEVEL - Decimal("5")  # 98 - 1.5 * ATR(2) = 98 - 3 = 95
RISK = FLAT_LEVEL - STOP_LEVEL  # ровно 5
QUANTITY = EQUITY * PARAMS["risk_frac"] / RISK  # ровно 20
TP1 = FLAT_LEVEL + PARAMS["tp1_r"] * RISK  # 107.5
TP2 = FLAT_LEVEL + PARAMS["tp2_r"] * RISK  # 115
ENTRY_PRICE = FLAT_LEVEL * (Decimal(1) + SLIPPAGE)  # фактический вход MARKET
BE_LEVEL = FLAT_LEVEL  # безубыток: оценка входа брекета
BE_PRICE = BE_LEVEL * (Decimal(1) - SLIPPAGE)  # фактический стоп в безубыток

# Индексы минут: сигнал часа 2 закрывается на минуте 179, вход — минута 180.
SIGNAL_MINUTE = 179
ENTRY_MINUTE = 180


def _minute(index: int, o: Decimal, h: Decimal, lo: Decimal, c: Decimal) -> Bar:
    return Bar(ts=START + 60 * index, open=o, high=h, low=lo, close=c, volume=ONE)


def _hour(k: int, o: Decimal, h: Decimal, lo: Decimal, c: Decimal) -> list[Bar]:
    """Час k: 60 одинаковых минут агрегируются в 1H-бар ровно с этими OHLC."""
    return [_minute(60 * k + i, o, h, lo, c) for i in range(60)]


def _flat_hour(k: int) -> list[Bar]:
    """Плоский час: 1H {100, 101, 99, 100}; TR последующих часов = 2, минимум 99."""
    return _hour(k, FLAT_LEVEL, FLAT_LEVEL + ONE, FLAT_LEVEL - ONE, FLAT_LEVEL)


def _sweep_hour(k: int) -> list[Bar]:
    """Сигнальный час: 1H {100, 100, 98, 100}: проколка минимума, закрытие на уровне."""
    return _hour(k, FLAT_LEVEL, FLAT_LEVEL, FLAT_LEVEL - Decimal("2"), FLAT_LEVEL)


def _entry_bars() -> list[Bar]:
    """Часы 0-1 плоские, час 2 — смётка, час 3 плоский: вход исполняется и живёт."""
    return _flat_hour(0) + _flat_hour(1) + _sweep_hour(2) + _flat_hour(3)


def _craft(
    bars: list[Bar], index: int, o: Decimal, h: Decimal, lo: Decimal, c: Decimal
) -> list[Bar]:
    """Подмена одной минуты: так изолируется одно условие выхода внутри часа."""
    bars[index] = _minute(index, o, h, lo, c)
    return bars


def _run(
    strategy: SweepLiquidityStrategy,
    bars: list[Bar],
    *,
    fill: bool = False,
    flat_from: int | None = None,
    flat_to: int | None = None,
    equity: Decimal = EQUITY,
) -> list[tuple[int, list[Order]]]:
    """Прогон стратегии по барам (эмуляция движка).

    `fill=True` открывает позицию на следующем баре после MARKET-входа,
    `flat_from`/`flat_to` обнуляют позицию на диапазоне минут (эмуляция
    стопа); без `flat_to` — до конца ленты.
    """
    out: list[tuple[int, list[Order]]] = []
    current = Decimal("0")
    pending: Decimal | None = None
    end = flat_to if flat_to is not None else len(bars)
    for index, bar in enumerate(bars):
        if pending is not None:
            current = pending
            pending = None
        if flat_from is not None and flat_from <= index < end:
            current = Decimal("0")
        state = StrategyState(position=current, equity=equity, bar_index=index)
        orders = strategy.on_bar(bar, state)
        if not orders:
            continue
        out.append((index, orders))
        if fill and orders[0].kind is OrderKind.MARKET:
            pending = current + orders[0].qty
    return out


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


def _engine_run(
    bars: list[Bar],
    broker: _RecordingBroker,
    metrics: Metrics,
    trade_log: TradeLog | None = None,
) -> None:
    Engine().run(_ListFeed(bars), SweepLiquidityStrategy(**PARAMS), broker, metrics, trade_log)


# Реестр и параметры


def test_registry_builds_sweep_liquidity() -> None:
    strategy = build("sweep_liquidity", {})
    assert strategy.name == "sweep_liquidity"
    assert isinstance(strategy, SweepLiquidityStrategy)


def test_registry_passes_params() -> None:
    strategy = build("sweep_liquidity", {"lookback": Decimal("30")})
    assert isinstance(strategy, Strategy)


def test_registry_rejects_unknown_strategy() -> None:
    with pytest.raises(UnknownStrategy):
        build("sweep_typo", {})


def test_registry_rejects_unknown_param() -> None:
    with pytest.raises(ValueError, match="неизвестные параметры"):
        build("sweep_liquidity", {"atr_lenght": Decimal("14")})


def test_strategy_satisfies_protocol() -> None:
    assert isinstance(SweepLiquidityStrategy(), Strategy)


def test_defaults_match_the_spec() -> None:
    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(SweepLiquidityStrategy.__init__).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    assert defaults == {
        "lookback": Decimal("20"),
        "atr_len": Decimal("14"),
        "atr_mult": Decimal("1.5"),
        "tp1_r": Decimal("1.5"),
        "tp2_r": Decimal("3.0"),
        "risk_frac": Decimal("0.01"),
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("lookback", Decimal("0")),
        ("lookback", Decimal("-1")),
        ("lookback", Decimal("2.5")),
        ("atr_len", Decimal("0")),
        ("atr_len", Decimal("3.5")),
        ("atr_mult", Decimal("0")),
        ("atr_mult", Decimal("-1")),
        ("tp1_r", Decimal("0")),
        ("tp2_r", Decimal("0")),
        ("risk_frac", Decimal("0")),
        ("risk_frac", Decimal("1.5")),
    ],
)
def test_invalid_params_are_rejected(name: str, value: Decimal) -> None:
    with pytest.raises(ValueError, match=name):
        build("sweep_liquidity", {name: value})


def test_tp2_not_above_tp1_is_rejected() -> None:
    with pytest.raises(ValueError, match="tp2_r"):
        build("sweep_liquidity", {"tp2_r": Decimal("1.4")})


# Вход: смётка и возврат внутрь


def test_no_entry_without_a_dip() -> None:
    """Плоская история: минимумы не пробиты, сигналов нет совсем."""
    strategy = SweepLiquidityStrategy(**PARAMS)

    result = _run(strategy, _flat_hour(0) + _flat_hour(1) + _flat_hour(2) + _flat_hour(3))

    assert result == []


def test_no_entry_without_a_close_back_inside() -> None:
    """Проколка есть (96 < 99), но закрытие (97) под минимумом — не смётка."""
    bars = (
        _flat_hour(0)
        + _flat_hour(1)
        + _hour(2, FLAT_LEVEL, FLAT_LEVEL, FLAT_LEVEL - Decimal("4"), Decimal("97"))
        + _flat_hour(3)
    )
    strategy = SweepLiquidityStrategy(**PARAMS)

    result = _run(strategy, bars)

    assert result == []
    assert strategy.last_rolling_low == Decimal("96")


def test_no_entry_while_lookback_is_not_filled() -> None:
    """Смётка в часе 1 — квалифицировала бы всё, кроме неполного окна границ."""
    bars = (
        _flat_hour(0)
        + _hour(1, FLAT_LEVEL, Decimal("105"), FLAT_LEVEL - Decimal("4"), Decimal("105"))
        + _flat_hour(2)
        + _flat_hour(3)
    )
    strategy = SweepLiquidityStrategy(**PARAMS)

    result = _run(strategy, bars)

    assert result == []


def test_no_entry_while_atr_is_not_ready() -> None:
    """ATR длиной 5 не готов к часу 2, где пришла смётка."""
    strategy = SweepLiquidityStrategy(**{**PARAMS, "atr_len": Decimal("5")})

    result = _run(strategy, _entry_bars())

    assert result == []


def test_entry_is_skipped_without_capital() -> None:
    """Нулевой капитал: размер вышел бы нулевым, сделки нет."""
    strategy = SweepLiquidityStrategy(**PARAMS)

    result = _run(strategy, _entry_bars(), equity=Decimal("0"))

    assert result == []


def test_entry_is_a_market_buy_with_size_by_risk_and_a_half_target() -> None:
    """Вход по закрытию следующего бара; брекет: стоп весь объём, цель половина."""
    strategy = SweepLiquidityStrategy(**PARAMS)

    result = _run(strategy, _entry_bars(), fill=True)

    assert [(index, len(orders)) for index, orders in result] == [
        (SIGNAL_MINUTE, 1),
        (ENTRY_MINUTE, 2),
    ]
    order = result[0][1][0]
    assert order.side is Side.BUY
    assert order.kind is OrderKind.MARKET
    assert order.reduce_only is False
    assert order.limit_price is None
    assert order.stop_price is None
    assert order.qty == QUANTITY
    assert strategy.last_rolling_low == Decimal("98")
    assert strategy.last_atr == Decimal("2")
    stop, target = result[1][1]
    assert stop.side is Side.SELL
    assert stop.kind is OrderKind.STOP_MARKET
    assert stop.stop_price == STOP_LEVEL
    assert stop.limit_price is None
    assert stop.reduce_only is True
    assert target.side is Side.SELL
    assert target.kind is OrderKind.LIMIT
    assert target.limit_price == TP1
    assert target.stop_price is None
    assert target.reduce_only is True
    assert target.qty == stop.qty * HALF


def test_no_second_entry_while_the_position_is_open() -> None:
    """Позиция открыта: дальнейшие смётки (часы 3-5) входов не дают."""
    bars = (
        _flat_hour(0)
        + _flat_hour(1)
        + _sweep_hour(2)
        + _sweep_hour(3)
        + _sweep_hour(4)
        + _sweep_hour(5)
    )
    strategy = SweepLiquidityStrategy(**PARAMS)

    result = _run(strategy, bars, fill=True)

    assert [(index, len(orders)) for index, orders in result] == [
        (SIGNAL_MINUTE, 1),
        (ENTRY_MINUTE, 2),
    ]


# Движок: исполнение входа и выходов, частичный TP и перенос стопа


def test_engine_stops_out_at_full_loss() -> None:
    """Бар с проколкой 94: весь объём закрывается по стопу 95 с проскальзыванием."""
    bars = _craft(_entry_bars(), 184, FLAT_LEVEL, FLAT_LEVEL, Decimal("94"), FLAT_LEVEL)
    broker = _RecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=EQUITY)

    _engine_run(bars, broker, metrics)

    entry, stop = broker.fills
    assert [fill.reason for fill in broker.fills] == ["market", "stop"]
    assert entry.price == ENTRY_PRICE
    assert entry.qty == QUANTITY
    assert stop.side is Side.SELL
    assert stop.price == STOP_LEVEL * (Decimal(1) - SLIPPAGE)
    assert stop.qty == QUANTITY
    assert not broker.position.is_open
    assert metrics.trades == 1
    assert metrics.losses == 1
    assert metrics.wins == 0
    assert metrics.net_pnl == (stop.price - entry.price) * QUANTITY - stop.fee - entry.fee


def test_engine_tp1_closes_half_and_moves_stop_to_breakeven() -> None:
    """TP1 закрывает половину; остаток уходит по безубытку — это и есть новый стоп."""
    bars = _craft(_entry_bars(), 181, FLAT_LEVEL, FLAT_LEVEL + Decimal("8"), FLAT_LEVEL, FLAT_LEVEL)
    bars = _craft(bars, 182, FLAT_LEVEL, FLAT_LEVEL, Decimal("99.9"), FLAT_LEVEL)
    broker = _RecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=EQUITY)
    log = TradeLog()

    _engine_run(bars, broker, metrics, log)

    entry, partial, breakeven = broker.fills
    assert [fill.reason for fill in broker.fills] == ["market", "take_profit", "stop"]
    assert [fill.qty for fill in broker.fills] == [QUANTITY, QUANTITY * HALF, QUANTITY * HALF]
    assert entry.price == ENTRY_PRICE
    assert partial.price == TP1
    assert partial.fee == COSTS.maker_fee * QUANTITY * HALF * TP1
    assert breakeven.price == BE_PRICE
    assert not broker.position.is_open
    assert metrics.trades == 2
    assert metrics.wins == 1
    assert metrics.losses == 1
    assert metrics.net_pnl == (TP1 - ENTRY_PRICE) * QUANTITY * HALF - partial.fee - (
        entry.fee * HALF
    ) + (BE_PRICE - ENTRY_PRICE) * QUANTITY * HALF - breakeven.fee - entry.fee * HALF
    trades = log.trades
    assert len(trades) == 2
    assert {t.entry_ts for t in trades} == {START + 60 * ENTRY_MINUTE}
    assert [t.exit_reason for t in trades] == ["take_profit", "stop"]
    assert sum((t.pnl for t in trades), Decimal("0")) == metrics.net_pnl


def test_engine_tp2_fills_the_rest_at_the_resubmitted_target() -> None:
    """После TP1 цель TP2 активна именно с перезаложенного брекета: филл по 115."""
    bars = _craft(_entry_bars(), 181, FLAT_LEVEL, FLAT_LEVEL + Decimal("8"), FLAT_LEVEL, FLAT_LEVEL)
    bars = _craft(bars, 182, Decimal("108"), Decimal("116"), Decimal("108"), Decimal("115"))
    broker = _RecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=EQUITY)
    log = TradeLog()

    _engine_run(bars, broker, metrics, log)

    entry, partial, final = broker.fills
    assert [fill.reason for fill in broker.fills] == ["market", "take_profit", "take_profit"]
    assert [fill.qty for fill in broker.fills] == [QUANTITY, QUANTITY * HALF, QUANTITY * HALF]
    assert partial.price == TP1
    assert final.price == TP2
    assert not broker.position.is_open
    assert metrics.trades == 2
    assert metrics.wins == 2
    assert metrics.net_pnl == (TP1 - ENTRY_PRICE) * QUANTITY * HALF - partial.fee - (
        entry.fee * HALF
    ) + (TP2 - ENTRY_PRICE) * QUANTITY * HALF - final.fee - entry.fee * HALF
    trades = log.trades
    assert len(trades) == 2
    assert {t.entry_ts for t in trades} == {START + 60 * ENTRY_MINUTE}
    assert [t.exit_ts for t in trades] == [START + 60 * 181, START + 60 * 182]
    assert [t.exit_reason for t in trades] == ["take_profit", "take_profit"]
    assert sum((t.pnl for t in trades), Decimal("0")) == metrics.net_pnl


def test_engine_sketch_case_tp2_on_the_tp1_bar_does_not_pay_twice() -> None:
    """«High ≥ TP2 на том же баре, где TP1»: платится только TP1 половиной.

    Уровень цели движка на этом баре — TP1; остаток живёт дальше (здесь
    останавливается безубытком) и по TP2 в том же баре второй цели нет.
    """
    bars = _craft(
        _entry_bars(), 181, FLAT_LEVEL, STOP_LEVEL + Decimal("21"), FLAT_LEVEL, FLAT_LEVEL
    )
    broker = _RecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=EQUITY)
    log = TradeLog()

    _engine_run(bars, broker, metrics, log)

    entry, partial, breakeven = broker.fills
    assert [fill.reason for fill in broker.fills] == ["market", "take_profit", "stop"]
    assert [fill.qty for fill in broker.fills] == [QUANTITY, QUANTITY * HALF, QUANTITY * HALF]
    assert partial.price == TP1
    assert breakeven.price == BE_PRICE
    assert not broker.position.is_open
    assert metrics.trades == 2
    assert metrics.wins == 1
    assert metrics.losses == 1
    assert sum((t.pnl for t in log.trades), Decimal("0")) == metrics.net_pnl


def test_engine_second_entry_after_a_full_exit() -> None:
    """Стоп закрыл сделку: состояние сброшено, новая смётка входит заново."""
    bars = _craft(
        _entry_bars() + _flat_hour(4),
        184,
        FLAT_LEVEL,
        FLAT_LEVEL,
        Decimal("94"),
        FLAT_LEVEL,
    )
    strategy = SweepLiquidityStrategy(**PARAMS)

    result = _run(strategy, bars, fill=True, flat_from=185, flat_to=239)

    assert [(index, len(orders)) for index, orders in result] == [
        (SIGNAL_MINUTE, 1),
        (ENTRY_MINUTE, 2),
        (239, 1),
        (240, 2),
    ]
    atr_second = Decimal("4.5")  # (2 + TR часа 3) / 2, TR часа 3 с проколкой 94 равен 7
    stop_second = Decimal("94") - PARAMS["atr_mult"] * atr_second
    risk_second = FLAT_LEVEL - stop_second
    quantity_second = EQUITY * PARAMS["risk_frac"] / risk_second
    order = result[2][1][0]
    assert order.side is Side.BUY
    assert order.kind is OrderKind.MARKET
    assert order.qty == quantity_second
    stop, target = result[3][1]
    assert stop.stop_price == stop_second
    assert target.limit_price == FLAT_LEVEL + PARAMS["tp1_r"] * risk_second
    assert target.qty == quantity_second * HALF


# Журнал диапазона: точки по закрытиям 1H-баров


def _hour_start(k: int) -> int:
    """Начало 1H-бара часа k: якорь по эпохе, как у агрегатора."""
    return START + 3600 * k


def test_channel_log_is_empty_until_enabled() -> None:
    """Без включения журнал пуст даже после баров: запись — по запросу."""
    strategy = SweepLiquidityStrategy(**PARAMS)

    _run(strategy, _entry_bars())

    assert strategy.channel_log == ()


def test_channel_log_is_empty_until_the_window_is_full() -> None:
    """Окно границ собрано не целиком — точки ещё нет: диапазона не было."""
    strategy = SweepLiquidityStrategy(**PARAMS)
    strategy.enable_channel_log()

    _run(strategy, _flat_hour(0) + _flat_hour(1))

    assert strategy.channel_log == ()
    assert strategy.last_rolling_low is None
    assert strategy.last_rolling_high is None


def test_channel_log_points_match_the_signal_window() -> None:
    """Точки несут окно перед каждым баром: lower — граница сигнала, upper — зеркало.

    Часы 0-1 плоские: окно перед часом 2 — {99, 99}/{101, 101}; окно перед
    часом 3 — часы 1-2: минимум 98 (плоский 99, смётка 98), максимум 101.
    """
    strategy = SweepLiquidityStrategy(**PARAMS)
    strategy.enable_channel_log()

    _run(strategy, _entry_bars())

    points = strategy.channel_log
    assert [(point.ts, point.lower, point.upper, point.mid) for point in points] == [
        (_hour_start(2), Decimal("99"), Decimal("101"), Decimal("100")),
        (_hour_start(3), Decimal("98"), Decimal("101"), Decimal("99.5")),
    ]
    assert points[-1].lower == strategy.last_rolling_low
    assert points[-1].upper == strategy.last_rolling_high


def test_channel_log_enabled_includes_the_signal_bar_window() -> None:
    """Граница в точке сигнального бара — та же, по которой сигнал проверялся.

    Сигнал часа 2 бьётся против окна часов 0-1 (lower = 99): точка часа 2
    несёт именно 99, а не 98 — окно сдвигается только на закрытии часа 3.
    """
    strategy = SweepLiquidityStrategy(**PARAMS)
    strategy.enable_channel_log()

    _run(strategy, _entry_bars(), fill=True)

    signal_point = next(
        point for point in strategy.channel_log if point.ts == _hour_start(2)
    )
    assert signal_point.lower == Decimal("99")


def test_channel_log_does_not_change_orders() -> None:
    """Включённая запись не влияет на решения: ордерные точки те же."""
    bars = _entry_bars()
    without_log = SweepLiquidityStrategy(**PARAMS)
    with_log = SweepLiquidityStrategy(**PARAMS)
    with_log.enable_channel_log()

    assert _run(with_log, bars, fill=True) == _run(without_log, bars, fill=True)
