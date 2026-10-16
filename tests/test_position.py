from decimal import Decimal

from strategylab.broker.orders import Side
from strategylab.core.position import Position


def test_long_unrealised_gains_when_price_rises() -> None:
    p = Position(side=Side.BUY, qty=Decimal("2"), entry_price=Decimal("100"))
    assert p.unrealised(Decimal("110")) == Decimal("20")
    assert p.unrealised(Decimal("90")) == Decimal("-20")


def test_short_unrealised_gains_when_price_falls() -> None:
    p = Position(side=Side.SELL, qty=Decimal("2"), entry_price=Decimal("100"))
    assert p.unrealised(Decimal("90")) == Decimal("20")
    assert p.unrealised(Decimal("110")) == Decimal("-20")


def test_unrealised_scales_with_qty() -> None:
    p = Position(side=Side.BUY, qty=Decimal("0.5"), entry_price=Decimal("100"))
    assert p.unrealised(Decimal("120")) == Decimal("10")


def test_flat_position_is_closed_with_zero_unrealised() -> None:
    p = Position(side=Side.BUY, qty=Decimal("0"), entry_price=Decimal("100"))
    assert not p.is_open
    assert p.unrealised(Decimal("1234.5")) == Decimal("0")


def test_is_open_depends_on_qty_not_side() -> None:
    long = Position(side=Side.BUY, qty=Decimal("1"), entry_price=Decimal("100"))
    short = Position(side=Side.SELL, qty=Decimal("1"), entry_price=Decimal("100"))
    assert long.is_open
    assert short.is_open


def test_flat_position_may_carry_any_side() -> None:
    for side in Side:
        p = Position(side=side, qty=Decimal("0"), entry_price=Decimal("100"))
        assert not p.is_open
        assert p.unrealised(Decimal("1234.5")) == Decimal("0")


def test_position_is_mutable() -> None:
    p = Position()
    p.side = Side.SELL
    p.qty = Decimal("3")
    p.entry_price = Decimal("100")
    p.stop_price = Decimal("95")
    p.take_profit = Decimal("110")
    assert p.side == Side.SELL
    assert p.qty == Decimal("3")
    assert p.entry_price == Decimal("100")
    assert p.stop_price == Decimal("95")
    assert p.take_profit == Decimal("110")
    assert p.is_open


def test_position_defaults_to_flat() -> None:
    p = Position()
    assert not p.is_open
    assert p.qty == Decimal("0")
    assert p.entry_price == Decimal("0")
    assert p.stop_price is None
    assert p.take_profit is None
