import dataclasses
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar

BAR_A = (1717200000, "67659.5", "67735.6", "67650.0", "67663.2")
BAR_B = (1717200600, "67670.0", "67710.0", "67640.0", "67690.0")
BAR_C = (1717201200, "67610.0", "67640.0", "67600.0", "67620.0")


def _bar(spec: tuple[int, str, str, str, str]) -> Bar:
    ts, o, h, lo, c = spec
    return Bar(
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=Decimal("1000"),
    )


def _order(side: Side, kind: OrderKind, qty: str, limit: str | None, stop: str | None) -> Order:
    return Order(
        side,
        kind,
        Decimal(qty),
        Decimal(limit) if limit is not None else None,
        Decimal(stop) if stop is not None else None,
        False,
    )


def test_limit_buy_fills_at_limit_price_on_touch() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("67652.71"), None, False))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67652.71")


def test_limit_fill_carries_exact_fee_reason_and_bar_open_time() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "67652.71", None))
    fills = b.apply_bar(_bar(BAR_A))
    assert fills == [
        Fill(
            ts=1717200000,
            side=Side.BUY,
            price=Decimal("67652.71"),
            qty=Decimal("1"),
            fee=costs.maker_fee * Decimal("1") * Decimal("67652.71"),
            reason="limit",
        )
    ]
    assert fills[0].fee == Decimal("13.530542")


def test_limit_sell_fills_at_limit_price_on_touch() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.SELL, OrderKind.LIMIT, "0.5", "67660.00", None))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67660.00")
    assert fills[0].side == Side.SELL
    assert fills[0].fee == costs.maker_fee * Decimal("0.5") * Decimal("67660.00")
    assert fills[0].reason == "limit"
    assert fills[0].ts == BAR_A[0]


def test_limit_order_survives_bar_that_does_not_touch() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "67600", None))
    assert b.apply_bar(_bar(BAR_A)) == []
    assert b.pending_orders == [_order(Side.BUY, OrderKind.LIMIT, "1", "67600", None)]
    fills = b.apply_bar(_bar(BAR_C))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67600")
    assert fills[0].reason == "limit"


def test_limit_at_exactly_low_fills_and_a_hair_below_does_not() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "67650.0", None))
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "67649.99", None))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67650.0")


def test_sell_limit_at_exactly_high_fills_and_a_hair_above_does_not() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.SELL, OrderKind.LIMIT, "1", "67735.6", None))
    b.submit(_order(Side.SELL, OrderKind.LIMIT, "1", "67735.61", None))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67735.6")
    assert fills[0].side == Side.SELL


def test_market_buy_fills_at_close_plus_slippage_with_taker_fee() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.MARKET, "1", None, None))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    expected = Decimal(BAR_A[4]) * (Decimal(1) + costs.slippage)
    assert fills[0].price == expected
    assert fills[0].price - Decimal(BAR_A[4]) == Decimal(BAR_A[4]) * costs.slippage
    assert fills[0].fee == costs.taker_fee * Decimal("1") * expected
    assert fills[0].reason == "market"
    assert fills[0].ts == BAR_A[0]


def test_market_sell_fills_at_close_minus_slippage_with_taker_fee() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.SELL, OrderKind.MARKET, "2", None, None))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    expected = Decimal(BAR_A[4]) * (Decimal(1) - costs.slippage)
    assert fills[0].price == expected
    assert Decimal(BAR_A[4]) - fills[0].price == Decimal(BAR_A[4]) * costs.slippage
    assert fills[0].fee == costs.taker_fee * Decimal("2") * expected
    assert fills[0].reason == "market"


def test_market_order_executes_on_the_next_apply_bar() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    assert b.apply_bar(_bar(BAR_A)) == []
    b.submit(_order(Side.BUY, OrderKind.MARKET, "1", None, None))
    fills = b.apply_bar(_bar(BAR_B))
    assert len(fills) == 1
    assert fills[0].ts == BAR_B[0]
    assert fills[0].price == Decimal(BAR_B[4]) * (Decimal(1) + costs.slippage)


def test_stop_buy_triggers_on_high_and_fills_with_slippage() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.STOP_MARKET, "1", None, "67700"))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    expected = Decimal(BAR_A[4]) * (Decimal(1) + costs.slippage)
    assert fills[0].price == expected
    assert fills[0].fee == costs.taker_fee * Decimal("1") * expected
    assert fills[0].reason == "stop"
    assert fills[0].ts == BAR_A[0]


def test_stop_sell_triggers_on_low_and_fills_with_slippage() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "1", None, "67652"))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    expected = Decimal(BAR_A[4]) * (Decimal(1) - costs.slippage)
    assert fills[0].price == expected
    assert fills[0].fee == costs.taker_fee * Decimal("1") * expected
    assert fills[0].reason == "stop"


def test_stop_that_does_not_trigger_stays_pending() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    resting = _order(Side.BUY, OrderKind.STOP_MARKET, "1", None, "67800")
    b.submit(resting)
    assert b.apply_bar(_bar(BAR_A)) == []
    assert b.pending_orders == [resting]


def test_fills_within_a_bar_are_ordered_stop_then_limit_then_market() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.STOP_MARKET, "1", None, "67700"))
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "67652.71", None))
    b.submit(_order(Side.BUY, OrderKind.MARKET, "1", None, None))
    fills = b.apply_bar(_bar(BAR_A))
    assert [f.reason for f in fills] == ["stop", "limit", "market"]
    assert [f.price for f in fills] == [
        Decimal(BAR_A[4]) * (Decimal(1) + Decimal("0.0005")),
        Decimal("67652.71"),
        Decimal(BAR_A[4]) * (Decimal(1) + Decimal("0.0005")),
    ]


def test_reduce_only_order_raises_while_position_is_flat() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    with pytest.raises(ValueError):
        b.submit(Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("67652.71"), None, True))
    with pytest.raises(ValueError):
        b.submit(Order(Side.SELL, OrderKind.MARKET, Decimal("1"), None, None, True))
    assert b.pending_orders == []


def test_submit_rejects_orders_without_required_price() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    with pytest.raises(ValueError):
        b.submit(_order(Side.BUY, OrderKind.STOP_MARKET, "1", None, None))
    with pytest.raises(ValueError):
        b.submit(_order(Side.SELL, OrderKind.LIMIT, "1", None, None))


def test_cost_model_defaults_are_exact_and_frozen() -> None:
    costs = CostModel()
    assert costs.taker_fee == Decimal("0.0006")
    assert costs.maker_fee == Decimal("0.0002")
    assert costs.slippage == Decimal("0.0005")
    assert costs.leverage == Decimal("1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        costs.slippage = Decimal("0.001")  # type: ignore[misc]


def test_intrabar_order_values() -> None:
    assert IntrabarOrder.PESSIMISTIC == 0
    assert IntrabarOrder.OPTIMISTIC == 1
    assert IntrabarOrder.PESSIMISTIC != IntrabarOrder.OPTIMISTIC
