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
BAR_GAP_UP = (1717200000, "67750", "67800", "67700", "67780")
BAR_GAP_DOWN = (1717200600, "67600", "67640", "67550", "67610")
BAR_LOW_99 = (1717200000, "101", "102", "99", "100.5")
BAR_LOW_199 = (1717200600, "201", "202", "199", "200.5")
BAR_BOTH = (1717201200, "150", "250", "90", "200")


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


def test_stop_buy_fills_at_stop_price_reached_from_below() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.STOP_MARKET, "1", None, "67700"))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    expected = Decimal("67700") * (Decimal(1) + costs.slippage)
    assert fills[0].price == expected
    assert fills[0].fee == costs.taker_fee * Decimal("1") * expected
    assert fills[0].reason == "stop"
    assert fills[0].ts == BAR_A[0]


def test_stop_sell_fills_at_stop_price_reached_from_above() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "1", None, "67652"))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    expected = Decimal("67652") * (Decimal(1) - costs.slippage)
    assert fills[0].price == expected
    assert fills[0].fee == costs.taker_fee * Decimal("1") * expected
    assert fills[0].reason == "stop"


def test_stop_buy_fills_at_bar_open_when_bar_gaps_above_the_level() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.STOP_MARKET, "1", None, "67700"))
    fills = b.apply_bar(_bar(BAR_GAP_UP))
    assert len(fills) == 1
    expected = Decimal("67750") * (Decimal(1) + costs.slippage)
    assert fills[0].price == expected
    assert fills[0].fee == costs.taker_fee * Decimal("1") * expected
    assert fills[0].reason == "stop"


def test_stop_sell_fills_at_bar_open_when_bar_gaps_below_the_level() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "1", None, "67652"))
    fills = b.apply_bar(_bar(BAR_GAP_DOWN))
    assert len(fills) == 1
    expected = Decimal("67600") * (Decimal(1) - costs.slippage)
    assert fills[0].price == expected
    assert fills[0].fee == costs.taker_fee * Decimal("1") * expected
    assert fills[0].reason == "stop"


def test_stop_buy_fills_exactly_at_high_and_not_a_penny_above() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.STOP_MARKET, "1", None, "67735.6"))
    b.submit(_order(Side.BUY, OrderKind.STOP_MARKET, "1", None, "67735.61"))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67735.6") * (Decimal(1) + Decimal("0.0005"))


def test_stop_sell_fills_exactly_at_low_and_not_a_penny_below() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "1", None, "67650.0"))
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "1", None, "67649.99"))
    fills = b.apply_bar(_bar(BAR_A))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67650.0") * (Decimal(1) - Decimal("0.0005"))


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
        Decimal("67700") * (Decimal(1) + Decimal("0.0005")),
        Decimal("67652.71"),
        Decimal(BAR_A[4]) * (Decimal(1) + Decimal("0.0005")),
    ]


def test_reduce_only_raises_while_position_is_flat() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    with pytest.raises(ValueError):
        b.submit(Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("67652.71"), None, True))
    with pytest.raises(ValueError):
        b.submit(Order(Side.SELL, OrderKind.MARKET, Decimal("1"), None, None, True))
    assert b.pending_orders == []


def test_reduce_only_raises_only_when_it_exceeds_the_open_position() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "5", "100", None))
    b.apply_bar(_bar(BAR_LOW_99))
    assert b.position.qty == Decimal("5")
    with pytest.raises(ValueError):
        b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("6"), Decimal("200"), None, True))
    assert b.pending_orders == []
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("5"), Decimal("200"), None, True))
    assert len(b.pending_orders) == 1


def test_reduce_only_order_is_dropped_when_position_already_closed() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "10", "100", None))
    b.apply_bar(_bar(BAR_LOW_99))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("5"), Decimal("200"), None, True))
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "10", None, "99"))
    fills = b.apply_bar(_bar(BAR_BOTH))
    assert [f.qty for f in fills] == [Decimal("10")]
    assert [f.reason for f in fills] == ["stop"]
    assert b.pending_orders == []
    assert b.position.qty == Decimal("0")
    assert b.position.entry_price == Decimal("0")


def test_reduce_only_is_truncated_to_open_size_at_execution() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "5", "100", None))
    b.apply_bar(_bar(BAR_LOW_99))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("5"), Decimal("200"), None, True))
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "2", None, "99"))
    fills = b.apply_bar(_bar(BAR_BOTH))
    assert [f.qty for f in fills] == [Decimal("2"), Decimal("3")]
    assert [f.reason for f in fills] == ["stop", "limit"]
    assert b.position.qty == Decimal("0")
    assert b.position.entry_price == Decimal("0")


def test_opening_and_increasing_fills_average_entry_by_quantity() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "100", None))
    assert b.apply_bar(_bar(BAR_LOW_99))[0].price == Decimal("100")
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "200", None))
    assert b.apply_bar(_bar(BAR_LOW_199))[0].price == Decimal("200")
    assert b.position.side is Side.BUY
    assert b.position.qty == Decimal("2")
    assert b.position.entry_price == Decimal("150")


def test_fill_against_position_reduces_then_flips_with_remainder() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "100", None))
    b.apply_bar(_bar(BAR_LOW_99))
    b.submit(_order(Side.SELL, OrderKind.LIMIT, "2", "200", None))
    fills = b.apply_bar(_bar(BAR_LOW_199))
    assert [f.qty for f in fills] == [Decimal("2")]
    assert b.position.side is Side.SELL
    assert b.position.qty == Decimal("1")
    assert b.position.entry_price == Decimal("200")


def test_fill_that_closes_position_resets_entry_and_exits() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "100", None))
    b.apply_bar(_bar(BAR_LOW_99))
    b.submit(_order(Side.SELL, OrderKind.LIMIT, "1", "200", None))
    b.apply_bar(_bar(BAR_LOW_199))
    assert b.position.qty == Decimal("0")
    assert b.position.entry_price == Decimal("0")
    assert b.position.stop_price is None
    assert b.position.take_profit is None
    assert not b.position.is_open


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
