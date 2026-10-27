import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.core.position import Position
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.strategy.base import StrategyState
from strategylab.strategy.examples import BuyHoldStrategy

COSTS = CostModel()
TAKER_FEE = COSTS.taker_fee
SLIPPAGE = COSTS.slippage

SYMBOL = "BTCUSDT"
START = 1717200000  # 2024-06-01T00:00:00Z, граница часов
HOUR = Interval.HOUR.value
# Окно обязано делиться на интервал: иначе агрегация упадёт IncompleteBarGroup.
MINUTES_PER_WINDOW = 2040  # ровно 34 часа


def _bar(ts: int, close: str) -> Bar:
    price = Decimal(close)
    return Bar(ts=ts, open=price, high=price, low=price, close=price, volume=Decimal("1"))


def _bars(ts_start: int, count: int) -> list[Bar]:
    return [_bar(ts_start + i, str(ts_start + i)) for i in range(count)]


def _market(side: Side, qty: str) -> Order:
    return Order(side, OrderKind.MARKET, Decimal(qty), None, None, False)


class ListFeed:
    """Лента из списка баров: бары интервала стратегии — те же бары."""

    def __init__(self, bars: list[Bar], interval: Interval = Interval.MINUTE) -> None:
        self.interval = interval
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class ScriptedStrategy:
    """Стратегия по сценарию: заданные ордера на заданных барах, состояния запоминает."""

    name = "scripted"

    def __init__(self, script: dict[int, list[Order]]) -> None:
        self._script = script
        self.states: list[StrategyState] = []

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        self.states.append(state)
        return list(self._script.get(state.bar_index, []))


class FillRecordingBroker(BrokerSimulator):
    """Брокер, запоминающий филлы, применённые через apply_bar."""

    def __init__(self, costs: CostModel, intrabar: IntrabarOrder) -> None:
        super().__init__(costs, intrabar)
        self.fills: list[Fill] = []

    def apply_bar(self, bar: Bar) -> list[Fill]:
        fills = super().apply_bar(bar)
        self.fills.extend(fills)
        return fills


# Порядок «сигнал — следующий бар»


def test_order_from_bar_n_fills_on_bar_n_plus_one() -> None:
    """Сигнал на баре N исполняется не раньше бара N+1: проверка сравнением ts."""
    bars = _bars(100, 5)
    feed = ListFeed(bars)
    broker = FillRecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()

    Engine().run(feed, ScriptedStrategy({2: [_market(Side.BUY, "1")]}), broker, metrics)

    assert len(broker.fills) == 1
    entry_fill = broker.fills[0]
    assert entry_fill.ts >= bars[3].ts
    assert entry_fill.price == Decimal("103") * (Decimal(1) + SLIPPAGE)


# StrategyState: знаковая позиция, equity из Metrics, bar_index с нуля


def test_state_carries_signed_position_equity_and_bar_index() -> None:
    bars = _bars(100, 5)
    feed = ListFeed(bars)
    strategy = ScriptedStrategy({0: [_market(Side.BUY, "1")], 2: [_market(Side.SELL, "1")]})
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()

    Engine().run(feed, strategy, broker, metrics)

    entry_price = Decimal("101") * (Decimal(1) + SLIPPAGE)
    exit_price = Decimal("103") * (Decimal(1) - SLIPPAGE)
    # Комиссии обеих сторон: закрывающая от цены выхода, входная — от цены входа.
    realized = (exit_price - entry_price) - TAKER_FEE * exit_price - TAKER_FEE * entry_price

    assert [s.bar_index for s in strategy.states] == [0, 1, 2, 3, 4]
    assert [s.position for s in strategy.states] == [
        Decimal("0"),
        Decimal("1"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
    ]
    assert [s.equity for s in strategy.states] == [
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        realized,
        realized,
    ]
    assert metrics.trades == 1
    assert metrics.net_pnl == realized


def test_state_position_is_negative_for_a_short() -> None:
    bars = _bars(100, 3)
    feed = ListFeed(bars)
    strategy = ScriptedStrategy({0: [_market(Side.SELL, "1")]})
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()

    Engine().run(feed, strategy, broker, metrics)

    assert [s.position for s in strategy.states] == [
        Decimal("0"),
        Decimal("-1"),
        Decimal("-1"),
    ]


# Реализованный pnl пишется в метрики, включая переворот позиции


def test_flip_realizes_only_the_closed_part() -> None:
    """SELL 3 против лонга 1: реализуется только закрытая единица, не весь филл."""
    bars = _bars(100, 4)
    feed = ListFeed(bars)
    strategy = ScriptedStrategy({0: [_market(Side.BUY, "1")], 2: [_market(Side.SELL, "3")]})
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()

    Engine().run(feed, strategy, broker, metrics)

    entry_price = Decimal("101") * (Decimal(1) + SLIPPAGE)
    entry_fee = TAKER_FEE * Decimal("1") * entry_price
    flip_price = Decimal("103") * (Decimal(1) - SLIPPAGE)
    flip_fee = TAKER_FEE * Decimal("3") * flip_price
    # Комиссия переворотного филла делится по объёму: одна единица закрылась,
    # две открыли шорт — доля закрытой части в realized, доля новой позиции
    # станет её входной комиссией.
    realized_flip = (flip_price - entry_price) - flip_fee * Decimal("1") / Decimal("3") - entry_fee

    close_price = Decimal("103") * (Decimal(1) + SLIPPAGE)
    close_fee = TAKER_FEE * Decimal("2") * close_price
    short_entry_fee = flip_fee * Decimal("2") / Decimal("3")
    realized_close = (
        (close_price - flip_price) * Decimal("2") * Decimal(-1) - close_fee - short_entry_fee
    )

    assert not broker.position.is_open
    assert metrics.trades == 2
    assert metrics.net_pnl == realized_flip + realized_close


# Сведение позиции в конце окна


def test_open_position_is_closed_at_the_end_of_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(100, 4)
    feed = ListFeed(bars)
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()
    applied: list[Fill] = []
    original = Position.apply_fill

    def spy(self: Position, fill: Fill) -> None:
        applied.append(fill)
        original(self, fill)

    monkeypatch.setattr(Position, "apply_fill", spy)

    Engine().run(feed, BuyHoldStrategy(), broker, metrics)

    assert not broker.position.is_open
    assert broker.position.qty == Decimal("0")
    close_fill = applied[-1]
    assert close_fill.ts == bars[-1].ts
    assert close_fill.side is Side.SELL
    # Сведение окна исполняется как маркет: цена закрытия с уводом.
    assert close_fill.price == bars[-1].close * (Decimal(1) - SLIPPAGE)
    assert close_fill.qty == Decimal("1")
    assert close_fill.reason == "market"
    assert metrics.trades == 1


def test_signal_on_the_last_bar_does_not_execute() -> None:
    """Сведение на последнем баре происходит, а сигнал стратегии — нет: бара после него нет."""
    bars = _bars(100, 3)
    feed = ListFeed(bars)
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()
    order = _market(Side.BUY, "1")
    strategy = ScriptedStrategy({2: [order]})

    Engine().run(feed, strategy, broker, metrics)

    assert broker.pending_orders == [order]
    assert metrics.trades == 0
    assert metrics.net_pnl == Decimal("0")
    assert not broker.position.is_open


class CountingStore(CandleStore):
    """Хранилище со счётчиком обращений к read_1m, счётчик на месте вызова."""

    def __init__(self, conn: sqlite3.Connection, symbol: str) -> None:
        super().__init__(conn, symbol)
        self.calls: list[tuple[str, int, int]] = []

    def read_1m(self, symbol: str, start_ts: int, end_ts: int) -> Iterator[Bar]:
        self.calls.append((symbol, start_ts, end_ts))
        return super().read_1m(symbol, start_ts, end_ts)


def _store(tmp_path: Path, name: str) -> CountingStore:
    conn = connect(tmp_path / name)
    conn.executescript(SCHEMA)
    return CountingStore(conn, SYMBOL)


def _minutes(start_ts: int, count: int) -> list[Bar]:
    return [
        Bar(
            ts=start_ts + 60 * offset,
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("0.5"),
            close=Decimal("1.5"),
            volume=Decimal("1"),
        )
        for offset in range(count)
    ]
