from __future__ import annotations

from decimal import Decimal

import pytest

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import Strategy, StrategyState
from strategylab.strategy.examples import BuyHoldStrategy, SmaCrossStrategy, UnknownStrategy, build


def _bar(ts: int, close: str) -> Bar:
    c = Decimal(close)
    return Bar(ts, c, c, c, c, Decimal("1"))


def _run(strategy: Strategy, closes: list[str]) -> list[tuple[int, list[Order]]]:
    result: list[tuple[int, list[Order]]] = []
    for i, close in enumerate(closes):
        state = StrategyState(position=Decimal("0"), equity=Decimal("10000"), bar_index=i)
        result.append((i, strategy.on_bar(_bar(1_717_200_000 + 60 * i, close), state)))
    return result


def _order_indices(result: list[tuple[int, list[Order]]]) -> list[int]:
    return [i for i, orders in result if orders]


def test_build_returns_sma_cross_with_params() -> None:
    s = build("sma_cross", {"fast": Decimal("10"), "slow": Decimal("30")})
    assert s.name == "sma_cross"


def test_build_returns_buy_hold() -> None:
    s = build("buy_hold", {})
    assert s.name == "buy_hold"


def test_build_unknown_name_raises() -> None:
    with pytest.raises(UnknownStrategy):
        build("нет_такой", {})


@pytest.mark.parametrize(
    ("fast", "slow"),
    [
        (Decimal("10"), Decimal("0")),
        (Decimal("10"), Decimal("-5")),
        (Decimal("0"), Decimal("30")),
        (Decimal("-1"), Decimal("30")),
        (Decimal("30"), Decimal("10")),
        (Decimal("10"), Decimal("10")),
    ],
)
def test_build_rejects_params_that_cannot_work(fast: Decimal, slow: Decimal) -> None:
    with pytest.raises(ValueError, match="slow|fast"):
        build("sma_cross", {"fast": fast, "slow": slow})


def test_sma_cross_stays_quiet_with_fewer_bars_than_slow() -> None:
    s = build("sma_cross", {"fast": Decimal("2"), "slow": Decimal("3")})
    result = _run(s, ["1", "1"])
    assert [orders for _, orders in result] == [[], []]


def test_sma_cross_buys_once_on_golden_cross() -> None:
    s = build("sma_cross", {"fast": Decimal("2"), "slow": Decimal("3")})
    result = _run(s, ["1", "1", "1", "10", "10"])
    assert _order_indices(result) == [3]
    orders = result[3][1]
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, Order)
    assert o.side == Side.BUY
    assert o.kind == OrderKind.MARKET
    assert o.reduce_only is False


def test_sma_cross_sells_once_on_death_cross() -> None:
    s = build("sma_cross", {"fast": Decimal("2"), "slow": Decimal("3")})
    result = _run(s, ["10", "10", "10", "1", "1"])
    assert _order_indices(result) == [3]
    orders = result[3][1]
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, Order)
    assert o.side == Side.SELL
    assert o.kind == OrderKind.MARKET
    assert o.reduce_only is False


def test_sma_cross_emits_exactly_one_order_per_cross() -> None:
    s = build("sma_cross", {"fast": Decimal("2"), "slow": Decimal("3")})
    result = _run(s, ["1", "1", "1", "10", "10", "1", "1"])
    assert _order_indices(result) == [3, 5]
    assert result[3][1][0].side == Side.BUY
    assert result[5][1][0].side == Side.SELL


def test_buy_hold_buys_once_on_first_bar_only() -> None:
    s = build("buy_hold", {})
    result = _run(s, ["10", "11", "12"])
    assert _order_indices(result) == [0]
    orders = result[0][1]
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, Order)
    assert o.side == Side.BUY
    assert o.kind == OrderKind.MARKET
    assert o.reduce_only is False


def test_example_strategies_satisfy_strategy_protocol() -> None:
    assert isinstance(SmaCrossStrategy(Decimal("2"), Decimal("3")), Strategy)
    assert isinstance(BuyHoldStrategy(), Strategy)
