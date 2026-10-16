from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from strategylab.core.bars import Bar
from strategylab.strategy.base import Strategy, StrategyState

if TYPE_CHECKING:
    from strategylab.broker.orders import Order


def _bar(ts: int, close: str) -> Bar:
    c = Decimal(close)
    return Bar(ts, c, c, c, c, Decimal("1"))


def test_state_is_frozen_and_carries_fields() -> None:
    st = StrategyState(position=Decimal("0"), equity=Decimal("10000"), bar_index=0)
    assert st.position == Decimal("0")
    assert st.equity == Decimal("10000")
    assert st.bar_index == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.position = Decimal("1")  # type: ignore[misc]


class _Stub:
    def __init__(self) -> None:
        self.name = "stub"

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        return []


class _NoOnBar:
    name = "no_on_bar"


def test_protocol_accepts_object_with_name_and_on_bar() -> None:
    assert isinstance(_Stub(), Strategy)


def test_protocol_rejects_object_without_on_bar() -> None:
    assert not isinstance(_NoOnBar(), Strategy)


def test_protocol_rejects_plain_object() -> None:
    assert not isinstance(object(), Strategy)
