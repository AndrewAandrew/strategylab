from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum


class IntrabarOrder(IntEnum):
    """Какой из двух уровней внутри бара считается достигнутым первым."""

    PESSIMISTIC = 0
    OPTIMISTIC = 1


@dataclass(frozen=True, slots=True)
class CostModel:
    """Тарифы и проскальзывание симулятора. Все поля — Decimal, без float."""

    taker_fee: Decimal = Decimal("0.0006")
    maker_fee: Decimal = Decimal("0.0002")
    slippage: Decimal = Decimal("0.0005")
    leverage: Decimal = Decimal("1")
