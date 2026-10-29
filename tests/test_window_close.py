"""Тесты фикса C-3: очередь заявок не переживает конец окна (fix/window-close-cancel-all).

Семантика (design-draft «Фикс 2»): `_close_window` СНАЧАЛА снимает все pending-заявки
(`broker.cancel_all()`), ПОТОМ сводит позицию по bar.close — существующая логика без
изменений. Снятие неисполненных заявок не влияет на fills/equity: метрики прогона с
очередью на конце окна и без неё совпадают. Счётчик снятых заявок `broker.window_cancelled`
наблюдаем после run() без чтения очереди: учётное тождество
`submits − fills − expired − window_cancelled == 0`, pending на конце окна всегда 0.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.strategy.base import StrategyState

COSTS = CostModel()
START = 1717200000


def _bar(ts: int, low: str) -> Bar:
    """Плоский бар по 100, кроме low: касание лимита 99 задаётся только низким."""
    price = Decimal("100")
    return Bar(ts=ts, open=price, high=price, low=Decimal(low), close=price, volume=Decimal("1"))


def _miss_bars(count: int) -> list[Bar]:
    """Бары без касания: low=100 > лимита 99 — BUY-лимитка не исполняется ни на одном."""
    return [_bar(START + index, "100") for index in range(count)]


def _limit_buy(price: str, ttl: int | None = None) -> Order:
    return Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal(price), None, False, ttl)


def _market_buy() -> Order:
    return Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False)


class ListFeed:
    """Лента из списка баров (приём tests/test_engine.py)."""

    def __init__(self, bars: list[Bar]) -> None:
        self.interval = Interval.MINUTE
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class ScriptedStrategy:
    """Стратегия по сценарию: заданные ордера на заданных барах (приём test_engine.py)."""

    name = "scripted"

    def __init__(self, script: dict[int, list[Order]]) -> None:
        self._script = script

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        return list(self._script.get(state.bar_index, []))


class FillRecordingBroker(BrokerSimulator):
    """Брокер, запоминающий филлы, применённые через apply_bar (приём test_engine.py)."""

    def __init__(self, costs: CostModel, intrabar: IntrabarOrder) -> None:
        super().__init__(costs, intrabar)
        self.fills: list[Fill] = []

    def apply_bar(self, bar: Bar) -> list[Fill]:
        fills = super().apply_bar(bar)
        self.fills.extend(fills)
        return fills


# Брокер: cancel_all снимает всё и ведёт счётчик


def test_cancel_all_returns_orders_clears_queue_and_counts() -> None:
    b = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    first = _limit_buy("99")
    second = Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("95"), False)
    b.submit(first)
    b.submit(second)

    assert b.cancel_all() == [first, second]
    assert b.pending_orders == []
    assert b.window_cancelled == 2

    # Повторно на пустой очереди: снимать нечего, счётчик не двигается.
    assert b.cancel_all() == []
    assert b.window_cancelled == 2


def test_cancel_all_counter_starts_at_zero() -> None:
    b = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    assert b.window_cancelled == 0


# (a) Pending-лимитка не переживает окно


def test_pending_limit_does_not_survive_window() -> None:
    bars = _miss_bars(4)
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()

    Engine().run(ListFeed(bars), ScriptedStrategy({0: [_limit_buy("99")]}), broker, metrics)

    assert broker.pending_orders == []
    assert broker.window_cancelled == 1
    assert metrics.trades == 0
    assert not broker.position.is_open


# (b) Метрики идентичны: с pending-заявкой на конце окна и без неё


def test_metrics_identical_with_and_without_trailing_pending() -> None:
    """Снятие НЕИСПОЛНЕННЫХ заявок не влияет на fills/equity — числа не меняются."""
    bars = _miss_bars(4)

    plain_broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    plain = Metrics(equity=Decimal("10000"))
    Engine().run(ListFeed(bars), ScriptedStrategy({0: [_market_buy()]}), plain_broker, plain)

    queued_broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    queued = Metrics(equity=Decimal("10000"))
    Engine().run(
        ListFeed(bars),
        ScriptedStrategy({0: [_market_buy(), _limit_buy("1")]}),
        queued_broker,
        queued,
    )

    assert plain_broker.window_cancelled == 0
    assert queued_broker.window_cancelled == 1
    assert plain.trades == 1  # sanity: вход состоялся, позиция сведена на конце окна
    for name in ("trades", "wins", "losses", "net_pnl", "equity", "max_drawdown"):
        assert getattr(queued, name) == getattr(plain, name), name
    assert queued.profit_factor() == plain.profit_factor()
    assert queued.win_rate() == plain.win_rate()
    assert queued.sharpe() == plain.sharpe()


# (c) Учётное тождество: submits − fills − expired − window_cancelled == 0


def test_accounting_identity_submits_fills_expired_window_cancelled() -> None:
    """Все члены тождества известны независимо: submits — сценарий, fills — брокер,
    expired — конструкция сценария (TIF-списание), window_cancelled — счётчик брокера.

    Сценарий: L1 (@99, без TIF) исполняется на последнем баре касанием low=98;
    L2 (@99, ttl=1) списывается на входе третьего apply_bar, не исполнившись;
    L3 (@1) подаётся на последнем баре и снимается на конце окна.
    """
    bars = [*_miss_bars(3), _bar(START + 3, "98")]
    broker = FillRecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()
    script = {0: [_limit_buy("99"), _limit_buy("99", ttl=1)], 3: [_limit_buy("1")]}

    Engine().run(ListFeed(bars), ScriptedStrategy(script), broker, metrics)

    submits = 3
    expired = 1  # L2 списана TIF до последнего бара: в филлах и в снятых её нет
    assert len(broker.fills) == 1
    assert broker.fills[0].ts == bars[3].ts  # филл один и он на последнем баре — это L1
    assert broker.fills[0].price == Decimal("99")
    assert broker.window_cancelled == 1  # снята ровно L3: не будь L2 списана — было бы 2
    assert broker.pending_orders == []  # pending-заглушка тождества ушла: всегда 0
    assert submits - len(broker.fills) - expired - broker.window_cancelled == 0


# (d) Мутационный контраст: без cancel_all старое поведение воспроизводится


def test_mutation_without_cancel_all_pending_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Гейт отключён (cancel_all — no-op) → поведение до фикса C-3: очередь переживает окно."""

    monkeypatch.setattr(BrokerSimulator, "cancel_all", lambda self: [])
    bars = _miss_bars(4)
    order = _limit_buy("99")
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics()

    Engine().run(ListFeed(bars), ScriptedStrategy({0: [order]}), broker, metrics)

    assert broker.pending_orders == [order]
    assert broker.window_cancelled == 0
    assert metrics.trades == 0
