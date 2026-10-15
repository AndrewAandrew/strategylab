import dataclasses
from decimal import Decimal

import pytest

from strategylab.core.bars import Bar, Interval


def _bar(ts: int, o: str, h: str, low: str, c: str, v: str = "1") -> Bar:
    return Bar(ts, Decimal(o), Decimal(h), Decimal(low), Decimal(c), Decimal(v))


def test_bar_is_frozen() -> None:
    b = _bar(1717200000, "1", "2", "0.5", "1.5")
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.close = Decimal("9")  # type: ignore[misc]


def test_interval_factors() -> None:
    assert Interval.MINUTE.factor == 1
    assert Interval.FIVE_MIN.factor == 5
    assert Interval.FIFTEEN_MIN.factor == 15
    assert Interval.HOUR.factor == 60