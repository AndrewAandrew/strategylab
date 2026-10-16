from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from strategylab.broker.orders import Side


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

    def unrealised(self, price: Decimal) -> Decimal:
        """Нереализованный pnl при заданной цене. Только Decimal, без float."""
        if self.side == Side.SELL:
            return (self.entry_price - price) * self.qty
        return (price - self.entry_price) * self.qty
