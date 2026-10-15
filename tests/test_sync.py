import socket
import sqlite3
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from strategylab.core.bars import Bar, Interval
from strategylab.data import sync
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.data.sync import BybitError, MalformedRow, fetch_page, rows_to_bars, sync_1m

CURSOR_2020_04_01 = 1585699200  # 2020-04-01T00:00:00Z, курсор включительно


def _network_available() -> bool:
    try:
        with socket.create_connection(("api.bybit.com", 443), timeout=5):
            return True
    except OSError:
        return False


requires_network = pytest.mark.skipif(
    not _network_available(), reason="нет доступа к api.bybit.com"
)


def _row(ts: int) -> list[str]:
    """Строка kline в том виде, в котором её отдаёт биржа."""
    return [str(ts * 1000), "67659.5", "67735.6", "67650.0", "67663.2", "2", "1"]


def _expected(ts: int) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal("67659.5"),
        high=Decimal("67735.6"),
        low=Decimal("67650.0"),
        close=Decimal("67663.2"),
        volume=Decimal("2"),
    )


def _serve(monkeypatch: pytest.MonkeyPatch, history: list[int], page: int = 1000) -> list[int]:
    """Подменяет `fetch_page` биржевым контрактом: строки по убыванию, `end` включительно.

    Возвращает список курсоров, по которым приходили страницы, — по нему видно,
    шёл ли `sync_1m` назад и откуда.
    """
    cursors: list[int] = []

    def fake_fetch_page(cursor_end: int, symbol: str) -> list[list[str]]:
        cursors.append(cursor_end)
        freshest = sorted((ts for ts in history if ts <= cursor_end), reverse=True)
        return [_row(ts) for ts in freshest[:page]]

    monkeypatch.setattr(sync, "fetch_page", fake_fetch_page)
    return cursors


def _db(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    path = tmp_path / "sync.db"
    conn = connect(path)
    conn.executescript(SCHEMA)
    return conn, path


def test_rows_are_reversed_and_timestamps_divided() -> None:
    rows = [
        ["1717200060000", "67663.2", "67700.0", "67650.0", "67690.0", "3", "1"],
        ["1717200000000", "67659.5", "67735.6", "67650.0", "67663.2", "2", "1"],
    ]
    bars = rows_to_bars(rows)
    assert [b.ts for b in bars] == [1717200000, 1717200060]
    assert bars[0].open == Decimal("67659.5")
    # close пятой колонки: в строке биржи это "67690.0", а "67663.2" — open бара
    assert bars[1].close == Decimal("67690.0")


def test_a_row_reads_as_exact_decimals() -> None:
    assert rows_to_bars([_row(1717200000)]) == [_expected(1717200000)]


@pytest.mark.parametrize(
    "row",
    [[], _row(1717200000)[:6], _row(1717200000) + ["1"]],
    ids=["empty", "six", "eight"],
)
def test_row_of_a_width_other_than_seven_is_malformed(row: list[str]) -> None:
    with pytest.raises(MalformedRow):
        rows_to_bars([row])


def test_a_malformed_row_in_the_middle_of_a_page_is_not_skipped() -> None:
    rows = [_row(1717200060), ["1717200000000"], _row(1717200120)]

    with pytest.raises(MalformedRow):
        rows_to_bars(rows)


# sync_1m идёт назад по курсору, пока окно не исчерпано


def test_sync_1m_walks_back_page_by_page_and_keeps_only_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    history = [1717200000 + 60 * i for i in range(5)]
    cursors = _serve(monkeypatch, history, page=2)
    conn, _ = _db(tmp_path)
    store = CandleStore(conn, "BTCUSDT")

    loaded = sync_1m(conn, "BTCUSDT", 1717200000, 1717200180)

    assert loaded == 3
    assert [b.ts for b in store.read_1m("BTCUSDT", 1717200000, 1717200180)] == [
        1717200000,
        1717200060,
        1717200120,
    ]
    assert cursors == [1717200180, 1717200119]


def test_sync_1m_is_a_noop_on_a_second_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, [1717200000 + 60 * i for i in range(5)])
    conn, _ = _db(tmp_path)
    store = CandleStore(conn, "BTCUSDT")

    assert sync_1m(conn, "BTCUSDT", 1717200000, 1717200180) == 3
    assert sync_1m(conn, "BTCUSDT", 1717200000, 1717200180) == 0
    assert store.count("BTCUSDT", 1717200000, 1717200180) == 3


def test_sync_1m_stops_on_an_empty_page_instead_of_asking_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cursors = _serve(monkeypatch, [])
    conn, _ = _db(tmp_path)

    assert sync_1m(conn, "BTCUSDT", 1717200000, 1717200180) == 0
    assert len(cursors) == 1


def test_sync_1m_commits_so_a_second_connection_sees_the_bars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, [1717200000])
    conn, path = _db(tmp_path)

    sync_1m(conn, "BTCUSDT", 1717200000, 1717200060)

    other = connect(path)
    assert CandleStore(other, "BTCUSDT").count("BTCUSDT", 1717200000, 1717200060) == 1


# Интервал биржи выводится из нашего Interval: HTTP-клиент в проекте один


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        (Interval.MINUTE, "1"),
        (Interval.FIVE_MIN, "5"),
        (Interval.FIFTEEN_MIN, "15"),
        (Interval.HOUR, "60"),
    ],
)
def test_fetch_page_asks_the_exchange_for_the_interval_mapped_from_ours(
    monkeypatch: pytest.MonkeyPatch, interval: Interval, expected: str
) -> None:
    asked: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> httpx.Response:
        asked.append(params)
        empty = {"retCode": 0, "result": {"list": []}}
        return httpx.Response(200, json=empty, request=httpx.Request("GET", url))

    monkeypatch.setattr(sync.httpx, "get", fake_get)

    assert fetch_page(CURSOR_2020_04_01, "BTCUSDT", interval) == []
    assert asked[0]["interval"] == expected


# Сеть


@pytest.mark.network
@requires_network
def test_fetch_page_returns_a_full_page_ending_at_the_cursor() -> None:
    bars = rows_to_bars(fetch_page(CURSOR_2020_04_01, "BTCUSDT"))

    assert len(bars) == 1000
    assert bars[-1].ts == CURSOR_2020_04_01
    # 2020-03-31T07:21:00Z: 1000 минутных баров назад от курсора включительно
    assert bars[0].ts == 1585639260


@pytest.mark.network
@requires_network
def test_fetch_page_raises_bybit_error_on_an_unknown_symbol() -> None:
    with pytest.raises(BybitError):
        fetch_page(CURSOR_2020_04_01, "NOSUCHUSDT")
