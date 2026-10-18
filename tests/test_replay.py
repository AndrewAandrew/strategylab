import sqlite3
from collections.abc import Iterable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Order, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.feed import BarFeed
from strategylab.core.metrics import Metrics
from strategylab.data.aggregate import IncompleteBarGroup
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.drivers.replay import ReplayDriver
from strategylab.strategy.base import Strategy, StrategyState
from strategylab.strategy.examples import BuyHoldStrategy

SYMBOL = "BTCUSDT"
START = 1717200000  # 2024-06-01T00:00:00Z, граница часов
HOUR = Interval.HOUR.value
# Окно обязано делиться на интервал: иначе агрегация упадёт IncompleteBarGroup,
# а прогон в тесте ленивой загрузки не дойдёт до баров.
MINUTES_PER_WINDOW = 2040  # ровно 34 часа


class CountingStore(CandleStore):
    """Хранилище со счётчиком обращений к `read_1m`.

    Счётчик стоит на месте вызова в тесте, а не на методе в проде: драйвер зовёт
    настоящий `CandleStore.read_1m` и не знает, что его считают.
    """

    def __init__(self, conn: sqlite3.Connection, symbol: str) -> None:
        super().__init__(conn, symbol)
        self.calls: list[tuple[str, int, int]] = []

    def read_1m(self, symbol: str, start_ts: int, end_ts: int) -> Iterator[Bar]:
        self.calls.append((symbol, start_ts, end_ts))
        return super().read_1m(symbol, start_ts, end_ts)


class FlatStrategy:
    """Стратегия, которая не открывает позицию: ордеров не возвращает никогда."""

    name = "flat"

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        return []


def _store(tmp_path: Path, name: str) -> CountingStore:
    conn = connect(tmp_path / name)
    conn.executescript(SCHEMA)
    return CountingStore(conn, SYMBOL)


def _minutes(start_ts: int, offsets: Iterable[int]) -> list[Bar]:
    return [
        Bar(
            ts=start_ts + 60 * offset,
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("0.5"),
            close=Decimal("1.5"),
            volume=Decimal("1"),
        )
        for offset in offsets
    ]


def _driver(store: CountingStore, start_ts: int, end_ts: int) -> ReplayDriver:
    return ReplayDriver(store, SYMBOL, Interval.HOUR, start_ts, end_ts)


def _signed_position(broker: BrokerSimulator) -> Decimal:
    qty = broker.position.qty
    if qty == 0:
        return Decimal("0")
    return qty if broker.position.side is Side.BUY else -qty


def _simulate_run(feed: BarFeed, broker: BrokerSimulator, strategy: Strategy) -> None:
    """То, как движок будет гонять драйвер: бар интервала применяется к брокеру, а
    минутные бары запрашиваются только пока позиция открыта.

    Метрики и реализованный pnl — работа движка (задача B), здесь состояние нужно
    лишь стратегии-заглушке.
    """
    metrics = Metrics()
    width = feed.interval.value
    for index, bar in enumerate(feed.bars()):
        broker.apply_bar(bar)
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


# Кэш агрегатов: интервал считается один раз на прогон


def test_interval_is_aggregated_once_for_the_whole_run(tmp_path: Path) -> None:
    store = _store(tmp_path, "once.db")
    store.write(_minutes(START, range(5 * 60)))
    driver = _driver(store, START, START + 5 * HOUR)

    first = list(driver.bars())
    second = list(driver.bars())

    assert first == second
    assert [bar.ts for bar in first] == [START + i * HOUR for i in range(5)]
    assert len(store.calls) == 1, "агрегация обязана быть один раз на прогон, а не на каждый проход"
    assert store.calls[0] == (SYMBOL, START, START + 5 * HOUR)


def test_bars_are_interval_bars_with_aggregated_values(tmp_path: Path) -> None:
    store = _store(tmp_path, "values.db")
    store.write(_minutes(START, range(60)))
    driver = _driver(store, START, START + HOUR)

    (bar,) = list(driver.bars())

    assert bar.ts == START
    assert bar.open == Decimal("1")
    assert bar.high == Decimal("2")
    assert bar.low == Decimal("0.5")
    assert bar.close == Decimal("1.5")
    assert bar.volume == Decimal("60")


def test_cache_lives_in_the_driver_so_different_windows_do_not_share_bars(tmp_path: Path) -> None:
    store = _store(tmp_path, "windows.db")
    store.write(_minutes(START, range(10 * 60)))
    first_driver = _driver(store, START, START + 5 * HOUR)
    second_driver = _driver(store, START + 5 * HOUR, START + 10 * HOUR)

    assert [bar.ts for bar in first_driver.bars()] == [START + i * HOUR for i in range(5)]
    assert [bar.ts for bar in second_driver.bars()] == [
        START + (5 + i) * HOUR for i in range(5)
    ]
    assert len(store.calls) == 2
    assert store.calls[0] == (SYMBOL, START, START + 5 * HOUR)
    assert store.calls[1] == (SYMBOL, START + 5 * HOUR, START + 10 * HOUR)


def test_two_drivers_on_the_same_window_aggregate_independently(tmp_path: Path) -> None:
    store = _store(tmp_path, "independent.db")
    store.write(_minutes(START, range(5 * 60)))
    first_driver = _driver(store, START, START + 5 * HOUR)
    second_driver = _driver(store, START, START + 5 * HOUR)

    assert list(first_driver.bars()) == list(second_driver.bars())
    assert len(store.calls) == 2, "кэш в модуле прочитал бы окно один раз на двоих"


# Лента и ленивая загрузка минутных баров


def test_replay_driver_implements_the_feed_protocol(tmp_path: Path) -> None:
    store = _store(tmp_path, "protocol.db")
    driver = _driver(store, START, START + HOUR)

    assert isinstance(driver, BarFeed)


def test_minute_bars_are_read_lazily_only_for_the_requested_range(tmp_path: Path) -> None:
    store = _store(tmp_path, "minutes.db")
    store.write(_minutes(START, range(5 * 60)))
    driver = _driver(store, START, START + 5 * HOUR)
    list(driver.bars())

    minutes = list(driver.minutes(START + HOUR, START + 2 * HOUR))

    assert [bar.ts for bar in minutes] == [START + HOUR + 60 * i for i in range(60)]
    assert store.calls[-1] == (SYMBOL, START + HOUR, START + 2 * HOUR)
    assert len(store.calls) == 2, "лишних обращений к хранилищу быть не должно"


def test_a_flat_run_reads_zero_minute_bars_for_execution(tmp_path: Path) -> None:
    store = _store(tmp_path, "lazy_flat.db")
    store.write(_minutes(START, range(MINUTES_PER_WINDOW)))
    driver = _driver(store, START, START + MINUTES_PER_WINDOW * 60)

    _simulate_run(driver, BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC), FlatStrategy())

    assert len(store.calls) == 1, (
        "позиция не открывалась: минутные бары для исполнения не читаются вообще"
    )
    assert store.calls[0] == (SYMBOL, START, START + MINUTES_PER_WINDOW * 60)


def test_a_run_holding_a_position_reads_the_minute_bars_of_the_open_hours(tmp_path: Path) -> None:
    store = _store(tmp_path, "lazy_open.db")
    store.write(_minutes(START, range(MINUTES_PER_WINDOW)))
    driver = _driver(store, START, START + MINUTES_PER_WINDOW * 60)

    broker = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    _simulate_run(driver, broker, BuyHoldStrategy())

    bars = list(driver.bars())
    hours_held = len(bars) - 1  # позиция открывается на баре 1 и держится до конца окна
    assert store.calls[0] == (SYMBOL, START, START + MINUTES_PER_WINDOW * 60)
    assert len(store.calls) == 1 + hours_held
    assert [call[1:3] for call in store.calls[1:]] == [
        (bar.ts, bar.ts + HOUR) for bar in bars[1:]
    ]


# Разрыв внутри окна: IncompleteBarGroup, а не пропущенный бар


def test_a_whole_missing_hour_raises_incomplete_bar_group(tmp_path: Path) -> None:
    store = _store(tmp_path, "gap_hour.db")
    store.write(_minutes(START, list(range(180)) + list(range(240, 300))))
    driver = _driver(store, START, START + 5 * HOUR)

    with pytest.raises(IncompleteBarGroup) as exc:
        list(driver.bars())

    assert (exc.value.expected, exc.value.actual, exc.value.ts) == (60, 0, START + 3 * HOUR)


def test_a_single_missing_minute_raises_rather_than_hiding_the_gap(tmp_path: Path) -> None:
    store = _store(tmp_path, "gap_minute.db")
    offsets = list(range(0, 120)) + list(range(121, 180)) + list(range(240, 300))
    store.write(_minutes(START, offsets))
    driver = _driver(store, START, START + 5 * HOUR)

    with pytest.raises(IncompleteBarGroup) as exc:
        list(driver.bars())

    assert (exc.value.expected, exc.value.actual, exc.value.ts) == (60, 59, START + 2 * HOUR)


# Пустое окно


def test_empty_window_yields_no_bars_and_still_reads_the_window_once(tmp_path: Path) -> None:
    store = _store(tmp_path, "empty.db")
    driver = _driver(store, START, START + HOUR)

    assert list(driver.bars()) == []
    assert store.calls == [(SYMBOL, START, START + HOUR)]
