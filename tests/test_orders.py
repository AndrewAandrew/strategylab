import dataclasses
from decimal import Decimal

import pytest

from strategylab.broker.orders import Fill, Order, OrderKind, Side


def test_side_str_is_bare_string() -> None:
    assert str(Side.BUY) == "buy"
    assert f"{Side.SELL}" == "sell"
    assert Side.BUY == "buy"
    assert Side.SELL == "sell"
    assert Side.BUY != "sell"


def test_order_kind_str_is_bare_string() -> None:
    assert str(OrderKind.MARKET) == "market"
    assert f"{OrderKind.LIMIT}" == "limit"
    assert f"{OrderKind.STOP_MARKET}" == "stop_market"
    assert OrderKind.MARKET == "market"
    assert OrderKind.LIMIT == "limit"
    assert OrderKind.STOP_MARKET == "stop_market"
    assert OrderKind.MARKET != "limit"


def test_order_is_frozen_and_carries_fields() -> None:
    o = Order(
        side=Side.BUY,
        kind=OrderKind.MARKET,
        qty=Decimal("1.5"),
        limit_price=None,
        stop_price=None,
        reduce_only=False,
    )
    assert o.side == Side.BUY
    assert o.kind == OrderKind.MARKET
    assert o.qty == Decimal("1.5")
    assert o.limit_price is None
    assert o.stop_price is None
    assert o.reduce_only is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.qty = Decimal("2")  # type: ignore[misc]


def test_order_keeps_optional_prices() -> None:
    o = Order(
        side=Side.SELL,
        kind=OrderKind.LIMIT,
        qty=Decimal("1"),
        limit_price=Decimal("100.25"),
        stop_price=None,
        reduce_only=True,
    )
    assert o.limit_price == Decimal("100.25")
    assert o.stop_price is None
    assert o.reduce_only is True


def test_fill_is_frozen_and_carries_fields() -> None:
    f = Fill(
        ts=1_717_200_000,
        side=Side.BUY,
        price=Decimal("50000.5"),
        qty=Decimal("0.25"),
        fee=Decimal("0.5"),
        reason="entry",
    )
    assert f.ts == 1_717_200_000
    assert f.side == Side.BUY
    assert f.price == Decimal("50000.5")
    assert f.qty == Decimal("0.25")
    assert f.fee == Decimal("0.5")
    assert f.reason == "entry"
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.qty = Decimal("1")  # type: ignore[misc]
