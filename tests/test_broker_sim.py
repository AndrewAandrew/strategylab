import dataclasses
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator, LiquidationError
from strategylab.core.bars import Bar
from strategylab.core.position import Position

BAR_A = (1717200000, "67659.5", "67735.6", "67650.0", "67663.2")
BAR_B = (1717200600, "67670.0", "67710.0", "67640.0", "67690.0")
BAR_C = (1717201200, "67610.0", "67640.0", "67600.0", "67620.0")
BAR_GAP_UP = (1717200000, "67750", "67800", "67700", "67780")
BAR_GAP_DOWN = (1717200600, "67600", "67640", "67550", "67610")
BAR_LOW_99 = (1717200000, "101", "102", "99", "100.5")
BAR_LOW_199 = (1717200600, "201", "202", "199", "200.5")
BAR_BOTH = (1717201200, "150", "250", "90", "200")
BAR_FORK = (1717201200, "100", "120", "80", "100")
BAR_TARGET_EXACT = (1717201200, "100", "110", "100", "105")
BAR_TARGET_MISS = (1717201200, "100", "109.99", "100", "105")
BAR_ABOVE_110 = (1717201800, "112", "115", "111", "113")
BAR_GAP_DOWN_STOP = (1717201200, "80", "85", "75", "82")
BAR_LIQ_LONG_HIT = (1717201200, "100", "100", "40", "100")
BAR_LIQ_LONG_BOUNDARY = (1717201200, "100", "100", "50", "100")
BAR_LIQ_LONG_MISS = (1717201200, "100", "100", "50.01", "100")
BAR_LIQ_SHORT_HIT = (1717201200, "100", "160", "100", "100")
BAR_LIQ_SHORT_BOUNDARY = (1717201200, "100", "150", "100", "100")
BAR_LIQ_SHORT_MISS = (1717201200, "100", "149.99", "100", "100")


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


def _open_long(b: BrokerSimulator, qty: str = "1", entry: str = "100") -> None:
    b.submit(_order(Side.BUY, OrderKind.LIMIT, qty, entry, None))
    b.apply_bar(_bar(BAR_LOW_99))


def _open_short(b: BrokerSimulator, qty: str = "1", entry: str = "100") -> None:
    b.submit(_order(Side.SELL, OrderKind.LIMIT, qty, entry, None))
    b.apply_bar(_bar(BAR_LOW_99))


def _set_long_protection(b: BrokerSimulator, stop: str = "90", target: str = "110") -> None:
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal(stop), True))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("1"), Decimal(target), None, True))


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
    assert [f.reason for f in fills] == ["stop", "take_profit"]
    assert b.position.qty == Decimal("0")
    assert b.position.entry_price == Decimal("0")


def test_reduce_only_limit_surviving_a_flip_does_not_grow_the_new_position() -> None:
    """reduce-only заявка, пережившая переворот, снимается: уменьшать ей больше нечего.

    Лонг 1 по 100, отложенный reduce-only SELL LIMIT 1 по 110. MARKET SELL 2
    закрывает лонг и открывает шорт 1. На следующем баре лимит по 110 обязан
    быть снят, а не исполниться: иначе шорт молча растёт 1 → 2.
    """
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "100", None))
    b.apply_bar(_bar(BAR_LOW_99))
    assert (b.position.side, b.position.qty) == (Side.BUY, Decimal("1"))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("1"), Decimal("110"), None, True))
    b.submit(_order(Side.SELL, OrderKind.MARKET, "2", None, None))
    # Бар с high 109.99: зеркальная цель 110 не задета, лимит ещё не исполнялся.
    b.apply_bar(_bar(BAR_TARGET_MISS))
    assert (b.position.side, b.position.qty) == (Side.SELL, Decimal("1"))
    # Переворот произошёл внутри бара: сторона позиции больше не та, что
    # уменьшала заявка, — она снята сразу, а не на следующем баре.
    assert b.pending_orders == []
    # Бар с low 111 и high 115: лимит по 110 мог бы исполниться — заявки уже нет.
    fills = b.apply_bar(_bar(BAR_ABOVE_110))
    assert fills == []
    assert (b.position.side, b.position.qty) == (Side.SELL, Decimal("1"))


def test_reduce_only_order_on_the_same_side_as_the_open_position_is_rejected() -> None:
    """reduce-only заявка не может увеличивать позицию: та же сторона — отказ на подаче."""
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(_order(Side.BUY, OrderKind.LIMIT, "1", "100", None))
    b.apply_bar(_bar(BAR_LOW_99))
    with pytest.raises(ValueError):
        b.submit(Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("120"), None, True))
    assert b.pending_orders == []
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("1"), Decimal("110"), None, True))
    assert len(b.pending_orders) == 1


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


def test_reduce_only_stop_market_mirrors_stop_price() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("90"), True))
    assert b.position.stop_price == Decimal("90")
    assert b.position.take_profit is None


def test_reduce_only_limit_mirrors_take_profit() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("1"), Decimal("110"), None, True))
    assert b.position.take_profit == Decimal("110")
    assert b.position.stop_price is None


def test_fork_pessimistic_fills_stop_when_both_levels_inside_bar() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    fills = b.apply_bar(_bar(BAR_FORK))
    stop_price = Decimal("90") * (Decimal(1) - costs.slippage)
    assert fills == [
        Fill(
            ts=BAR_FORK[0],
            side=Side.SELL,
            price=stop_price,
            qty=Decimal("1"),
            fee=costs.taker_fee * Decimal("1") * stop_price,
            reason="stop",
        )
    ]
    assert b.position.qty == Decimal("0")
    assert b.pending_orders == []


def test_fork_optimistic_fills_target_when_both_levels_inside_bar() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.OPTIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    fills = b.apply_bar(_bar(BAR_FORK))
    assert fills == [
        Fill(
            ts=BAR_FORK[0],
            side=Side.SELL,
            price=Decimal("110"),
            qty=Decimal("1"),
            fee=costs.maker_fee * Decimal("1") * Decimal("110"),
            reason="take_profit",
        )
    ]
    assert b.position.qty == Decimal("0")
    assert b.pending_orders == []


def test_fork_pair_gives_opposite_outcomes_on_the_same_bar() -> None:
    pessimistic = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    optimistic = BrokerSimulator(CostModel(), IntrabarOrder.OPTIMISTIC)
    _open_long(pessimistic)
    _open_long(optimistic)
    _set_long_protection(pessimistic)
    _set_long_protection(optimistic)
    pessimistic_fills = pessimistic.apply_bar(_bar(BAR_FORK))
    optimistic_fills = optimistic.apply_bar(_bar(BAR_FORK))
    assert [f.reason for f in pessimistic_fills] == ["stop"]
    assert [f.reason for f in optimistic_fills] == ["take_profit"]
    assert pessimistic_fills[0].price != optimistic_fills[0].price


def test_position_stop_fills_at_bar_open_when_bar_gaps_below_the_level() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    fills = b.apply_bar(_bar(BAR_GAP_DOWN_STOP))
    gap_price = Decimal("80") * (Decimal(1) - costs.slippage)
    assert fills == [
        Fill(
            ts=BAR_GAP_DOWN_STOP[0],
            side=Side.SELL,
            price=gap_price,
            qty=Decimal("1"),
            fee=costs.taker_fee * Decimal("1") * gap_price,
            reason="stop",
        )
    ]


def test_position_target_fills_exactly_at_level_without_slippage() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    fills = b.apply_bar(_bar(BAR_TARGET_EXACT))
    assert fills == [
        Fill(
            ts=BAR_TARGET_EXACT[0],
            side=Side.SELL,
            price=Decimal("110"),
            qty=Decimal("1"),
            fee=costs.maker_fee * Decimal("1") * Decimal("110"),
            reason="take_profit",
        )
    ]


def test_position_target_does_not_fill_a_hair_below_the_level() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    assert b.apply_bar(_bar(BAR_TARGET_MISS)) == []
    assert b.position.qty == Decimal("1")


def test_position_stop_is_checked_before_resting_limits() -> None:
    costs = CostModel()
    b = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    b.submit(_order(Side.SELL, OrderKind.LIMIT, "1", "110", None))
    fills = b.apply_bar(_bar(BAR_FORK))
    assert [f.reason for f in fills] == ["stop", "limit"]
    assert [f.price for f in fills] == [
        Decimal("90") * (Decimal(1) - costs.slippage),
        Decimal("110"),
    ]
    assert b.position.side is Side.SELL
    assert b.position.qty == Decimal("1")


def test_exit_cancels_all_paired_reduce_only_orders() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    b.apply_bar(_bar(BAR_GAP_DOWN_STOP))
    assert b.pending_orders == []
    assert b.position.stop_price is None
    assert b.position.take_profit is None


def test_dead_paired_order_does_not_execute_on_the_next_bar() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    b.apply_bar(_bar(BAR_GAP_DOWN_STOP))
    assert b.pending_orders == []
    assert b.apply_bar(_bar(BAR_TARGET_EXACT)) == []


def test_liquidation_long_raises_when_adverse_extreme_reaches_margin() -> None:
    b = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    with pytest.raises(LiquidationError):
        b.apply_bar(_bar(BAR_LIQ_LONG_HIT))
    assert b.position.qty == Decimal("0")
    assert b.position.entry_price == Decimal("0")
    assert b.pending_orders == []


def test_liquidation_long_does_not_raise_a_hair_above_the_margin_point() -> None:
    b = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    assert b.apply_bar(_bar(BAR_LIQ_LONG_MISS)) == []
    assert b.position.qty == Decimal("1")


def test_liquidation_short_raises_when_adverse_extreme_reaches_margin() -> None:
    b = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_short(b)
    with pytest.raises(LiquidationError):
        b.apply_bar(_bar(BAR_LIQ_SHORT_HIT))
    assert b.position.qty == Decimal("0")
    assert b.position.entry_price == Decimal("0")


def test_liquidation_short_does_not_raise_a_hair_below_the_margin_point() -> None:
    b = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_short(b)
    assert b.apply_bar(_bar(BAR_LIQ_SHORT_MISS)) == []
    assert b.position.qty == Decimal("1")


def test_liquidation_boundary_exact_point_raises_for_both_directions() -> None:
    long_sim = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    short_sim = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_long(long_sim)
    _open_short(short_sim)
    with pytest.raises(LiquidationError):
        long_sim.apply_bar(_bar(BAR_LIQ_LONG_BOUNDARY))
    with pytest.raises(LiquidationError):
        short_sim.apply_bar(_bar(BAR_LIQ_SHORT_BOUNDARY))


def test_liquidation_close_reports_stop_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    b = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    applied: list[Fill] = []
    original = Position.apply_fill

    def spy(self: Position, fill: Fill) -> None:
        applied.append(fill)
        original(self, fill)

    monkeypatch.setattr(Position, "apply_fill", spy)
    with pytest.raises(LiquidationError):
        b.apply_bar(_bar(BAR_LIQ_LONG_HIT))
    assert [f.reason for f in applied] == ["stop"]


def test_position_closed_by_its_stop_does_not_liquidate() -> None:
    b = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b, stop="60", target="110")
    fills = b.apply_bar(_bar(BAR_LIQ_LONG_HIT))
    assert [f.reason for f in fills] == ["stop"]
    assert b.position.qty == Decimal("0")


def test_position_closed_by_stop_market_order_does_not_liquidate() -> None:
    b = BrokerSimulator(CostModel(leverage=Decimal("2")), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    b.submit(_order(Side.SELL, OrderKind.STOP_MARKET, "1", None, "60"))
    fills = b.apply_bar(_bar(BAR_LIQ_LONG_HIT))
    assert [f.reason for f in fills] == ["stop"]
    assert b.position.qty == Decimal("0")


def test_position_is_read_only_publicly() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    with pytest.raises(AttributeError):
        b.position = Position()  # type: ignore[misc]
    assert b.position.qty == Decimal("0")
    assert b.position.is_open is False


def test_take_profit_reason_is_pinned() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _open_long(b)
    _set_long_protection(b)
    fills = b.apply_bar(_bar(BAR_TARGET_EXACT))
    assert [f.reason for f in fills] == ["take_profit"]


# Частичный тейк-профит: зеркало цели с размером, отдыхающий лимит того же уровня


def _open_bracketed_long() -> BrokerSimulator:
    """Лонг 20 по 100 с брекетом: стоп весь объём на 95, лимит половины на 107.5."""
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _open_long(b, "20", "100")
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("20"), None, Decimal("95"), True))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("10"), Decimal("107.5"), None, True))
    return b


PARTIAL_BAR = (1717201200, "100", "108", "100", "105")


def test_partial_target_mirrors_size_and_fills_only_half() -> None:
    """Цель с половинным зеркалом закрывает половину позиции, стоп остаётся жив."""
    b = _open_bracketed_long()

    fills = b.apply_bar(_bar(PARTIAL_BAR))

    assert [f.reason for f in fills] == ["take_profit"]
    assert [f.qty for f in fills] == [Decimal("10")]
    assert fills[0].price == Decimal("107.5")
    assert b.position.qty == Decimal("10")
    assert b.position.stop_price == Decimal("95")


def test_partial_target_clears_mirror_and_the_resting_same_level_limit() -> None:
    """После частичного выхода зеркало снято, и отдыхающий лимит уровня снят.

    Второй бар с тем же хаєм не может исполнить остаток «вторым списком»:
    уровень цели отработан полностью, у остатка цели нет до новой постановки.
    """
    b = _open_bracketed_long()
    b.apply_bar(_bar(PARTIAL_BAR))

    assert b.position.take_profit is None
    assert b.position.take_profit_qty is None
    assert [o for o in b.pending_orders if o.kind is OrderKind.LIMIT] == []

    fills = b.apply_bar(_bar((1717201800, "107", "108", "96", "107")))

    assert fills == []
    assert b.position.qty == Decimal("10")


def test_resubmitted_bracket_mirrors_breakeven_stop_and_new_target_on_remaining() -> None:
    """Перезакладка брекета по остатку: стоп в безубыток, цель TP2 = перезапись зеркал."""
    b = _open_bracketed_long()
    b.apply_bar(_bar(PARTIAL_BAR))
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("10"), None, Decimal("100"), True))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("10"), Decimal("115"), None, True))

    assert (
        b.position.qty,
        b.position.stop_price,
        b.position.take_profit,
        b.position.take_profit_qty,
    ) == (Decimal("10"), Decimal("100"), Decimal("115"), Decimal("10"))

    fills = b.apply_bar(_bar((1717202400, "108", "116", "108", "115")))
    assert [f.reason for f in fills] == ["take_profit"]
    assert [f.qty for f in fills] == [Decimal("10")]
    assert fills[0].price == Decimal("115")
    assert not b.position.is_open
    assert b.pending_orders == []


def test_partial_target_does_not_overwhelm_a_stop_on_the_same_bar() -> None:
    """Стоп и частичная цель на одном баре: PESSIMISTIC решает в пользу стопа.
    """
    costs = CostModel()
    b = _open_bracketed_long()

    fills = b.apply_bar(_bar((1717201200, "100", "108", "94", "100")))

    assert [f.reason for f in fills] == ["stop"]
    assert [f.qty for f in fills] == [Decimal("20")]
    assert fills[0].price == Decimal("95") * (Decimal(1) - costs.slippage)
    assert not b.position.is_open
    assert b.pending_orders == []


def test_second_stop_submit_replaces_the_resting_mirror() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False))
    b.apply_bar(_bar((1, "100", "101", "99", "100")))
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("95"), True))
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("98"), True))
    stops = [o for o in b.pending_orders if o.kind is OrderKind.STOP_MARKET]
    assert len(stops) == 1 and stops[0].stop_price == Decimal("98")
    assert b.position.stop_price == Decimal("98")


def test_limit_mirror_is_not_deduplicated() -> None:
    # Две reduce_only LIMIT разных объёмов — разные цели: движок обязан оставить обе.
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False))
    b.apply_bar(_bar((1, "100", "101", "99", "100")))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("0.4"), Decimal("105"), None, True))
    b.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("0.6"), Decimal("110"), None, True))
    assert len([o for o in b.pending_orders if o.kind is OrderKind.LIMIT]) == 2


# Трейлинг ATR-стопа после входа (задача 10): гэп и лестница перезаказов


def test_short_trailing_stop_fills_on_gap_up() -> None:
    """Гэп выше resting-СТОПа шорта: PESSIMISTIC выбивает стоп по open бара.

    Шорт открыт MARKET SELL (исполнение по close первого бара), поверх него
    отдыхает reduce-only BUY STOP_MARKET на 96 — уровень трейла. Второй бар
    открывается на 99, то есть гэпом ВЫШЕ стопа, а high 101 доходит до уровня
    лишь из гэпа: стоп обязан исполниться по open, а не по 96.
    """
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.SELL, OrderKind.MARKET, Decimal("1"), None, None, False))
    b.apply_bar(_bar((1, "99", "101", "94", "100")))
    assert (b.position.side, b.position.qty) == (Side.SELL, Decimal("1"))
    b.submit(Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("96"), True))
    fills = b.apply_bar(_bar((2, "99", "101", "94", "100")))
    assert fills[0].reason == "stop"
    assert fills[0].price > Decimal("96")


def test_ten_trailing_stop_resubmits_leave_one_resting_order() -> None:
    """Лестница из десяти перезаказов трейла: в очереди остаётся один стоп.

    Dedup Task 5 под нагрузкой: каждый следующий reduce-only STOP_MARKET той
    же стороны вытесняет предыдущего предшественника, накопления нет.
    """
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.SELL, OrderKind.MARKET, Decimal("1"), None, None, False))
    b.apply_bar(_bar((1, "99", "101", "94", "100")))
    for level in range(10):
        b.submit(
            Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal(level), True)
        )
    stops = [o for o in b.pending_orders if o.kind is OrderKind.STOP_MARKET]
    assert len(b.pending_orders) == 1
    assert stops == [
        Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("9"), True)
    ]
    assert b.position.stop_price == Decimal("9")
