"""Лимит-пуллбек: волны maker-лимиток с TIF (design §7, решение владельца A3).

Юнит-тесты дёргают `on_bar` на синтетических состояниях; интеграционные гоняют
настоящий `Engine` + `BrokerSimulator` на короткой ленте (стиль `test_engine.py`).
Ключевой инвариант A3: на границе волны старая лимитка ещё исполнима (новая
подаётся после обработки бара), на следующем баре старая уже мертва — двойного
entry-филла не существует.
"""

from collections.abc import Iterator
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.strategy.base import Strategy, StrategyState
from strategylab.strategy.examples import STRATEGY_CLASSES, build
from strategylab.strategy.limit_pullback import LimitPullbackStrategy

COSTS = CostModel()
DEPTH = Decimal("0.01")  # лимит от close 100 → ровно 99
LIMIT_PRICE = Decimal("99")
ONE = Decimal("1")
# Капитал метрик для движковых прогонов: гейт подачи (фикс B/E) не пропускает
# entry-лимитки при нулевом equity. Совпадает с equity в `_state` ниже.
CAPITAL = Decimal("10000")


def _bar(ts: int, close: str = "100", low: str | None = None) -> Bar:
    price = Decimal(close)
    return Bar(
        ts=ts,
        open=price,
        high=price,
        low=price if low is None else Decimal(low),
        close=price,
        volume=ONE,
    )


def _bars(specs: list[tuple[str, str]]) -> list[Bar]:
    """Список (close, low) — лента синтетических баров."""
    return [_bar(1000 + index, close, low) for index, (close, low) in enumerate(specs)]


def _flat(lows: list[str]) -> list[Bar]:
    return _bars([("100", low) for low in lows])


def _state(position: str, index: int) -> StrategyState:
    return StrategyState(
        position=Decimal(position), equity=Decimal("10000"), bar_index=index
    )


def _strategy(ttl: str = "2", hold: str = "5") -> LimitPullbackStrategy:
    return LimitPullbackStrategy(DEPTH, Decimal(ttl), Decimal(hold))


class ListFeed:
    """Лента из списка баров (стиль test_engine.py)."""

    def __init__(self, bars: list[Bar]) -> None:
        self.interval = Interval.MINUTE
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class FillRecordingBroker(BrokerSimulator):
    """Брокер, запоминающий филлы, применённые через apply_bar."""

    def __init__(self, costs: CostModel, intrabar: IntrabarOrder) -> None:
        super().__init__(costs, intrabar)
        self.fills: list[Fill] = []

    def apply_bar(self, bar: Bar) -> list[Fill]:
        fills = super().apply_bar(bar)
        self.fills.extend(fills)
        return fills


class RecordingStrategy:
    """Обёртка: до диспетча внутрь фиксирует цены лимиток в очереди брокера.

    Момент записи — после `apply_bar` текущего бара и до подач этого бара:
    очередь = выжившие после списаний и филлов. Цены различают волну с новой
    границы со старым поколением (у идентичных лимиток длина очереди молчит).
    """

    name = "recording"

    def __init__(self, inner: LimitPullbackStrategy, broker: BrokerSimulator) -> None:
        self._inner = inner
        self._broker = broker
        self.resting_limits: dict[int, tuple[Decimal, ...]] = {}

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        self.resting_limits[state.bar_index] = tuple(
            order.limit_price for order in self._broker.pending_orders
        )
        return self._inner.on_bar(bar, state)


def _run(
    bars: list[Bar], strategy: Strategy
) -> tuple[FillRecordingBroker, Metrics]:
    broker = FillRecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    metrics = Metrics(equity=CAPITAL)
    Engine().run(ListFeed(bars), strategy, broker, metrics)
    return broker, metrics


def _run_recording(
    bars: list[Bar], strategy: LimitPullbackStrategy
) -> tuple[FillRecordingBroker, RecordingStrategy]:
    broker = FillRecordingBroker(COSTS, IntrabarOrder.PESSIMISTIC)
    recorder = RecordingStrategy(strategy, broker)
    Engine().run(ListFeed(bars), recorder, broker, Metrics(equity=CAPITAL))
    return broker, recorder


def _entry_fills(fills: list[Fill]) -> list[Fill]:
    return [f for f in fills if f.reason == "limit"]


def test_wave_entry_limit_maker_fill() -> None:
    """Касание low на S+1: entry-филл ровно по цене лимита с maker-комиссией."""
    bars = _flat(["100", "99", "100", "100"])  # волна на b0, касание на b1
    broker, metrics = _run(bars, _strategy(ttl="2", hold="5"))
    entry = _entry_fills(broker.fills)
    assert len(entry) == 1
    fill = entry[0]
    assert fill.price == LIMIT_PRICE
    assert fill.qty == ONE
    assert fill.fee == COSTS.maker_fee * ONE * LIMIT_PRICE
    assert fill.ts == bars[1].ts
    # Сведение окна — прямой филл движка, не через apply_bar: в записи его нет.
    assert len(broker.fills) == 1
    assert metrics.trades == 1


def test_ttl_expiry_cancels_entry() -> None:
    """Старая лимитка списана молча (без Fill): на баре после границы в очереди
    только новое поколение, притом по цене лимита нового бара."""
    # b0 close100 → лимит 99 (исполним на b1,b2). b2 — граница волны: close 200,
    # касания нет (low 200), новая лимитка 198 подаётся после обработки b2.
    # b3 (low 200 > 198, тоже без касания): старая обязана быть списана на входе
    # apply_bar — в очереди только 198, ни одного филла.
    bars = _bars([("100", "100"), ("100", "100"), ("200", "200"), ("200", "200")])
    broker, recorder = _run_recording(bars, _strategy(ttl="2", hold="5"))
    expected = {0: (), 1: (LIMIT_PRICE,), 2: (LIMIT_PRICE,), 3: (Decimal("198"),)}
    assert recorder.resting_limits == expected
    assert broker.fills == []  # ни одного исполнения — списание молчаливое
    assert not broker.position.is_open


def test_no_new_limits_while_position_open() -> None:
    """Лонг жив (hold велик): за 8 баров ни одного ордера — ни BUY, ни выхода."""
    strategy = _strategy(ttl="2", hold="10")
    orders: list[Order] = []
    for index in range(8):
        orders += strategy.on_bar(_bar(1000 + index), _state("1", index))
    assert orders == []


def test_exit_market_reduce_only_after_hold() -> None:
    """Через hold баров от входа — один MARKET SELL reduce_only; возврат во флэт
    сбрасывает флаг, и следующая волна снова ставит BUY."""
    strategy = _strategy(ttl="2", hold="3")
    orders: list[Order] = []
    for index in range(6):
        position = "1" if index < 4 else "0"
        orders += strategy.on_bar(_bar(1000 + index), _state(position, index))
    sells = [o for o in orders if o.side is Side.SELL]
    assert len(sells) == 1
    exit_order = sells[0]
    assert exit_order.kind is OrderKind.MARKET
    assert exit_order.reduce_only is True
    assert exit_order.qty == ONE
    assert exit_order.expires_in_bars is None
    # Вход засчитан с b0 (первый ненулевой observed), exit подан на b3 (3-0>=3).
    # Возврат во флэт на b4 (4%2==0) — ровно одна новая волна:
    buys = [o for o in orders if o.side is Side.BUY]
    assert len(buys) == 1
    assert buys[0].kind is OrderKind.LIMIT and buys[0].expires_in_bars == 2


def test_only_one_fillable_entry_limit() -> None:
    """Инвариант A3: касание на границе волны и следом — ровно один entry-филл.
    Старая лимитка исполнима на баре границы (новая подана после обработки бара),
    на S+e+1 уже мертва — двойного филла не существует."""
    # A: касание ровно на границе S+e (b2): исполняется старая, волна не ставится.
    broker_a, _ = _run(_flat(["100", "100", "99"]), _strategy(ttl="2", hold="5"))
    assert len(_entry_fills(broker_a.fills)) == 1
    # B: касание на границе И на следующем баре: старая закрывает позицию, волна
    # не ставится, к b3 в очереди нет ни одного BUY — второго entry-филла нет.
    broker_b, _ = _run(_flat(["100", "100", "99", "99"]), _strategy(ttl="2", hold="5"))
    assert len(_entry_fills(broker_b.fills)) == 1
    # C: касание только на S+e+1 (b3): старая списана на входе, новая (с границы
    # b2) исполняется — ровно один entry-филл, очередь на b3 после филла пуста.
    bars_c = _flat(["100", "100", "100", "99"])
    broker_c, recorder_c = _run_recording(bars_c, _strategy(ttl="2", hold="5"))
    assert recorder_c.resting_limits == {0: (), 1: (LIMIT_PRICE,), 2: (LIMIT_PRICE,), 3: ()}
    assert len(_entry_fills(broker_c.fills)) == 1
    assert _entry_fills(broker_c.fills)[0].ts == bars_c[3].ts


def test_params_validation() -> None:
    """Границы параметров + регистрация в реестре и build (R4: явная ветка)."""
    for bad_depth in (Decimal("0"), Decimal("-0.1"), Decimal("1"), Decimal("1.5")):
        with pytest.raises(ValueError):
            LimitPullbackStrategy(bad_depth, Decimal("2"), Decimal("3"))
    for bad_ttl in (Decimal("0"), Decimal("-1")):
        with pytest.raises(ValueError):
            LimitPullbackStrategy(DEPTH, bad_ttl, Decimal("3"))
    for bad_hold in (Decimal("0"), Decimal("-1")):
        with pytest.raises(ValueError):
            LimitPullbackStrategy(DEPTH, Decimal("2"), bad_hold)

    # Дробный ttl → int() в стиле SmaCross: 2.7 живёт как 2 (волны каждые 2 бара).
    strategy = LimitPullbackStrategy(DEPTH, Decimal("2.7"), Decimal("3"))
    wave_orders = [
        order
        for index in (1, 2)
        for order in strategy.on_bar(_bar(1000 + index), _state("0", index))
    ]
    assert len(wave_orders) == 1  # только b2: 2 % int(2.7) == 0, 1 % int(2.7) != 0
    assert wave_orders[0].expires_in_bars == 2

    assert LimitPullbackStrategy.name == "limit_pullback"
    assert STRATEGY_CLASSES["limit_pullback"] is LimitPullbackStrategy
    built = build(
        "limit_pullback",
        {"depth": DEPTH, "ttl_bars": Decimal("2"), "hold_bars": Decimal("5")},
    )
    assert isinstance(built, LimitPullbackStrategy)
    unknown_params = {"depth": DEPTH, "ttl_bars": Decimal("2"), "unknown": ONE}
    with pytest.raises(ValueError):
        LimitPullbackStrategy.from_params(unknown_params)
