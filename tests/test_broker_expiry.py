"""Тесты TIF-списания лимиток (`expires_in_bars`) — фича feat/limit-order-expiry.

Семантика (design §7, план Task 4): ордер подан при внутреннем счётчике seq=S →
участвует в apply_bar S+1 … S+e → на входе первого apply_bar с seq > S+e молча
списывается без Fill. `expires_in_bars=None` — неограниченно (прежнее поведение).
Существующие брокерские тесты (`test_broker_sim.py`) этим файлом не трогаются.
"""

from decimal import Decimal

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar

LIMIT = Decimal("100")
QTY = Decimal("1")


def _miss(ts: int) -> Bar:
    """Бар, не касающийся лимита 100 со стороны BUY (low выше лимита)."""
    return Bar(ts=ts, open=Decimal("102"), high=Decimal("105"), low=Decimal("101"),
               close=Decimal("103"), volume=Decimal("1000"))


def _touch(ts: int) -> Bar:
    """Бар, касающийся лимита 100: low <= limit → филл по цене лимита maker-fee."""
    return Bar(ts=ts, open=Decimal("101"), high=Decimal("102"), low=Decimal("99"),
               close=Decimal("100"), volume=Decimal("1000"))


def _broker() -> BrokerSimulator:
    return BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)


def _limit_buy(ttl: int | None) -> Order:
    return Order(Side.BUY, OrderKind.LIMIT, QTY, LIMIT, None, False, ttl)


def test_limit_expires_after_ttl_without_fill() -> None:
    b = _broker()
    order = _limit_buy(2)
    b.submit(order)
    assert b.apply_bar(_miss(1)) == []  # S+1: живой, касания нет
    assert b.pending_orders == [order]
    assert b.apply_bar(_miss(2)) == []  # S+2: последний исполнимый бар
    assert b.pending_orders == [order]
    assert b.apply_bar(_miss(3)) == []  # S+3: списан на входе, без Fill
    assert b.pending_orders == []


def test_limit_fills_before_expiry_maker() -> None:
    b = _broker()
    b.submit(_limit_buy(2))
    fills = b.apply_bar(_touch(1))
    assert len(fills) == 1
    assert fills[0].price == LIMIT
    assert fills[0].fee == CostModel().maker_fee * QTY * LIMIT
    assert fills[0].reason == "limit"
    assert b.pending_orders == []


def test_expiry_none_rests_forever() -> None:
    b = _broker()
    order = _limit_buy(None)
    b.submit(order)
    for index in range(50):
        assert b.apply_bar(_miss(index)) == []
    assert b.pending_orders == [order]


def test_expired_limit_does_not_fill_late() -> None:
    b = _broker()
    order = _limit_buy(2)
    b.submit(order)
    b.apply_bar(_miss(1))
    b.apply_bar(_miss(2))
    # S+e+1: бар касался бы лимита, но ордер списан на входе apply_bar.
    assert b.apply_bar(_touch(3)) == []
    assert b.pending_orders == []
    assert b.position.is_open is False


def test_position_untouched_by_expiry() -> None:
    b = _broker()
    b.submit(_limit_buy(None))  # без TIF: исполнится на первом касании
    assert len(b.apply_bar(_touch(1))) == 1
    before = b.position
    assert before.qty == QTY and before.entry_price == LIMIT
    b.submit(_limit_buy(2))  # вторая лимитка та же цена, но с TTL
    assert b.apply_bar(_miss(2)) == []
    assert b.apply_bar(_miss(3)) == []
    assert b.apply_bar(_touch(4)) == []  # списана до касания: без нового филла
    after = b.position
    assert after.qty == before.qty
    assert after.entry_price == before.entry_price
    assert after.side is before.side


def test_legacy_positional_construction() -> None:
    """6 позиционных аргументов валидны, седьмое поле по умолчанию None."""
    order = Order(Side.BUY, OrderKind.LIMIT, QTY, LIMIT, None, False)
    assert order.expires_in_bars is None
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)  # позиционный контракт прежний
    b.submit(order)
    assert b.apply_bar(_miss(1)) == []
    assert b.pending_orders == [order]


def test_born_survives_mirror_ops() -> None:
    """Частичный тейк (_drop_resting_target/_cancel_reduced) не двигает born TIF-лимиток.

    Иначе пересборка _Rest на этих сайтах молча продлевает срок жизни отдыхающим
    заявкам: watch-лимитка пережила бы свой TTL и исполчилась на позднем касании.
    """
    b = _broker()
    entry = Order(Side.BUY, OrderKind.LIMIT, Decimal("2"), LIMIT, None, False)
    watch = Order(Side.BUY, OrderKind.LIMIT, QTY, Decimal("95"), None, False, 2)  # ttl=2, born=0
    b.submit(entry)
    b.submit(watch)
    bar_a = Bar(ts=1, open=Decimal("101"), high=Decimal("105"), low=Decimal("98"),
                close=Decimal("100"), volume=Decimal("1000"))  # entry filled @100, watch: 98>95
    fills = b.apply_bar(bar_a)
    assert [(f.price, f.reason) for f in fills] == [(LIMIT, "limit")]
    tp = Order(Side.SELL, OrderKind.LIMIT, QTY, Decimal("110"), None, True)
    b.submit(tp)  # частичный тейк qty1 (mirror на позиции 2)
    bar_b = Bar(ts=2, open=Decimal("105"), high=Decimal("110"), low=Decimal("96"),
                close=Decimal("108"), volume=Decimal("1000"))  # TP hit, watch 96>95 rests
    fills = b.apply_bar(bar_b)
    assert [(f.price, f.reason, f.qty) for f in fills] == [(Decimal("110"), "take_profit", QTY)]
    assert b.pending_orders == [watch]  # watch пережил mirror-операции баром
    bar_c = Bar(ts=3, open=Decimal("97"), high=Decimal("99"), low=Decimal("95"),
                close=Decimal("96"), volume=Decimal("1000"))  # seq=3 > born+2: списана на входе
    assert b.apply_bar(bar_c) == []  # касание 95 — ПОЗДНО: watch мертва
    assert b.pending_orders == []
    assert b.position.qty == QTY and b.position.entry_price == LIMIT  # лонг 1 не тронут
