"""Гейт капитала при подаче ордеров: marked equity и требуемая маржа (дефекты B и E).

Семантика из design-draft 2026-10-29 (фикс 1): перед `broker.submit` движок
считает marked equity = `metrics.equity + signed_qty × (bar.close − entry_price)`
(капитал метрик — только реализованный, нереализованная просадка позиции
добавляется по close подающего бара) и отклоняет `MarginError` любой
не-reduce-only ордер, если marked ≤ 0 либо требуемая маржа
`(|позиция| × close + qty × close) / leverage` строго больше marked —
равенство проходит. Reduce-only проходят всегда: счёт обязан иметь возможность
закрыться. Notional для всех видов ордеров считается по close подающего бара —
для LIMIT это сознательная консервативная аппроксимация.
"""

from collections.abc import Iterator
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator, MarginError
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.strategy.base import StrategyState

COSTS = CostModel()


def _bar(ts: int, close: str) -> Bar:
    price = Decimal(close)
    return Bar(ts=ts, open=price, high=price, low=price, close=price, volume=Decimal("1"))


def _bars(closes: list[str]) -> list[Bar]:
    return [_bar(100 + 60 * index, close) for index, close in enumerate(closes)]


def _market(side: Side, qty: str, *, reduce_only: bool = False) -> Order:
    return Order(side, OrderKind.MARKET, Decimal(qty), None, None, reduce_only)


class ListFeed:
    """Лента из списка баров: бары интервала стратегии — те же бары."""

    def __init__(self, bars: list[Bar]) -> None:
        self.interval = Interval.MINUTE
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class ScriptedStrategy:
    """Стратегия по сценарию: заданные ордера на заданных барах."""

    name = "scripted"

    def __init__(self, script: dict[int, list[Order]]) -> None:
        self._script = script

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        return list(self._script.get(state.bar_index, []))


def _run(
    bars: list[Bar],
    script: dict[int, list[Order]],
    equity: Decimal,
) -> tuple[BrokerSimulator, Metrics]:
    broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=equity)
    Engine().run(ListFeed(bars), ScriptedStrategy(script), broker, metrics)
    return broker, metrics


# Дефект B: oversized-заявка больше не принимается без вопросов


def test_oversized_notional_is_rejected_with_margin_error() -> None:
    """Entry-notional ×11.5 от капитала (реестр B: 115 289.92 при 10 000) — отказ."""
    bars = _bars(["100", "100", "100"])
    # notional = 1150 × 100 = 115 000 — ровно ×11.5 против капитала 10 000.
    script = {0: [_market(Side.BUY, "1150")]}

    with pytest.raises(MarginError):
        _run(bars, script, Decimal("10000"))


# Дефект E: marked equity ≤ 0 — новые ордера отклоняются, reduce-only проходит


def _drowning_setup() -> tuple[list[Bar], Decimal]:
    """Счёт тонет в нереализованной просадке: реализованный капитал ещё положителен.

    Капитал 100; BUY 100 подаётся на баре с close 1 (требуемая маржа 100 —
    равенство проходит), исполняется на следующем баре по close 2 (вход
    2.001 с уводом), затем цена возвращается к 1: marked = 100 + 100 ×
    (1 − 2.001) = −0.1 ≤ 0. Ликвидация при leverage=1 недостижима (требует
    low ≤ 0) — без гейта движок продолжил бы исполнять новые ордера.
    """
    return _bars(["1", "2", "1", "1"]), Decimal("100")


def test_new_order_is_rejected_when_marked_equity_is_not_positive() -> None:
    """marked ≤ 0 и ордер не reduce-only — явный MarginError, не тихий пропуск."""
    bars, equity = _drowning_setup()
    script = {0: [_market(Side.BUY, "100")], 2: [_market(Side.BUY, "1")]}

    with pytest.raises(MarginError):
        _run(bars, script, equity)


def test_reduce_only_passes_and_closes_position_when_marked_equity_is_not_positive() -> None:
    """Тот же тонущий счёт: reduce-only проходит всегда и позиция закрывается.

    Счёт обязан иметь возможность закрыться: выход исполняется на следующем
    баре, прогон завершается без исключения, а реализованный убыток уводит
    equity метрик в минус — честно, а не продолжением торгов.
    """
    bars, equity = _drowning_setup()
    script = {0: [_market(Side.BUY, "100")], 2: [_market(Side.SELL, "100", reduce_only=True)]}

    broker, metrics = _run(bars, script, equity)

    assert not broker.position.is_open
    assert metrics.trades == 1
    assert metrics.equity < 0


# Регрессия и граница: обычные заявки в пределах маржи гейт не трогает


def test_regular_order_within_margin_is_accepted() -> None:
    """Регрессия: обычная заявка в пределах маржи проходит и исполняется как раньше."""
    bars = _bars(["100", "100", "100"])
    script = {0: [_market(Side.BUY, "1")]}

    broker, metrics = _run(bars, script, Decimal("10000"))

    assert not broker.position.is_open  # сведение окна закрыло позицию
    assert metrics.trades == 1


def test_required_margin_exactly_equal_to_marked_equity_passes() -> None:
    """Граница: требуемая маржа ТОЧНО равна marked — отклоняется только строгое превышение."""
    bars = _bars(["100", "100", "100"])
    # требуемая маржа = (0 × 100 + 100 × 100) / 1 = 10 000 = marked equity.
    script = {0: [_market(Side.BUY, "100")]}

    # без исключения: заявка принята, исполнена и сведена в конце окна.
    broker, metrics = _run(bars, script, Decimal("10000"))

    assert metrics.trades == 1
    assert not broker.position.is_open


# Flat-guard: entry_price плоской позиции не попадает в арифметику гейта


def test_flat_position_entry_price_garbage_does_not_affect_the_gate() -> None:
    """Плоская позиция: signed_qty = 0 обнуляет слагаемое, entry_price не участвует.

    Мусор (Infinity) в entry_price плоской позиции дал бы marked = NaN, а NaN
    проходит любое сравнение — гейт ослеп бы. С guard'ом marked равен ровно
    metrics.equity: при нуле заявка отклоняется, при положительном капитале — проходит.
    """
    bars = _bars(["100", "100", "100"])

    def _broker_with_junk() -> BrokerSimulator:
        broker = BrokerSimulator(COSTS, IntrabarOrder.PESSIMISTIC)
        broker.position.entry_price = Decimal("Infinity")
        return broker

    with pytest.raises(MarginError):
        Engine().run(
            ListFeed(bars),
            ScriptedStrategy({0: [_market(Side.BUY, "1")]}),
            _broker_with_junk(),
            Metrics(equity=Decimal("0")),
        )

    metrics = Metrics(equity=Decimal("10000"))
    Engine().run(
        ListFeed(bars),
        ScriptedStrategy({0: [_market(Side.BUY, "1")]}),
        _broker_with_junk(),
        metrics,
    )
    assert metrics.trades == 1
