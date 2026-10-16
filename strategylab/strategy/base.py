from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from strategylab.broker.orders import Order
from strategylab.core.bars import Bar


@dataclass(frozen=True, slots=True)
class StrategyState:
    """Снимок состояния на закрытом баре. position знаковый: > 0 лонг, < 0 шорт, 0 флэт."""

    position: Decimal
    equity: Decimal
    bar_index: int


@runtime_checkable
class Strategy(Protocol):
    """Контракт стратегии: имя и реакция на закрытый бар."""

    name: str

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]: ...
