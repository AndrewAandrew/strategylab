from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.core.position import Position


class LiquidationError(Exception):
    """Капитал обеспечения исчерпан: позиция закрыта принудительно, а не уходит в минус."""


@dataclass(slots=True)
class BrokerSimulator:
    """Брокер-симулятор: решает, исполнится ли заявка, по какой цене и с какой комиссией."""

    costs: CostModel
    intrabar: IntrabarOrder
    position: Position = field(default_factory=Position)
    _pending: list[Order] = field(default_factory=list, repr=False)

    @property
    def pending_orders(self) -> list[Order]:
        """Заявки, ждущие исполнения. Копия: внутреннее состояние не изменяется."""
        return list(self._pending)

    def submit(self, order: Order) -> None:
        if order.kind is OrderKind.STOP_MARKET and order.stop_price is None:
            raise ValueError("stop_market order requires stop_price")
        if order.kind is OrderKind.LIMIT and order.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if order.reduce_only and order.qty > self.position.qty:
            raise ValueError("reduce_only order is larger than the open position")
        self._pending.append(order)

    def apply_bar(self, bar: Bar) -> list[Fill]:
        fills = self._apply_stops(bar)
        # Шаг (2) порядка обработки бара — стоп-лосс и тейк-профит открытой
        # позиции по high/low с учётом IntrabarOrder — реализует задача B.
        fills += self._apply_limits(bar)
        fills += self._apply_markets(bar)
        return fills

    def _apply_stops(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        rest: list[Order] = []
        for order in self._pending:
            qty = self._effective_qty(order)
            if qty is None:
                continue
            fill = self._stop_fill(order, bar, qty)
            if fill is None:
                rest.append(order)
            else:
                fills.append(fill)
                self.position.apply_fill(fill)
        self._pending = rest
        return fills

    def _apply_limits(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        rest: list[Order] = []
        for order in self._pending:
            qty = self._effective_qty(order)
            if qty is None:
                continue
            fill = self._limit_fill(order, bar, qty)
            if fill is None:
                rest.append(order)
            else:
                fills.append(fill)
                self.position.apply_fill(fill)
        self._pending = rest
        return fills

    def _apply_markets(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        rest: list[Order] = []
        for order in self._pending:
            qty = self._effective_qty(order)
            if qty is None:
                continue
            fill = self._market_fill(order, bar, qty)
            if fill is None:
                rest.append(order)
            else:
                fills.append(fill)
                self.position.apply_fill(fill)
        self._pending = rest
        return fills

    def _effective_qty(self, order: Order) -> Decimal | None:
        """Размер филла; None — reduce-only заявка при плоской позиции отбрасывается."""
        if not order.reduce_only:
            return order.qty
        if self.position.qty == 0:
            return None
        return min(order.qty, self.position.qty)

    def _stop_base(self, bar: Bar, side: Side, stop: Decimal) -> Decimal:
        """Уровень исполнения стопа: stop при касании снизу, open при пробое гэпом."""
        if side is Side.BUY:
            return bar.open if bar.open >= stop else stop
        return bar.open if bar.open <= stop else stop

    def _stop_fill(self, order: Order, bar: Bar, qty: Decimal) -> Fill | None:
        if order.kind is not OrderKind.STOP_MARKET or order.stop_price is None:
            return None
        stop = order.stop_price
        if order.side is Side.BUY and bar.high < stop:
            return None
        if order.side is Side.SELL and bar.low > stop:
            return None
        price = self._slipped(self._stop_base(bar, order.side, stop), order.side)
        return self._make_fill(bar, order, price, self.costs.taker_fee, "stop", qty)

    def _limit_fill(self, order: Order, bar: Bar, qty: Decimal) -> Fill | None:
        if order.kind is not OrderKind.LIMIT or order.limit_price is None:
            return None
        if order.side is Side.BUY and bar.low > order.limit_price:
            return None
        if order.side is Side.SELL and bar.high < order.limit_price:
            return None
        return self._make_fill(bar, order, order.limit_price, self.costs.maker_fee, "limit", qty)

    def _market_fill(self, order: Order, bar: Bar, qty: Decimal) -> Fill | None:
        if order.kind is not OrderKind.MARKET:
            return None
        price = self._slipped(bar.close, order.side)
        return self._make_fill(bar, order, price, self.costs.taker_fee, "market", qty)

    def _slipped(self, price: Decimal, side: Side) -> Decimal:
        if side is Side.BUY:
            return price * (Decimal(1) + self.costs.slippage)
        return price * (Decimal(1) - self.costs.slippage)

    def _make_fill(
        self,
        bar: Bar,
        order: Order,
        price: Decimal,
        fee_rate: Decimal,
        reason: str,
        qty: Decimal,
    ) -> Fill:
        return Fill(
            ts=bar.ts,
            side=order.side,
            price=price,
            qty=qty,
            fee=fee_rate * qty * price,
            reason=reason,
        )
