import sqlite3
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.fork import Fork, run_fork
from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar, Interval
from strategylab.data.schema import SCHEMA
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, assign_zone
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.base import Strategy, StrategyState

SYMBOL = "BTCUSDT"
START_TS = 1717200000  # 2024-06-01T00:00:00Z
END_TS = 1717200300  # 2024-06-01T00:05:00Z
MINUTE = 60
QTY = Decimal("1")

# Пять 1m-баров окна спеки §13.1: третий — реальный бар 2024-06-01 (O=67659.5,
# H=67735.6, L=67650.0), стоп 67652.71 и цель 67733.95 лежат внутри него.
REAL_BARS = (
    (1717200000, "67640.0", "67650.0", "67630.0", "67645.0"),
    (1717200060, "67645.0", "67670.0", "67640.0", "67663.2"),
    (1717200120, "67659.5", "67735.6", "67650.0", "67663.2"),
    (1717200180, "67663.2", "67680.0", "67650.0", "67670.0"),
    (1717200240, "67670.0", "67690.0", "67660.0", "67685.0"),
)

ENTRY = Decimal("67693.33")
STOP = Decimal("67652.71")
TARGET = Decimal("67733.95")


def _number_or_text(raw: bytes) -> Decimal | str:
    text = raw.decode()
    try:
        return Decimal(text)
    except InvalidOperation:
        return text


def make_store_with_real_bars() -> CandleStore:
    conn = sqlite3.connect(":memory:")
    conn.text_factory = _number_or_text
    conn.executescript(SCHEMA)
    assign_zone(conn, SYMBOL, START_TS, END_TS, Zone.FIT)
    store = CandleStore(conn, SYMBOL)
    store.write(
        [
            Bar(
                ts=ts,
                open=Decimal(o),
                high=Decimal(h),
                low=Decimal(low),
                close=Decimal(c),
                volume=Decimal("1.5"),
            )
            for ts, o, h, low, c in REAL_BARS
        ]
    )
    return store


def make_spec(
    *, symbol: str, interval: Interval, start_ts: int, end_ts: int
) -> StrategySpec:
    return StrategySpec(
        name="fixed_position",
        symbol=symbol,
        interval=interval,
        start_ts=start_ts,
        end_ts=end_ts,
        params={},
        rules_ref="spec/13.1",
    )


class FixedPositionStrategy:
    """Вход лимитом по фиксированной цене; стоп и цель — reduce-only после входа."""

    name = "fixed_position"

    def __init__(self, entry: Decimal, stop: Decimal, take_profit: Decimal) -> None:
        self._entry = entry
        self._stop = stop
        self._take_profit = take_profit
        self._entered = False
        self._protected = False

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        if state.position > 0:
            if self._protected:
                return []
            self._protected = True
            return [
                Order(Side.SELL, OrderKind.STOP_MARKET, QTY, None, self._stop, True),
                Order(Side.SELL, OrderKind.LIMIT, QTY, self._take_profit, None, True),
            ]
        if self._entered:
            return []
        self._entered = True
        return [Order(Side.BUY, OrderKind.LIMIT, QTY, self._entry, None, False)]


def make_fixed_position_strategy(
    *, entry: Decimal, stop: Decimal, take_profit: Decimal
) -> Callable[[], Strategy]:
    def factory() -> Strategy:
        return FixedPositionStrategy(entry, stop, take_profit)

    return factory


def _spec() -> StrategySpec:
    return make_spec(
        symbol=SYMBOL, interval=Interval.MINUTE, start_ts=START_TS, end_ts=END_TS
    )


def _fork() -> Fork:
    return run_fork(
        store=make_store_with_real_bars(),
        spec=_spec(),
        strategy=make_fixed_position_strategy(entry=ENTRY, stop=STOP, take_profit=TARGET),
        costs=CostModel(),
    )


def test_fork_diverges_when_stop_and_target_share_one_minute() -> None:
    # Бар 00:02 2024-06-01: H=67735.6, L=67650.0. Оба уровня внутри него.
    # Вход 67693.33, стоп 67652.71, цель 67733.95.
    f = run_fork(store=make_store_with_real_bars(), spec=make_spec(
        symbol="BTCUSDT", interval=Interval.MINUTE,
        start_ts=1717200000, end_ts=1717200300,
    ), strategy=make_fixed_position_strategy(
        entry=Decimal("67693.33"), stop=Decimal("67652.71"),
        take_profit=Decimal("67733.95"),
    ), costs=CostModel())
    assert f.pessimistic.metrics.net_pnl < f.optimistic.metrics.net_pnl
    assert f.diverges(verdict=lambda r: r.metrics.net_pnl > 0) is True


def test_net_pnl_of_each_branch_is_the_window_result() -> None:
    f = _fork()
    assert f.pessimistic.metrics.net_pnl == Decimal("-115.017685187")
    assert f.optimistic.metrics.net_pnl == Decimal("27.07321")
    assert f.pessimistic.metrics.trades == 1
    assert f.optimistic.metrics.trades == 1
    assert (f.pessimistic.metrics.wins, f.pessimistic.metrics.losses) == (0, 1)
    assert (f.optimistic.metrics.wins, f.optimistic.metrics.losses) == (1, 0)


def test_header_publishes_both_branches_from_spec_window() -> None:
    spec = _spec()
    f = _fork()
    for report in (f.pessimistic, f.optimistic):
        assert report.spec_version == spec.version
        assert report.zones_used == (Zone.FIT,)
        assert report.trials == 1
        assert report.costs == CostModel()
    assert f.pessimistic.intrabar is IntrabarOrder.PESSIMISTIC
    assert f.optimistic.intrabar is IntrabarOrder.OPTIMISTIC


def test_strategy_factory_is_called_once_per_branch() -> None:
    calls = 0

    def counting_factory() -> Strategy:
        nonlocal calls
        calls += 1
        return FixedPositionStrategy(ENTRY, STOP, TARGET)

    run_fork(
        store=make_store_with_real_bars(),
        spec=_spec(),
        strategy=counting_factory,
        costs=CostModel(),
    )
    assert calls == 2


def test_diverges_is_false_when_verdict_agrees_on_branches() -> None:
    f = _fork()
    assert f.diverges(verdict=lambda r: r.metrics.trades == 1) is False
    assert f.diverges(verdict=lambda r: r.metrics.net_pnl < 0) is True
