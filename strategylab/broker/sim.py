from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.core.position import Position


class LiquidationError(Exception):
    """Капитал обеспечения исчерпан: позиция закрыта принудительно, а не уходит в минус."""


@dataclass(frozen=True, slots=True)
class _Rest:
    """Заявка в очереди брокера: сам ордер + seq бара подачи для TIF-списания."""

    order: Order
    born: int


@dataclass(slots=True)
class BrokerSimulator:
    """Брокер-симулятор: решает, исполнится ли заявка, по какой цене и с какой комиссией."""

    costs: CostModel
    intrabar: IntrabarOrder
    _position: Position = field(default_factory=Position)
    _pending: list[_Rest] = field(default_factory=list, repr=False)
    _seq: int = field(default=0, repr=False)

    @property
    def position(self) -> Position:
        """Открытая позиция, только для чтения: сеттера нет, писатель один — apply_fill."""
        return self._position

    @property
    def pending_orders(self) -> list[Order]:
        """Заявки, ждущие исполнения. Копия: внутреннее состояние не изменяется."""
        return [rest.order for rest in self._pending]

    def submit(self, order: Order) -> None:
        if order.kind is OrderKind.STOP_MARKET and order.stop_price is None:
            raise ValueError("stop_market order requires stop_price")
        if order.kind is OrderKind.LIMIT and order.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if order.reduce_only and order.qty > self._position.qty:
            raise ValueError("reduce_only order is larger than the open position")
        if order.reduce_only and self._position.is_open and order.side is self._position.side:
            raise ValueError("reduce_only order is on the same side as the open position")
        if order.reduce_only and self._position.is_open:
            self._mirror_reduced(order)
        self._pending.append(_Rest(order=order, born=self._seq))

    def _mirror_reduced(self, order: Order) -> None:
        """reduce-only заявка зеркалит свой уровень в позицию.

        STOP_MARKET зеркалит только уровень: стоп исполняется на весь
        остаток позиции, размер зеркалу не нужен. LIMIT зеркалит уровень
        и размер: тейк-профит на уменьшенной заявке исполняется её объёмом,
        а не всей позицией — так работает частичная фиксация прибыли.
        """
        if order.kind is OrderKind.STOP_MARKET and order.stop_price is not None:
            self._position.stop_price = order.stop_price
        elif order.kind is OrderKind.LIMIT and order.limit_price is not None:
            self._position.take_profit = order.limit_price
            self._position.take_profit_qty = order.qty

    def apply_bar(self, bar: Bar) -> list[Fill]:
        self._seq += 1
        self._pending = [
            rest
            for rest in self._pending
            if rest.order.expires_in_bars is None
            or self._seq <= rest.born + rest.order.expires_in_bars
        ]
        fills = self._apply_stops(bar)
        self._cancel_reduced()
        fills += self._apply_exits(bar)
        self._cancel_reduced()
        self._apply_liquidation(bar)
        fills += self._apply_limits(bar)
        self._cancel_reduced()
        fills += self._apply_markets(bar)
        self._cancel_reduced()
        return fills

    def _cancel_reduced(self) -> None:
        """Снять покоящиеся reduce-only заявки, не противящие открытой позиции.

        Позиция плоская — все: уменьшать нечего. Позиция открыта — те, чья
        сторона совпадает со стороной позиции: после переворота они уменьшать
        её больше не могут и исполнением стали бы увеличением.
        """
        if not self._position.is_open:
            self._pending = [rest for rest in self._pending if not rest.order.reduce_only]
            return
        self._pending = [
            rest
            for rest in self._pending
            if not rest.order.reduce_only or rest.order.side is not self._position.side
        ]

    def _apply_stops(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        rest: list[_Rest] = []
        for parked in self._pending:
            order = parked.order
            if order.reduce_only:
                rest.append(parked)
                continue
            qty = self._effective_qty(order)
            if qty is None:
                continue
            fill = self._stop_fill(order, bar, qty)
            if fill is None:
                rest.append(parked)
            else:
                fills.append(fill)
                self._position.apply_fill(fill)
        self._pending = rest
        return fills

    def _apply_exits(self, bar: Bar) -> list[Fill]:
        """Стоп-лосс и тейк-профит открытой позиции по high/low с учётом IntrabarOrder.

        Цель с уменьшенным зеркалом (`take_profit_qty` меньше размера позиции)
        исполняется только своим объёмом: частичная фиксация прибыли оставляет
        остаток позиции без цели до новой постановки. Замена уровня цели после
        частичного выхода — повторной reduce_only-постановкой, как и замена
        стопа.
        """
        position = self._position
        if not position.is_open:
            return []
        stop_hit = self._level_hit(position.stop_price, position.side, bar, is_stop=True)
        target_hit = self._level_hit(position.take_profit, position.side, bar, is_stop=False)
        if not stop_hit and not target_hit:
            return []
        if stop_hit and target_hit:
            take_target = self.intrabar is IntrabarOrder.OPTIMISTIC
        else:
            take_target = target_hit
        exit_side = self._exit_side(position.side)
        if take_target:
            assert position.take_profit is not None
            price = position.take_profit
            qty = self._target_qty(position)
            fill = self._make_fill(bar, exit_side, price, self.costs.maker_fee, "take_profit", qty)
            self._position.apply_fill(fill)
            if self._position.is_open:
                # Частичный выход по цели: зеркало снято — исполненная часть
                # уровня отработана, дышать этому уровню больше нечем. У
                # остатка цель появится только новой постановкой.
                self._position.take_profit = None
                self._position.take_profit_qty = None
                self._drop_resting_target(price, exit_side)
            self._cancel_reduced()
            return [fill]
        assert position.stop_price is not None
        price = self.slipped(self._stop_base(bar, exit_side, position.stop_price), exit_side)
        fill = self._make_fill(bar, exit_side, price, self.costs.taker_fee, "stop", position.qty)
        self._position.apply_fill(fill)
        self._cancel_reduced()
        return [fill]

    @staticmethod
    def _target_qty(position: Position) -> Decimal:
        """Объём исполнения цели: размер зеркала, но не больше позиции."""
        if position.take_profit_qty is None:
            return position.qty
        return min(position.qty, position.take_profit_qty)

    def _drop_resting_target(self, price: Decimal, side: Side) -> None:
        """Снимает отдыхающий reduce-only лимит с только что исполненного уровня.

        Цель исполнилась зеркалом позиции, а отдыхающий лимит того же уровня
        остался в очереди и исполнил бы остаток позиции в этом же баре:
        после частичной фиксации уровень живым не остаётся, остаток ждёт
        новой постановки цели.
        """
        self._pending = [
            rest
            for rest in self._pending
            if not (
                rest.order.reduce_only
                and rest.order.kind is OrderKind.LIMIT
                and rest.order.side is side
                and rest.order.limit_price == price
            )
        ]

    @staticmethod
    def _level_hit(level: Decimal | None, side: Side, bar: Bar, *, is_stop: bool) -> bool:
        """Достигнут ли уровень бара: стоп по неблагоприятной, цель по благоприятной стороне."""
        if level is None:
            return False
        if side is Side.BUY:
            return bar.low <= level if is_stop else bar.high >= level
        return bar.high >= level if is_stop else bar.low <= level

    @staticmethod
    def _exit_side(side: Side) -> Side:
        return Side.SELL if side is Side.BUY else Side.BUY

    def _apply_liquidation(self, bar: Bar) -> None:
        position = self._position
        if not position.is_open:
            return
        adverse = bar.low if position.side is Side.BUY else bar.high
        margin = position.qty * position.entry_price / self.costs.leverage
        unrealised = position.unrealised(adverse)
        if unrealised > -margin:
            return
        fill = Fill(
            ts=bar.ts,
            side=self._exit_side(position.side),
            price=adverse,
            qty=position.qty,
            fee=self.costs.taker_fee * position.qty * adverse,
            reason="stop",
        )
        self._position.apply_fill(fill)
        self._cancel_reduced()
        raise LiquidationError(
            f"margin exhausted: position liquidated at {adverse}, unrealised {unrealised}"
        )

    def _apply_limits(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        rest: list[_Rest] = []
        for parked in self._pending:
            order = parked.order
            qty = self._effective_qty(order)
            if qty is None:
                continue
            fill = self._limit_fill(order, bar, qty)
            if fill is None:
                rest.append(parked)
            else:
                fills.append(fill)
                self._position.apply_fill(fill)
        self._pending = rest
        return fills

    def _apply_markets(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        rest: list[_Rest] = []
        for parked in self._pending:
            order = parked.order
            qty = self._effective_qty(order)
            if qty is None:
                continue
            fill = self._market_fill(order, bar, qty)
            if fill is None:
                rest.append(parked)
            else:
                fills.append(fill)
                self._position.apply_fill(fill)
        self._pending = rest
        return fills

    def _effective_qty(self, order: Order) -> Decimal | None:
        """Размер филла; None — reduce-only заявка при плоской позиции отбрасывается."""
        if not order.reduce_only:
            return order.qty
        if self._position.qty == 0:
            return None
        return min(order.qty, self._position.qty)

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
        price = self.slipped(self._stop_base(bar, order.side, stop), order.side)
        return self._make_fill(bar, order.side, price, self.costs.taker_fee, "stop", qty)

    def _limit_fill(self, order: Order, bar: Bar, qty: Decimal) -> Fill | None:
        if order.kind is not OrderKind.LIMIT or order.limit_price is None:
            return None
        if order.side is Side.BUY and bar.low > order.limit_price:
            return None
        if order.side is Side.SELL and bar.high < order.limit_price:
            return None
        reason = "take_profit" if order.reduce_only and qty == self._position.qty else "limit"
        return self._make_fill(
            bar, order.side, order.limit_price, self.costs.maker_fee, reason, qty
        )

    def _market_fill(self, order: Order, bar: Bar, qty: Decimal) -> Fill | None:
        if order.kind is not OrderKind.MARKET:
            return None
        price = self.slipped(bar.close, order.side)
        return self._make_fill(bar, order.side, price, self.costs.taker_fee, "market", qty)

    def slipped(self, price: Decimal, side: Side) -> Decimal:
        """Уход базовой цены по направлению сделки.

        Публичный: сведение окна в движке исполняет закрывающий филл той же
        моделью, что и маркет-филл.
        """
        if side is Side.BUY:
            return price * (Decimal(1) + self.costs.slippage)
        return price * (Decimal(1) - self.costs.slippage)

    def _make_fill(
        self,
        bar: Bar,
        side: Side,
        price: Decimal,
        fee_rate: Decimal,
        reason: str,
        qty: Decimal,
    ) -> Fill:
        return Fill(
            ts=bar.ts,
            side=side,
            price=price,
            qty=qty,
            fee=fee_rate * qty * price,
            reason=reason,
        )
