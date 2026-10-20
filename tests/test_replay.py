import sqlite3
from collections.abc import Iterable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from strategylab.broker.orders import Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.feed import BarFeed
from strategylab.data.aggregate import IncompleteBarGroup
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.drivers.replay import ReplayDriver

SYMBOL = "BTCUSDT"
START = 1717200000  # 2024-06-01T00:00:00Z, граница часов
HOUR = Interval.HOUR.value


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


# Лента и протокол баров


def test_replay_driver_implements_the_feed_protocol(tmp_path: Path) -> None:
    store = _store(tmp_path, "protocol.db")
    driver = _driver(store, START, START + HOUR)

    assert isinstance(driver, BarFeed)


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
