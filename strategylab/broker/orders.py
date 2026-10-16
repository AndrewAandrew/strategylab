from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    """Сторона сделки. StrEnum: печатается и форматируется как строка значения."""

    BUY = "buy"
    SELL = "sell"


class OrderKind(StrEnum):
    """Тип ордера. StrEnum: печатается и форматируется как строка значения."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"


@dataclass(frozen=True, slots=True)
class Order:
    """Ордер стратегии. Все поля обязательны: reduce_only — флаг безопасности."""

    side: Side
    kind: OrderKind
    qty: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    reduce_only: bool


@dataclass(frozen=True, slots=True)
class Fill:
    """Исполнение ордера брокером."""

    ts: int
    side: Side
    price: Decimal
    qty: Decimal
    fee: Decimal
    reason: str
