from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from strategylab.broker.orders import Fill, Side


@dataclass(slots=True)
class Position:
    """Открытая позиция. qty — положительная величина, направление задаёт side.

    `entry_fee` — накопленная входная комиссия позиции: комиссия филлов,
    открывших и увеличивших её. Она — долг перед pnl: каждое частичное
    закрытие расходует пропорциональную долю, полное закрытие и переворот
    обнуляют её. При перевороте новой позиции достаётся доля комиссии
    закрывающего филла, приходящаяся на её объём.
    """

    side: Side = Side.BUY
    qty: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    entry_fee: Decimal = Decimal("0")
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None
    take_profit_qty: Decimal | None = None

    @property
    def is_open(self) -> bool:
        """Позиция открыта, когда qty > 0; направление на это не влияет."""
        return self.qty > 0

    def apply_fill(self, fill: Fill) -> None:
        """Применяет филл к позиции: открытие, усреднение, уменьшение или переворот.

        Единственная точка, где позиция меняется. Все цены — Decimal.
        Частичное уменьшение расходует зеркало цели: исполненная часть
        снимается с `take_profit_qty`, и когда цель исполнена целиком,
        её уровень живым не остаётся. Той же пропорцией расходуется
        входная комиссия: закрытые единицы не будут платить её ещё раз.
        """
        if self.qty == 0:
            self.side = fill.side
            self.qty = fill.qty
            self.entry_price = fill.price
            self.entry_fee = fill.fee
            self.take_profit_qty = None
            return
        if fill.side == self.side:
            total = self.qty + fill.qty
            self.entry_price = (self.entry_price * self.qty + fill.price * fill.qty) / total
            self.qty = total
            self.entry_fee += fill.fee
            return
        if fill.qty < self.qty:
            left = self.qty - fill.qty
            self.entry_fee = self.entry_fee * left / self.qty
            self.qty = left
            if self.take_profit_qty is not None:
                self.take_profit_qty -= fill.qty
                if self.take_profit_qty <= 0:
                    self.take_profit = None
                    self.take_profit_qty = None
            return
        if fill.qty == self.qty:
            self.qty = Decimal("0")
            self.entry_price = Decimal("0")
            self.entry_fee = Decimal("0")
            self.stop_price = None
            self.take_profit = None
            self.take_profit_qty = None
            return
        # Переворот: закрывающая часть филла уходит с остатком старой
        # позиции, новой достаётся доля комиссии, приходящаяся на её объём.
        new_fee = fill.fee * (fill.qty - self.qty) / fill.qty
        self.side = fill.side
        self.qty = fill.qty - self.qty
        self.entry_price = fill.price
        self.entry_fee = new_fee
        self.stop_price = None
        self.take_profit = None
        self.take_profit_qty = None

    def unrealised(self, price: Decimal) -> Decimal:
        """Нереализованный pnl при заданной цене. Только Decimal, без float."""
        if self.side == Side.SELL:
            return (self.entry_price - price) * self.qty
        return (price - self.entry_price) * self.qty
