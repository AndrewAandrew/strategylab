"""SMC CHoCH+FVG (smc_choch_fvg): синтетические 1м-бары, никакой сети.

Лента спроектирована «по барам» (индексы — позиции в ленте, бар t закрывается
на баре t+2 ровно как в замороженном §6):

- trough (3, 100) подтверждён на 5, peak (7, 108) на 9, trough (11, 96) на 13
  → нисходящая структура (96 < 100) с 13-го бара;
- close 111 на баре 17 > 108 → CHoCH long на 17; там же бычий FVG (L17=109 >
  H15=107): mid = (107+109)/2 = 108, SL = min(103,105,109)·0.9995 = 102.9485,
  R = 5.0515;
- S = max(17,17)+2 = 19 → там единственная entry-лимитка, live на 20..79;
- касание на 20 → брекет (SL 102.9485 весь, TP1 = 108+4R = 128.206 половина);
- TP1 на 21 → безубыток по mid=108; повторный сигнал (пара c=20,f=21, S=23)
  при занятой позиции молчит;
- trough (24, 110) подтверждён на 26, close 109 < 110 на 27 → обратный CHoCH
  → MARKET reduce_only, fill на 28 → флэт → полный сброс;
- бары 29-30 держат close над 110 (нет пересвета события), пара (c=27, f=28)
  сигналит шорт ровно на S=30 — после флэта машина перезаряжена.

Числа вывода: qty = 10000·0.01/5.0515 (28 значащих цифр Decimal-деления),
TP1 = 128.206, входной fill = 108 ровно (LIMIT исполняется по limit_price —
maker, без проскальзывания), runner-выход = close 110.2·(1−0.0005).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.strategy.base import StrategyState
from strategylab.strategy.examples import STRATEGY_CLASSES, UnknownStrategy, build
from strategylab.strategy.smc_choch_fvg import Fvg, SmcChochFvgStrategy

START = 1_717_200_000  # граница минуты: 1m-лента
ONE = Decimal("1")
ZERO = Decimal("0")
CAP = Decimal("10000")
# Leverage 10 — как в харнессе раунда (киб к3): маржа entry-лимитки обязана
# проходить гейт `_guard_submit`, иначе тесты падали бы MarginError.
COSTS = CostModel(leverage=Decimal("10"))
SLIPPAGE = COSTS.slippage

_K = Decimal("210")

# (high, low, close) — open строится как close предыдущего бара.
_LONG_SPEC: list[tuple[str, str, str]] = [
    ("110", "109", "110"),  # 0
    ("109", "104", "105"),  # 1
    ("105", "101", "102"),  # 2
    ("102", "100", "101"),  # 3: trough-центр (100)
    ("104", "101.5", "103"),  # 4
    ("106", "102", "105"),  # 5: trough (3,100) подтверждён
    ("107", "104", "106"),  # 6
    ("108", "106", "107"),  # 7: peak-центр (108)
    ("106", "104", "105"),  # 8
    ("104", "102", "103"),  # 9: peak (7,108) подтверждён
    ("103", "100.5", "101"),  # 10
    ("101", "96", "98"),  # 11: trough-центр (96)
    ("99", "97", "98"),  # 12
    ("102", "98", "101"),  # 13: trough (11,96) -> нисходящая структура
    ("105", "101", "104"),  # 14
    ("107", "103", "106"),  # 15: бычий FVG (L103 > H13=102)
    ("108", "105", "107"),  # 16
    ("112", "109", "111"),  # 17: CHoCH (111 > 108) + FVG (109 > 107)
    ("111", "107.5", "108"),  # 18: close == 108 — строгого пробоя нет
    ("110", "107", "108.5"),  # 19: S: entry BUY LIMIT 108
    ("115", "108", "114"),  # 20: касание 108 -> вход + брекет
    ("129", "113", "128"),  # 21: TP1 128.206 -> безубыток 108
    ("120", "113", "118"),  # 22: низкий 113 > 108: стоп цел
    ("119", "112", "117"),  # 23: пара (c=20,f=21) сигналит — позиция занята
    ("118", "110", "115"),  # 24: trough-центр (110)
    ("116", "111", "114"),  # 25
    ("117", "113", "116"),  # 26: trough (24,110) подтверждён
    ("112", "108.6", "109"),  # 27: обратный CHoCH (109 < 110) -> MARKET
    ("110.5", "108.5", "110.2"),  # 28: выход исполнен; флэт; reset
    ("110.4", "108.6", "110.1"),  # 29: close > 110 — событие не пересветен
    ("111", "110", "110.5"),  # 30: S шорт-пары (27,28) -> перезарядка машины
]

MID = Decimal("108")
SL = Decimal("103") * Decimal("0.9995")  # 102.9485 — буфер 0.05% под окном FVG
RISK = MID - SL  # 5.0515
QTY = CAP * Decimal("0.01") / RISK
HALF = QTY * Decimal("0.5")
TP1 = MID + Decimal("4") * RISK  # 128.206

# Зеркало ленты относительно 210: каждый знак неравенства переворачивается,
# поэтому все бычьи события/уровни длинного сценария становятся медвежьими.
_SHORT_SPEC = [
    (str(_K - Decimal(lo)), str(_K - Decimal(h)), str(_K - Decimal(c)))
    for h, lo, c in _LONG_SPEC[:23]
]
S_MID = _K - MID  # 102
S_SL = (_K - Decimal("103")) * Decimal("1.0005")  # 107.0535
S_RISK = S_SL - S_MID  # 5.0535
S_QTY = CAP * Decimal("0.01") / S_RISK
S_TP1 = S_MID - Decimal("4") * S_RISK  # 81.786

_BAND = ("111", "108.5", "110")  # экстремумных кандидатов не рождает (strict)


def _bars(spec: list[tuple[str, str, str]]) -> list[Bar]:
    """Лента 1м-баров: open = close предыдущего (первый бар — open=close)."""
    out: list[Bar] = []
    prev: Decimal | None = None
    for index, (h, lo, c) in enumerate(spec):
        high, low, close = Decimal(h), Decimal(lo), Decimal(c)
        out.append(
            Bar(
                ts=START + 60 * index,
                open=close if prev is None else prev,
                high=high,
                low=low,
                close=close,
                volume=ONE,
            )
        )
        prev = close
    return out


class ListFeed:
    """Лента из списка баров (стиль test_engine.py)."""

    def __init__(self, bars: list[Bar]) -> None:
        self.interval = Interval.MINUTE
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class FillBroker(BrokerSimulator):
    """Брокер, запоминающий филлы apply_bar с индексом бара."""

    def __init__(self, costs: CostModel, intrabar: IntrabarOrder) -> None:
        super().__init__(costs, intrabar)
        self.log: list[tuple[int, Fill]] = []

    def apply_bar(self, bar: Bar) -> list[Fill]:
        fills = super().apply_bar(bar)
        for fill in fills:
            self.log.append(((bar.ts - START) // 60, fill))
        return fills


class Observer:
    """Обёртка: снимает заявки, зеркала стопа, очередь и флаг сделки по барам."""

    name = "smc-observer"

    def __init__(self, inner: SmcChochFvgStrategy, broker: BrokerSimulator) -> None:
        self.inner = inner
        self.broker = broker
        self.orders: dict[int, list[Order]] = {}
        self.stop_mirror: dict[int, Decimal | None] = {}
        self.pending: dict[int, tuple[Order, ...]] = {}
        self.in_trade: dict[int, bool] = {}

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        index = state.bar_index
        self.stop_mirror[index] = self.broker.position.stop_price
        self.pending[index] = tuple(self.broker.pending_orders)
        orders = self.inner.on_bar(bar, state)
        self.orders[index] = orders
        self.in_trade[index] = self.inner.in_trade
        return orders


def _run(
    spec: list[tuple[str, str, str]], strategy: SmcChochFvgStrategy | None = None
) -> tuple[SmcChochFvgStrategy, FillBroker, Observer]:
    strat = strategy if strategy is not None else SmcChochFvgStrategy()
    broker = FillBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    observer = Observer(strat, broker)
    Engine().run(ListFeed(_bars(spec)), observer, broker, Metrics(equity=CAP))
    return strat, broker, observer


def _feed_flat(
    strategy: SmcChochFvgStrategy,
    bars: list[Bar],
    equity: Decimal = CAP,
    start: int = 0,
) -> list[list[Order]]:
    """Юнит-прогон по флэтному состоянию: только сигнальный слой.

    `start` — абсолютный bar_index первого бара (ленты режутся слайсами,
    а номера баров в стратегии считаются от начала ленты).
    """
    out: list[list[Order]] = []
    for offset, bar in enumerate(bars):
        index = start + offset
        out.append(strategy.on_bar(bar, StrategyState(ZERO, equity, index)))
    return out


def _substitute(
    spec: list[tuple[str, str, str]], index: int, bar: tuple[str, str, str]
) -> list[tuple[str, str, str]]:
    copied = list(spec)
    copied[index] = bar
    return copied


# --- структурный слой: подтверждение, CHoCH, FVG ----------------------------


def test_extreme_confirmed_exactly_at_t_plus_2() -> None:
    """До t+2 экстремум — кандидат, не подтверждённый; на t+2 — подтверждён.

    Кандидат trough(3,100) заведён на баре 3, подтверждён закрытием бара 5.
    Пик — симметрично: кандидат (7, 108) на баре 7, подтверждён на 9. До 9
    пик недоступен ни для какого правила.
    """
    strategy = SmcChochFvgStrategy()
    _feed_flat(strategy, _bars(_LONG_SPEC[:5]))
    assert [(c.kind, c.bar, c.price) for c in strategy.candidates] == [
        ("trough", 3, Decimal("100"))
    ]
    assert strategy.confirmed_extremes == ()  # на баре 4 центр 3 — только кандидат
    _feed_flat(strategy, _bars(_LONG_SPEC[5:6]), start=5)
    assert [(e.kind, e.bar, e.price) for e in strategy.confirmed_extremes] == [
        ("trough", 3, Decimal("100"))
    ]
    assert all(not (c.kind == "trough" and c.bar == 3) for c in strategy.candidates)
    strategy2 = SmcChochFvgStrategy()
    _feed_flat(strategy2, _bars(_LONG_SPEC[:9]))
    confirmed2 = [(e.kind, e.bar) for e in strategy2.confirmed_extremes]
    assert ("peak", 7) not in confirmed2  # на баре 8 — правое крыло не закрыто
    assert ("trough", 3) in confirmed2
    _feed_flat(strategy2, _bars(_LONG_SPEC[9:10]), start=9)
    confirmed3 = [(e.kind, e.bar) for e in strategy2.confirmed_extremes]
    assert ("peak", 7) in confirmed3  # ровно на t+2 = 9


def test_no_entry_before_signal_bar_and_structure_needs_confirmed() -> None:
    """До S=19 ни одного ордера; нисходящая структура — только с 13-го бара.

    Закрепляет «НЕТ входа на преждевременном сигнале»: naive-читатель,
    использующий неподтверждённые пики, дал бы CHoCH/вход раньше; машина
    держит все ссылки на экстремумы только по подтверждённым.
    """
    strategy = SmcChochFvgStrategy()
    orders = _feed_flat(strategy, _bars(_LONG_SPEC[:19]))
    assert all(bar_orders == [] for bar_orders in orders)
    # флаг структуры обновляется только на закрытии t+2 подтверждённого trough:
    fresh = SmcChochFvgStrategy()
    flags: list[bool] = []
    for index, bar in enumerate(_bars(_LONG_SPEC[:18])):
        fresh.on_bar(bar, StrategyState(ZERO, CAP, index))
        flags.append(fresh.descending)
    assert flags[12] is False  # trough(11,96) ещё кандидат
    assert flags[13] is True  # подтверждён на закрытии 13 = 11+2
    assert flags[17] is True and fresh.bull_choch_bar == 17  # событие, не вход


def test_choch_requires_strict_break_of_last_confirmed_peak() -> None:
    """CHoCH long = close строго > последнего подтверждённого peak (108).

    Вариант ленты с close 108 на баре 17 (ровно пик, не пробой) не сигналит
    никогда: события нет — пары нет.
    """
    strategy = SmcChochFvgStrategy()
    orders = _feed_flat(strategy, _bars(_LONG_SPEC[:19]))
    assert strategy.bull_choch_bar == 17  # событие живёт до S
    assert all(bar_orders == [] for bar_orders in orders)
    entry_bar = _feed_flat(strategy, _bars(_LONG_SPEC[19:20]), start=19)
    assert len(entry_bar[0]) == 1  # entry на S=19: пара израсходована

    quiet = SmcChochFvgStrategy()
    orders_q = _feed_flat(
        quiet, _bars(_substitute(_LONG_SPEC, 17, ("112", "107.5", "108"))[:20])
    )
    assert quiet.bull_choch_bar is None  # close 108 == peak 108: строгого пробоя нет
    assert all(bar_orders == [] for bar_orders in orders_q)


def test_fvg_strict_and_freshness_needs_fvg_at_or_after_choch() -> None:
    """FVG — строгое L(t) > H(t−2); свежесть — аменд §6 от 2026-11-01.

    Вариант с L17 = 107 = H15: FVG на 17 нет, остаётся старый FVG(15) — он
    раньше CHoCH(17) и по клаузе аменда (f.bar >= c.bar: бар FVG не раньше
    бара CHoCH) не образует пару, входа нет ни на 19, ни дальше.
    """
    baseline = SmcChochFvgStrategy()
    _feed_flat(baseline, _bars(_LONG_SPEC[:18]))
    assert baseline.bull_fvg is not None and baseline.bull_fvg.bar == 17

    stale = SmcChochFvgStrategy()
    orders = _feed_flat(
        stale, _bars(_substitute(_LONG_SPEC, 17, ("112", "107", "111")))[:20]
    )
    assert stale.bull_choch_bar == 17
    fvg = stale.bull_fvg
    assert fvg is not None and fvg.bar == 15  # пересечения строгого нет — старый
    assert all(bar_orders == [] for bar_orders in orders)


def test_entry_limit_mid_price_qty_ttl() -> None:
    """На S лимитка: BUY LIMIT mid=(H(t−2)+L(t))/2, qty=equity·risk_frac/R, TIF 60."""
    _, _, observer = _run(_LONG_SPEC[:21])
    entry_orders = observer.orders[19]
    assert len(entry_orders) == 1
    entry = entry_orders[0]
    assert entry.side is Side.BUY
    assert entry.kind is OrderKind.LIMIT
    assert entry.reduce_only is False
    assert entry.expires_in_bars == 60
    assert entry.limit_price == MID == (Decimal("107") + Decimal("109")) / Decimal("2")
    assert entry.qty == QTY
    assert observer.orders[18] == [] and observer.orders[20] != entry_orders


# --- торговый слой: брекет, TP1/BE, runner-выходы ----------------------------


def test_bracket_placed_on_fill_bar_with_frozen_stop() -> None:
    """Вход исполнили на баре 20: там же SL (замороженный с сигнала) и TP1=4R/2."""
    _, _, observer = _run(_LONG_SPEC[:22])
    bracket = observer.orders[20]
    assert len(bracket) == 2
    stop, target = bracket
    assert stop.side is Side.SELL and stop.kind is OrderKind.STOP_MARKET
    assert stop.reduce_only is True
    assert stop.qty == QTY and stop.stop_price == SL
    assert target.side is Side.SELL and target.kind is OrderKind.LIMIT
    assert target.reduce_only is True
    assert target.qty == HALF and target.limit_price == TP1


def test_tp1_moves_stop_to_breakeven_and_resting_semantics() -> None:
    """TP1 (21) -> безубыток по entry_est=mid; семантика покоящихся стопов sim.py.

    Проверяем фактическое поведение ядра: второй STOP_MARKET reduce_only
    ПЕРЕЗАПИСЫВАЕТ зеркало стопа позиции (`_mirror_reduced`), первая
    resting-заявка остаётся в очереди мёртвым грузом (`_apply_stops`
    пропускает reduce_only) и будет снята при флэте; resting-лимит TP1
    после частичного исполнения ядро снимает сам (`_drop_resting_target`).
    """
    _, broker, observer = _run(_LONG_SPEC[:23])
    be = observer.orders[21]
    assert len(be) == 1
    assert be[0].side is Side.SELL and be[0].kind is OrderKind.STOP_MARKET
    assert be[0].reduce_only is True
    assert be[0].qty == HALF and be[0].stop_price == MID
    assert observer.stop_mirror[22] == MID  # зеркало перезаписано: BE, не SL
    resting_stops = [
        order
        for order in observer.pending[22]
        if order.kind is OrderKind.STOP_MARKET and order.reduce_only
    ]
    assert len(resting_stops) == 2  # старый SL + новый BE, оба покоятся
    resting_targets = [
        order
        for order in observer.pending[22]
        if order.kind is OrderKind.LIMIT and order.reduce_only
    ]
    assert resting_targets == []  # TP1-лимит снят ядром после частичного филла
    reasons = [fill.reason for _, fill in broker.log]
    assert reasons == ["limit", "take_profit"]  # лишнего выхода не было


def test_reverse_choch_exits_runner_and_market_fill_next_bar() -> None:
    """Обратный CHoCH на 27: MARKET reduce_only остатка, fill на 28, флэт-ресет."""
    _, broker, observer = _run(_LONG_SPEC)
    exits = observer.orders[27]
    assert len(exits) == 1
    exit_order = exits[0]
    assert exit_order.side is Side.SELL and exit_order.kind is OrderKind.MARKET
    assert exit_order.reduce_only is True and exit_order.qty == HALF
    market_fills = [
        (index, fill) for index, fill in broker.log if fill.reason == "market"
    ]
    assert len(market_fills) == 1
    index, fill = market_fills[0]
    assert index == 28
    assert fill.price == Decimal("110.2") * (ONE - SLIPPAGE)
    assert observer.in_trade[29] is False  # позиция флэт -> состояние сброшено


def test_hard_cap_exits_even_without_tp1() -> None:
    """hard-cap max_hold=240 баров от бара входа: выход и до TP1 (решение 7)."""
    spec = _LONG_SPEC[:21] + [_BAND] * 241  # bars 21..261
    _, broker, observer = _run(spec)
    assert observer.orders[259] == []
    cap_exit = observer.orders[260]
    assert len(cap_exit) == 1
    assert cap_exit[0].kind is OrderKind.MARKET and cap_exit[0].reduce_only is True
    assert cap_exit[0].qty == QTY  # TP1 не было: остаток = вся позиция
    market_fills = [(index, fill) for index, fill in broker.log if fill.reason == "market"]
    assert len(market_fills) == 1
    assert market_fills[0][0] == 261
    assert market_fills[0][1].price == Decimal("110") * (ONE - SLIPPAGE)


def test_second_signal_blocked_while_position_open() -> None:
    """Пара (c=20, f=21) сигналит ровно на S=23 при занятой позиции — тишина."""
    _, _, observer = _run(_LONG_SPEC[:25])
    assert observer.in_trade[22] is True
    assert observer.orders[22] == []
    assert observer.orders[23] == []  # одна лимитка на сделку
    assert observer.orders[24] == []


# --- лимитка: TIF, сайзинг, зеркало шорта, сброс ------------------------------


def test_ttl_expiry_silently_releases_entry() -> None:
    """Не касались mid 60 живых баров — заявка мертва на входе S+61, входа нет."""
    spec = _LONG_SPEC[:20] + [_BAND] * 60 + [("111", "107", "108")]
    _, broker, observer = _run(spec)
    assert broker.log == []  # entry не исполнилась; списание молчаливое (§6)
    assert observer.in_trade[79] is True  # ещё жива на последнем живом баре
    assert observer.in_trade[80] is False  # S+61: pending сброшен, окно открыто
    assert observer.pending[80] == ()  # ядро списало лимитку до касания


def test_ttl_boundary_fills_on_last_live_bar() -> None:
    """Граница TIF: касание на S+60 (bar 79) исполнимо, на S+61 — нет."""
    spec = _LONG_SPEC[:20] + [_BAND] * 59 + [("111", "108", "109")]
    _, broker, observer = _run(spec)
    assert len(broker.log) == 1
    assert broker.log[0][0] == 79
    assert broker.log[0][1].price == MID
    assert broker.log[0][1].fee == COSTS.maker_fee * QTY * MID
    assert observer.orders[79][0].kind is OrderKind.STOP_MARKET


def test_short_mirror_levels() -> None:
    """Зеркало: SELL LIMIT mid=(L(t−2)+H(t))/2, SL сверху, TP1 вниз, BE BUY-стоп."""
    _, broker, observer = _run(_SHORT_SPEC)
    entry = observer.orders[19]
    assert len(entry) == 1
    assert entry[0].side is Side.SELL and entry[0].kind is OrderKind.LIMIT
    assert entry[0].limit_price == S_MID
    assert entry[0].qty == S_QTY and entry[0].expires_in_bars == 60
    bracket = observer.orders[20]
    assert len(bracket) == 2
    stop, target = bracket
    assert stop.side is Side.BUY and stop.kind is OrderKind.STOP_MARKET
    assert stop.stop_price == S_SL and stop.qty == S_QTY and stop.reduce_only is True
    assert target.side is Side.BUY and target.kind is OrderKind.LIMIT
    assert target.limit_price == S_TP1 and target.qty == S_QTY * Decimal("0.5")
    be = observer.orders[21]
    assert len(be) == 1 and be[0].side is Side.BUY
    assert be[0].kind is OrderKind.STOP_MARKET and be[0].stop_price == S_MID
    reasons = [fill.reason for _, fill in broker.log]
    assert reasons == ["limit", "take_profit"]


def test_sizing_uses_equity_and_risk_frac() -> None:
    """qty = state.equity·risk_frac/R; equity=0 — сигнала нет; risk_frac — параметр."""
    doubled = SmcChochFvgStrategy(risk_frac=Decimal("0.02"))
    orders_d = _feed_flat(doubled, _bars(_LONG_SPEC[:20]))
    assert orders_d[19][0].qty == CAP * Decimal("0.02") / RISK  # та же формула ядра

    broke = _feed_flat(SmcChochFvgStrategy(), _bars(_LONG_SPEC[:20]), equity=ZERO)
    assert broke[19] == []


def test_flat_resets_state_and_rearms_machine() -> None:
    """Флэт обнуляет торговое состояние: новая пара сигналит шорт ровно на S=30."""
    _, _, observer = _run(_LONG_SPEC[:31])
    assert observer.in_trade[28] is False
    assert observer.in_trade[29] is False
    assert observer.orders[29] == []
    short_entry = observer.orders[30]
    assert len(short_entry) == 1
    order = short_entry[0]
    assert order.side is Side.SELL and order.kind is OrderKind.LIMIT
    assert order.limit_price == (Decimal("113") + Decimal("110.5")) / Decimal("2")
    assert order.stop_price is None and order.reduce_only is False
    assert order.expires_in_bars == 60
    # FVG-заморозки шорт-пары: SL = max(H(26..28))·1.0005 = 117·1.0005.
    reloaded = SmcChochFvgStrategy()
    # тот же mid/SL пересчитаны машиной при появлении FVG(28):
    _feed_flat(reloaded, _bars(_LONG_SPEC[:29]))
    assert reloaded.bear_fvg == Fvg(
        bar=28,
        mid=Decimal("111.75"),
        sl=Decimal("117") * Decimal("1.0005"),
        risk=Decimal("117") * Decimal("1.0005") - Decimal("111.75"),
    )


def test_no_lookahead_prefix_slice() -> None:
    """Контрольный срез: решения на барах < 20 не зависят от хвоста ленты.

    Полная лента, обрезок 0..19 и обрезок + чужой хвост из 11 баров дают
    идентичные подачи на 0..19 — ни одно решение не использует будущие бары.
    """
    _, _, full = _run(_LONG_SPEC)
    _, _, cut = _run(_LONG_SPEC[:20])
    _, _, tail = _run(_LONG_SPEC[:20] + [_BAND] * 11)
    key = lambda order: (  # noqa: E731
        str(order.side),
        str(order.kind),
        order.qty,
        order.limit_price,
        order.stop_price,
        order.reduce_only,
        order.expires_in_bars,
    )
    for index in range(20):
        assert [key(o) for o in full.orders[index]] == [key(o) for o in cut.orders[index]]
        assert [key(o) for o in full.orders[index]] == [key(o) for o in tail.orders[index]]
    assert len(full.orders[19]) == 1  # срез содержит сам сигнал S=19


def test_registry_params_and_validation() -> None:
    """Реестр, from_params, валидация целых ttl/max_hold и risk_frac <= 1."""
    assert SmcChochFvgStrategy.name == "smc_choch_fvg"
    assert STRATEGY_CLASSES["smc_choch_fvg"] is SmcChochFvgStrategy
    built = build(
        "smc_choch_fvg",
        {
            "ttl_bars": Decimal("3"),
            "risk_frac": Decimal("0.01"),
            "tp_mult": Decimal("4"),
            "max_hold": Decimal("240"),
        },
    )
    assert isinstance(built, SmcChochFvgStrategy)
    with pytest.raises(UnknownStrategy):
        build("smc_choch_fvg_typo", {})
    with pytest.raises(ValueError):
        SmcChochFvgStrategy.from_params({"unknown": ONE})
    for bad_ttl in (ZERO, Decimal("-1"), Decimal("2.5")):
        with pytest.raises(ValueError):
            SmcChochFvgStrategy(ttl_bars=bad_ttl)
    for bad_hold in (ZERO, Decimal("240.5")):
        with pytest.raises(ValueError):
            SmcChochFvgStrategy(max_hold=bad_hold)
    for bad_frac in (ZERO, Decimal("-0.1"), Decimal("1.5")):
        with pytest.raises(ValueError):
            SmcChochFvgStrategy(risk_frac=bad_frac)
    for bad_tp in (ZERO, Decimal("-2")):
        with pytest.raises(ValueError):
            SmcChochFvgStrategy(tp_mult=bad_tp)
    assert SmcChochFvgStrategy().bull_fvg is None
