from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from strategylab.broker.orders import Fill, Side


@dataclass(slots=True)
class Position:
    """Открытая позиция. qty — положительная величина, направление задаёт side."""

    side: Side = Side.BUY
    qty: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None

    @property
    def is_open(self) -> bool:
        """Позиция открыта, когда qty > 0; направление на это не влияет."""
        return self.qty > 0

    def apply_fill(self, fill: Fill) -> None:
        """Применяет филл к позиции: открытие, усреднение, уменьшение или переворот.

        Единственная точка, где позиция меняется. Все цены — Decimal.
        """
        if self.qty == 0:
            self.side = fill.side
            self.qty = fill.qty
            self.entry_price = fill.price
            return
        if fill.side == self.side:
            total = self.qty + fill.qty
            self.entry_price = (self.entry_price * self.qty + fill.price * fill.qty) / total
            self.qty = total
            return
        if fill.qty < self.qty:
            self.qty -= fill.qty
            return
        if fill.qty == self.qty:
            self.qty = Decimal("0")
            self.entry_price = Decimal("0")
            self.stop_price = None
            self.take_profit = None
            return
        self.side = fill.side
        self.qty = fill.qty - self.qty
        self.entry_price = fill.price
        self.stop_price = None
        self.take_profit = None

    def unrealised(self, price: Decimal) -> Decimal:
        """Нереализованный pnl при заданной цене. Только Decimal, без float."""
        if self.side == Side.SELL:
            return (self.entry_price - price) * self.qty
        return (price - self.entry_price) * self.qty
