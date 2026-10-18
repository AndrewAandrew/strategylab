from __future__ import annotations

from decimal import Decimal

from strategylab.broker.orders import Fill, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar
from strategylab.core.feed import BarFeed
from strategylab.core.metrics import Metrics
from strategylab.strategy.base import Strategy, StrategyState


class Engine:
    """Прогон стратегии по ленте: бар → брокер → стратегия → ордера, сведение в конце окна.

    Порядок внутри бара фиксирован (решение 1): `broker.apply_bar(bar)`, затем
    `strategy.on_bar(bar, state)`, затем `broker.submit(...)` для всех ордеров
    стратегии. Это и есть правило «сигнал действует на следующий бар»: ордер,
    поданный на баре N, исполняется не раньше `apply_bar` бара N+1.

    Реализованный pnl считает движок, а не метрики и не брокер (решение 3):
    снимок позиции до `apply_bar`, снимок после; закрытая часть даёт
    `(exit_price - entry_before) * closed_qty * side_sign - fees`, комиссии —
    один раз и только по закрывающим филлам. Переворот реализует только
    закрытую часть, остаток открывает новую позицию.

    В конце окна движок сводит открытую позицию (решение 4): reduce-only выход
    по цене закрытия последнего бара, с записью реализованного pnl в метрики.
    Сигнал стратегии на последнем баре исполниться не может — бара после него
    нет (решение 5): сведение применяется напрямую, а не через очередь ордеров.
    """

    def run(
        self,
        feed: BarFeed,
        strategy: Strategy,
        broker: BrokerSimulator,
        metrics: Metrics,
    ) -> Metrics:
        width = feed.interval.value
        bars = list(feed.bars())
        last_index = len(bars) - 1
        for index, bar in enumerate(bars):
            before_side = broker.position.side
            before_qty = broker.position.qty
            before_entry = broker.position.entry_price
            fills = broker.apply_bar(bar)
            _record_realized(metrics, fills, before_side, before_qty, before_entry)
            if broker.position.is_open:
                for _minute in feed.minutes(bar.ts, bar.ts + width):
                    pass
            state = StrategyState(
                position=_signed_position(broker),
                equity=metrics.equity,
                bar_index=index,
            )
            for order in strategy.on_bar(bar, state):
                broker.submit(order)
            if index == last_index:
                _close_window(broker, metrics, bar)
        return metrics


def _signed_position(broker: BrokerSimulator) -> Decimal:
    """Знаковая позиция из брокера: плюс лонг, минус шорт, ноль у плоской."""
    qty = broker.position.qty
    if qty == 0:
        return Decimal("0")
    return qty if broker.position.side is Side.BUY else -qty


def _record_realized(
    metrics: Metrics,
    fills: list[Fill],
    before_side: Side,
    before_qty: Decimal,
    before_entry: Decimal,
) -> None:
    """Реализованный pnl закрытых частей позиции — работа движка, не метрик.

    Снимок позиции до `apply_bar`: если позиция уменьшилась, закрытая часть
    даёт `(exit_price - entry_before) * closed_qty * side_sign - fees`.
    Комиссии учитываются один раз и только по закрывающим филлам. Переворот
    реализует только закрытую часть, остаток открывает новую позицию.
    """
    if before_qty == 0:
        return
    sign = Decimal(1) if before_side is Side.BUY else Decimal(-1)
    remaining = before_qty
    for fill in fills:
        if remaining == 0:
            break
        if fill.side is before_side:
            continue
        closed = min(fill.qty, remaining)
        realized = (fill.price - before_entry) * closed * sign - fill.fee
        metrics.record(fill, realized)
        remaining -= closed


def _close_window(broker: BrokerSimulator, metrics: Metrics, bar: Bar) -> None:
    """Сведение позиции в конце окна: reduce-only выход по цене закрытия бара.

    Сигнал стратегии на последнем баре исполниться не может — бара после него
    нет. Сведение — не стратегический сигнал, а закрытие прогона: движок
    применяет закрывающий филл напрямую и пишет реализованный pnl в метрики.
    """
    position = broker.position
    if not position.is_open:
        return
    exit_side = Side.SELL if position.side is Side.BUY else Side.BUY
    price = bar.close
    fee = broker.costs.taker_fee * position.qty * price
    fill = Fill(
        ts=bar.ts, side=exit_side, price=price, qty=position.qty, fee=fee, reason="market"
    )
    sign = Decimal(1) if position.side is Side.BUY else Decimal(-1)
    realized = (price - position.entry_price) * position.qty * sign - fee
    position.apply_fill(fill)
    metrics.record(fill, realized)
