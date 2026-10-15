import socket
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from strategylab.core.bars import Bar, Interval
from strategylab.data.aggregate import IncompleteBarGroup, aggregate
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.data.sync import fetch_page, rows_to_bars, sync_1m

DAY_2024_06_01 = 1717200000  # 2024-06-01T00:00:00Z, начало суток и граница 5m/15m/1h
DAY_SECONDS = 86400
SYMBOL = "BTCUSDT"

FIELDS = ("open", "high", "low", "close", "volume")


def _network_available() -> bool:
    try:
        with socket.create_connection(("api.bybit.com", 443), timeout=5):
            return True
    except OSError:
        return False


requires_network = pytest.mark.skipif(
    not _network_available(), reason="нет доступа к api.bybit.com"
)


def _bar(
    ts: int,
    open_: str = "1",
    high: str = "2",
    low: str = "0.5",
    close: str = "1.5",
    volume: str = "1",
) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def _minutes(start: int, offsets: Sequence[int]) -> list[Bar]:
    return [_bar(start + 60 * offset) for offset in offsets]


# Агрегация 1m в бар большего интервала


def test_aggregates_five_minutes() -> None:
    bars = [_bar(1717200000 + 60 * i, "1", "2", "0.5", "1.5") for i in range(10)]
    out = aggregate(bars, Interval.FIVE_MIN)
    assert len(out) == 2
    assert out[0].ts == 1717200000
    assert out[0].open == Decimal("1")
    assert out[0].high == Decimal("2")
    assert out[0].low == Decimal("0.5")
    assert out[0].close == Decimal("1.5")
    assert out[0].volume == Decimal("5")


def test_open_is_the_first_close_is_the_last_high_is_the_max_low_is_the_min() -> None:
    # Все четыре цены берутся из разных баров: максимум и минимум внутри группы,
    # а не на её концах, поэтому перестановка open/close или max/min провалила бы тест.
    fields = [("10", "12", "9", "11", "1"), ("11", "15", "8", "14", "2")]
    fields += [("14", "14.5", "7", "13", "4"), ("9", "10", "6.5", "8", "8")]
    fields += [("8", "9", "7", "7.5", "16")]
    bars = [_bar(1717200000 + 60 * i, *field) for i, field in enumerate(fields)]

    out = aggregate(bars, Interval.FIVE_MIN)

    assert (out[0].open, out[0].close) == (Decimal("10"), Decimal("7.5"))
    assert (out[0].high, out[0].low) == (Decimal("15"), Decimal("6.5"))
    assert out[0].volume == Decimal("31")
    assert len(out) == 1


def test_every_group_keeps_its_own_boundary_timestamp() -> None:
    out = aggregate(_minutes(1717200000, range(15)), Interval.FIVE_MIN)

    assert [bar.ts for bar in out] == [1717200000, 1717200300, 1717200600]


# Разрыв не соединяется: неполная группа — исключение, а не бар меньшего размера


def test_gap_does_not_produce_a_short_bar() -> None:
    # пять баров: 0,1,2 минуты, потом пропуск, потом 4-я минута
    bars = [_bar(1717200000 + 60 * i, "1", "2", "0.5", "1.5") for i in (0, 1, 2, 4)]
    with pytest.raises(IncompleteBarGroup) as exc:
        aggregate(bars, Interval.FIVE_MIN)
    assert exc.value.expected == 5
    assert exc.value.actual == 4


def test_the_reported_ts_is_the_start_of_the_bar_the_group_would_have_become() -> None:
    # Отсутствует первая минута группы: начало группы — 00:00, а не 00:01,
    # где стоит первый дошедший бар.
    bars = _minutes(1717200000, (1, 2, 3, 4))

    with pytest.raises(IncompleteBarGroup) as exc:
        aggregate(bars, Interval.FIVE_MIN)

    assert (exc.value.expected, exc.value.actual, exc.value.ts) == (5, 4, 1717200000)


def test_a_group_is_anchored_to_the_unix_epoch_and_not_to_the_first_bar() -> None:
    # Окно начинается с восьмой минуты. Якорь — эпоха Unix, поэтому группа, в которую
    # попали бары, начинается в 00:00; якорь по первому бару начал бы её в 00:07.
    bars = _minutes(1717200000, (7, 8))

    with pytest.raises(IncompleteBarGroup) as exc:
        aggregate(bars, Interval.FIFTEEN_MIN)

    assert (exc.value.expected, exc.value.actual, exc.value.ts) == (15, 2, 1717200000)


def test_a_whole_missing_group_is_reported_instead_of_being_skipped() -> None:
    # Пять минут подряд: группа 00:05..00:09 отсутствует целиком. Обе оставшиеся
    # группы полны, поэтому проверка длины их не видит, а пропущенный бар дошёл бы
    # до стратегии как настоящий.
    bars = _minutes(1717200000, (0, 1, 2, 3, 4, 10, 11, 12, 13, 14))

    with pytest.raises(IncompleteBarGroup) as exc:
        aggregate(bars, Interval.FIVE_MIN)

    assert (exc.value.expected, exc.value.actual, exc.value.ts) == (5, 0, 1717200300)


def test_the_earliest_fault_is_reported_first() -> None:
    bars = _minutes(1717200000, (0, 1, 10, 11, 12, 13, 14))

    with pytest.raises(IncompleteBarGroup) as exc:
        aggregate(bars, Interval.FIVE_MIN)

    assert (exc.value.expected, exc.value.actual, exc.value.ts) == (5, 2, 1717200000)


# Граничные случаи, отложенные задачей 2


@pytest.mark.parametrize(
    "interval",
    [Interval.MINUTE, Interval.FIVE_MIN, Interval.FIFTEEN_MIN, Interval.HOUR],
)
def test_empty_series_aggregates_to_nothing(interval: Interval) -> None:
    assert aggregate([], interval) == []


@pytest.mark.parametrize(
    ("interval", "factor"),
    [(Interval.FIVE_MIN, 5), (Interval.FIFTEEN_MIN, 15), (Interval.HOUR, 60)],
)
def test_a_single_minute_bar_is_not_a_bar_of_a_larger_interval(
    interval: Interval, factor: int
) -> None:
    with pytest.raises(IncompleteBarGroup) as exc:
        aggregate([_bar(1717200000)], interval)

    assert (exc.value.expected, exc.value.actual) == (factor, 1)


def test_factor_one_returns_the_bars_unchanged_and_checks_nothing() -> None:
    bars = _minutes(1717200000, (0, 1, 2, 4, 5))

    assert aggregate(bars, Interval.MINUTE) == bars
    assert aggregate(bars[:1], Interval.MINUTE) == bars[:1]


# Сверка с биржёй: инвариант 1 спеки (§3.3) — агрегация воспроизводит биржевые бары


def _day_store(tmp_path: Path) -> CandleStore:
    conn = connect(tmp_path / "aggregate.db")
    conn.executescript(SCHEMA)
    store = CandleStore(conn, SYMBOL)
    sync_1m(conn, SYMBOL, DAY_2024_06_01, DAY_2024_06_01 + DAY_SECONDS)
    return store


def _exchange_bars(interval: Interval, start: int, end: int) -> dict[int, Bar]:
    bars = rows_to_bars(fetch_page(end - 1, SYMBOL, interval))
    return {bar.ts: bar for bar in bars if start <= bar.ts < end}


@pytest.mark.network
@requires_network
@pytest.mark.parametrize("interval", [Interval.FIVE_MIN, Interval.FIFTEEN_MIN, Interval.HOUR])
def test_aggregation_reproduces_the_exchange_bars(interval: Interval, tmp_path: Path) -> None:
    start, end = DAY_2024_06_01, DAY_2024_06_01 + DAY_SECONDS
    store = _day_store(tmp_path)

    ours = aggregate(list(store.read_1m(SYMBOL, start, end)), interval)
    theirs = _exchange_bars(interval, start, end)

    assert [bar.ts for bar in ours] == sorted(theirs)
    differences = [
        (bar.ts, field, getattr(bar, field), getattr(theirs[bar.ts], field))
        for bar in ours
        for field in FIELDS
        if getattr(bar, field) != getattr(theirs[bar.ts], field)
    ]
    assert differences == []
