from __future__ import annotations

from decimal import Decimal

from strategylab.broker.orders import Fill, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar
from strategylab.core.feed import BarFeed
from strategylab.core.metrics import Metrics
from strategylab.core.trades import TradeLog
from strategylab.strategy.base import Strategy, StrategyState


class Engine:
    """Прогон стратегии по ленте: бар → брокер → стратегия → ордера, сведение в конце окна.

    Порядок внутри бара фиксирован (решение 1): `broker.apply_bar(bar)`, затем
    `strategy.on_bar(bar, state)`, затем `broker.submit(...)` для всех ордеров
    стратегии. Это и есть правило «сигнал действует на следующий бар»: ордер,
    поданный на баре N, исполняется не раньше `apply_bar` бара N+1.

    Реализованный pnl считает движок, а не метрики и не брокер (решение 3):
    снимок позиции до `apply_bar`, снимок после; закрытая часть даёт
    `(exit_price - entry_before) * closed_qty * side_sign - exit_fee - entry_fee_share`.
    Комиссия закрывающего филла относится к закрытой части целиком; входная
    комиссия позиции (`Position.entry_fee`) распределяется пропорционально —
    каждой закрытой единице её доля, как и зеркалу частичного тейка. Так
    комиссия входа не пропадает: с частичными выходами и переворотами она
    расходуется симметрично, и net_pnl становится ниже на её сумму.
    Переворот реализует только закрытую часть, остаток открывает новую
    позицию, во входную комиссию которой идёт его доля закрывающего филла.

    В конце окна движок сводит открытую позицию (решение 4): reduce-only выход
    по цене закрытия последнего бара — с тем же проскальзыванием, что у
    маркет-филла, — и той же записью реализованного pnl: закрывающая
    комиссия плюс остаток непотраченной входной комиссии позиции.
    Сигнал стратегии на последнем баре исполниться не может — бара после него
    нет (решение 5): сведение применяется напрямую, а не через очередь ордеров.

    Журнал сделок (`trade_log`) — необязательный: без него прогон считает
    ровно то же самое, только ничего не запоминает. С журналом движок, кроме
    метрик, пишет время и цены каждой закрытой сделки; на метрики журнал не
    влияет — он только читает то, что движок и так знает.
    """

    def run(
        self,
        feed: BarFeed,
        strategy: Strategy,
        broker: BrokerSimulator,
        metrics: Metrics,
        trade_log: TradeLog | None = None,
    ) -> Metrics:
        bars = list(feed.bars())
        last_index = len(bars) - 1
        for index, bar in enumerate(bars):
            before_side = broker.position.side
            before_qty = broker.position.qty
            before_entry = broker.position.entry_price
            before_entry_fee = broker.position.entry_fee
            fills = broker.apply_bar(bar)
            _record_realized(
                metrics,
                fills,
                before_side,
                before_qty,
                before_entry,
                before_entry_fee,
                trade_log,
            )
            state = StrategyState(
                position=_signed_position(broker),
                equity=metrics.equity,
                bar_index=index,
            )
            for order in strategy.on_bar(bar, state):
                broker.submit(order)
            if index == last_index:
                _close_window(broker, metrics, bar, trade_log)
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
    before_entry_fee: Decimal,
    trade_log: TradeLog | None = None,
) -> None:
    """Реализованный pnl закрытых частей позиции — работа движка, не метрик.

    Снимок позиции до `apply_bar`: если позиция уменьшилась, закрытая часть
    даёт `(exit_price - entry_before) * closed_qty * side_sign
    - exit_fee - entry_fee_share`. Комиссия закрывающего филла относится к
    закрытой части целиком; если филл закрывает позицию и тут же открывает
    новую (переворот), его комиссия делится по объёму филла. Входная комиссия
    позиции распределяется пропорционально: закрытая единица несёт свою долю
    `before_entry_fee * closed / before_qty` — как и зеркало частичного тейка,
    отработав, она больше не остаётся на позиции.

    Журнал сделок (если он передан) получает те же числа: цену входа, pnl и
    филл, которым позиция закрылась. Время входа журнал ведёт сам — движок
    его не знает, вход мог случиться на любом баре раньше.
    """
    if before_qty == 0:
        # Позиции до бара не было: первый филл бара открывает новую.
        if trade_log is not None and fills:
            trade_log.open(fills[0].ts)
        return
    sign = Decimal(1) if before_side is Side.BUY else Decimal(-1)
    remaining = before_qty
    for fill in fills:
        if remaining == 0:
            break
        if fill.side is before_side:
            continue
        closed = min(fill.qty, remaining)
        exit_fee = fill.fee if closed == fill.qty else fill.fee * closed / fill.qty
        entry_fee = before_entry_fee * closed / before_qty
        realized = (fill.price - before_entry) * closed * sign - exit_fee - entry_fee
        # Сторона закрываемой позиции, а не филла: у закрывающего филла она противоположная.
        metrics.record(fill, realized, before_side)
        remaining -= closed
        if trade_log is not None:
            trade_log.close(fill, before_entry, realized, remaining)
            if remaining == 0 and fill.qty > closed:
                # Переворот: остаток филла открывает новую позицию.
                trade_log.open(fill.ts)


def _close_window(
    broker: BrokerSimulator,
    metrics: Metrics,
    bar: Bar,
    trade_log: TradeLog | None = None,
) -> None:
    """Сведение позиции в конце окна: reduce-only выход по цене закрытия бара.

    Сигнал стратегии на последнем баре исполниться не может — бара после него
    нет. Сведение — не стратегический сигнал, а закрытие прогона: движок
    применяет закрывающий филл напрямую и пишет реализованный pnl в метрики.
    Филл исполняется как маркет: цена закрытия уходит на указанную сторону,
    комиссия тейкера считается от фактической цены. В pnl уходит и остаток
    непотраченной входной комиссии позиции: закрытие принимает её целиком.
    """
    position = broker.position
    if not position.is_open:
        return
    exit_side = Side.SELL if position.side is Side.BUY else Side.BUY
    price = broker.slipped(bar.close, exit_side)
    fee = broker.costs.taker_fee * position.qty * price
    fill = Fill(
        ts=bar.ts, side=exit_side, price=price, qty=position.qty, fee=fee, reason="market"
    )
    side_before_close = position.side
    sign = Decimal(1) if position.side is Side.BUY else Decimal(-1)
    # Цену входа захватываем до филла: Position обнуляет entry_price при сведении.
    entry = position.entry_price
    realized = (price - entry) * position.qty * sign - fee - position.entry_fee
    position.apply_fill(fill)
    # В bucket стороны позиции: закрывающий филл короткой позиции — BUY.
    metrics.record(fill, realized, side_before_close)
    if trade_log is not None:
        trade_log.close(fill, entry, realized, Decimal("0"))
