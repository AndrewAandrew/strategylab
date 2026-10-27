"""Тесты журнала закрытых сделок: запись выключена по умолчанию и метрики не меняет.

Сценарий — ScriptedStrategy: вход MARKET на баре 0, выход MARKET на баре 2.
Числа повторяют тест состояния движка: вход исполняется на баре 1 (там и
время входа), выход — на баре 3.
"""

import sqlite3
from collections.abc import Iterator
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.fork import run_fork
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.core.trades import TradeLog, TradeLogError
from strategylab.data.schema import SCHEMA
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, assign_zone
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.base import StrategyState

COSTS = CostModel()
SLIPPAGE = COSTS.slippage

SYMBOL = "BTCUSDT"
START = 1717200000  # 2024-06-01T00:00:00Z, граница часа


def _bar(ts: int) -> Bar:
    price = Decimal(str(ts))
    return Bar(ts=ts, open=price, high=price, low=price, close=price, volume=Decimal("1"))


def _bars(count: int) -> list[Bar]:
    return [_bar(START + 60 * i) for i in range(count)]


def _market(side: Side, qty: str) -> Order:
    return Order(side, OrderKind.MARKET, Decimal(qty), None, None, False)


class ListFeed:
    """Лента из списка баров: бары интервала стратегии — те же бары."""

    def __init__(self, bars: list[Bar]) -> None:
        self.interval = Interval.MINUTE
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class ScriptedStrategy:
    """Стратегия по сценарию: заданные ордера на заданных барах.

    Сценарий без состояния: каждой ветке вилки можно дать свою свежую
    стратегию, и её поведение будет тем же.
    """

    name = "scripted"

    def __init__(self, script: dict[int, list[Order]]) -> None:
        self._script = script

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        return list(self._script.get(state.bar_index, []))


def _mark_out() -> dict[int, list[Order]]:
    """Вход на баре 0, выход на баре 2."""
    return {0: [_market(Side.BUY, "1")], 2: [_market(Side.SELL, "1")]}


def _run(script: dict[int, list[Order]], trade_log: TradeLog | None) -> Metrics:
    """Прогон одного сценария: вход на баре 0, выход на баре 2."""
    metrics = Metrics()
    Engine().run(
        ListFeed(_bars(5)),
        ScriptedStrategy(script),
        BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC),
        metrics,
        trade_log,
    )
    return metrics


# Запись выключена по умолчанию: журнал — наблюдатель, динамика — та же


def test_recording_does_not_change_metrics() -> None:
    """Прогон без журнала и прогон с журналом дают одинаковые метрики."""
    plain = _run(_mark_out(), None)
    logged = _run(_mark_out(), TradeLog())

    assert plain.net_pnl == logged.net_pnl
    assert plain.trades == logged.trades
    assert plain.wins == logged.wins
    assert plain.losses == logged.losses
    assert plain.equity == logged.equity


# Журнал включен: он знает время входа, которого движок не знал


def test_log_records_entry_exit_and_pnl_of_one_trade() -> None:
    """Закрытая сделка: время и цена входа, время и цена выхода, pnl движка."""
    log = TradeLog()
    metrics = _run(_mark_out(), log)

    trades = log.trades
    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_ts == START + 60
    assert trade.entry_price == Decimal(str(START + 60)) * (Decimal(1) + SLIPPAGE)
    assert trade.exit_ts == START + 180
    assert trade.exit_price == Decimal(str(START + 180)) * (Decimal(1) - SLIPPAGE)
    assert trade.pnl == metrics.net_pnl
    assert trade.exit_reason == "market"


def test_log_covers_the_window_close_with_market_reason() -> None:
    """Сведение открытой позиции в конце окна тоже попадает в журнал."""
    log = TradeLog()
    metrics = _run({0: [_market(Side.BUY, "1")]}, log)

    trades = log.trades
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "market"
    assert trade.exit_ts == START + 240
    assert trade.pnl == metrics.net_pnl


def test_log_rejects_a_close_without_a_recorded_entry() -> None:
    """Закрытие без `open` — ошибка журнала: время входа придумывать нельзя."""
    log = TradeLog()
    with pytest.raises(TradeLogError):
        log.close(
            Fill(
                ts=START,
                side=Side.SELL,
                price=Decimal("1"),
                qty=Decimal("1"),
                fee=Decimal("0"),
                reason="market",
            ),
            Decimal("1"),
            Decimal("0"),
            Decimal("0"),
        )


# Вилка: отчёт отдаёт сделки веток, метрики при записи не сдвигаются


def _number_or_text(raw: bytes) -> Decimal | str:
    """Текст колонки в Decimal, если это число: иначе цена умножалась бы как строка."""
    text = raw.decode()
    try:
        return Decimal(text)
    except Exception:
        return text


def _store() -> CandleStore:
    conn = sqlite3.connect(":memory:")
    conn.text_factory = _number_or_text
    conn.executescript(SCHEMA)
    assign_zone(conn, SYMBOL, START, START + 300, Zone.FIT)
    store = CandleStore(conn, SYMBOL)
    store.write(_bars(5))
    return store


def _spec() -> StrategySpec:
    return StrategySpec(
        name="scripted",
        symbol=SYMBOL,
        interval=Interval.MINUTE,
        start_ts=START,
        end_ts=START + 300,
        params={},
        rules_ref="test",
    )


def test_fork_reports_no_trades_without_recording() -> None:
    """Без записи отчёт пуст по сделкам — и по-прежнему полный по метрикам."""
    fork = run_fork(_store(), _spec(), lambda: ScriptedStrategy(_mark_out()), COSTS)

    assert fork.pessimistic.trades == ()
    assert fork.optimistic.trades == ()
    assert fork.pessimistic.metrics.trades == 1


def test_fork_with_recording_reports_trades_per_branch() -> None:
    """С записью отчёт каждой ветки несёт её сделки, метрики же без сдвига."""
    spec = _spec()
    fork = run_fork(_store(), spec, lambda: ScriptedStrategy(_mark_out()), COSTS)
    recorded = run_fork(
        _store(), spec, lambda: ScriptedStrategy(_mark_out()), COSTS, record_trades=True
    )

    for plain, with_trades in (
        (fork.pessimistic, recorded.pessimistic),
        (fork.optimistic, recorded.optimistic),
    ):
        assert with_trades.spec_version == plain.spec_version
        assert with_trades.metrics.net_pnl == plain.metrics.net_pnl
        assert len(with_trades.trades) == 1
        assert with_trades.trades[0].pnl == with_trades.metrics.net_pnl


# Bucket стороны: сделка принадлежит стороне позиции, а не закрывающего филла


def test_short_trade_lands_in_short_bucket() -> None:
    metrics = _run({0: [_market(Side.SELL, "1")], 2: [_market(Side.BUY, "1")]}, None)
    assert metrics.short.trades == 1
    assert metrics.long.trades == 0
    assert metrics.short.net_pnl == metrics.net_pnl
    assert metrics.long.net_pnl + metrics.short.net_pnl == metrics.net_pnl


def test_window_close_is_bucketed_by_position_side() -> None:
    # Стратегия открывает шорт и больше ничего: движок сводит окно на последнем баре.
    metrics = _run({0: [_market(Side.SELL, "1")]}, None)
    assert metrics.short.trades == 1 and metrics.long.trades == 0
    assert metrics.short.net_pnl == metrics.net_pnl
