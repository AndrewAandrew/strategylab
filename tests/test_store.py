from decimal import Decimal
from pathlib import Path

from strategylab.core.bars import Bar
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone


def make_store(tmp_path: Path) -> CandleStore:
    """Поднимает схему и отдаёт хранилище; коммит остаётся за вызывающим."""
    conn = connect(tmp_path / "store.db")
    conn.executescript(SCHEMA)
    return CandleStore(conn, "BTCUSDT")


def make_bar(ts: int) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal("67659.5"),
        high=Decimal("67735.6"),
        low=Decimal("67650.0"),
        close=Decimal("67663.2"),
        volume=Decimal("12.3456789012345"),
    )


def test_write_then_read_1m_round_trips(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000)], zone=Zone.FIT)
    assert store.count("BTCUSDT", 1717200000, 1717200060) == 1
    assert [b.ts for b in store.read_1m("BTCUSDT", 1717200000, 1717200060)] == [1717200000]


def test_read_1m_does_not_select_the_zone_column(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000)], zone=Zone.HOLDOUT)
    bar = next(store.read_1m("BTCUSDT", 1717200000, 1717200060))
    assert not hasattr(bar, "zone")


# Decimal доезжает до потребителя точным, без потерь на float


def test_read_1m_returns_the_exact_decimals_that_were_written(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000)])

    assert next(store.read_1m("BTCUSDT", 1717200000, 1717200060)) == make_bar(1717200000)


def test_read_1m_orders_bars_by_time_whatever_the_write_order(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200120), make_bar(1717200000), make_bar(1717200060)])

    assert [b.ts for b in store.read_1m("BTCUSDT", 1717200000, 1717200180)] == [
        1717200000,
        1717200060,
        1717200120,
    ]


# Окно полуинтервальное, как объявленные зоны


def test_read_1m_window_excludes_the_bar_that_opens_at_end_ts(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000), make_bar(1717200060)])

    assert [b.ts for b in store.read_1m("BTCUSDT", 1717200000, 1717200060)] == [1717200000]
    assert store.count("BTCUSDT", 1717200060, 1717200120) == 1


def test_read_1m_of_an_empty_window_is_empty(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000)])

    assert [b.ts for b in store.read_1m("BTCUSDT", 1717200060, 1717200060)] == []


# Повторный импорт — no-op, зона уже записанного бара не перетирается


def test_write_counts_only_bars_that_were_really_inserted(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    assert store.write([make_bar(1717200000), make_bar(1717200060)], zone=Zone.FIT) == 2
    assert store.write([make_bar(1717200000)], zone=Zone.FIT) == 0
    assert store.count("BTCUSDT", 1717200000, 1717200120) == 2


def test_rerun_does_not_re_zone_an_already_written_bar(tmp_path: Path) -> None:
    conn = connect(tmp_path / "store.db")
    conn.executescript(SCHEMA)
    store = CandleStore(conn, "BTCUSDT")
    store.write([make_bar(1717200000)], zone=Zone.FIT)
    store.write([make_bar(1717200000)], zone=Zone.HOLDOUT)

    stored = conn.execute("SELECT zone FROM bars_1m WHERE ts = ?", (1717200000,)).fetchone()

    assert stored is not None
    assert stored[0] == "fit"


def test_two_symbols_stored_through_one_connection_do_not_mix(tmp_path: Path) -> None:
    conn = connect(tmp_path / "store.db")
    conn.executescript(SCHEMA)
    btc = CandleStore(conn, "BTCUSDT")
    eth = CandleStore(conn, "ETHUSDT")
    btc.write([make_bar(1717200000)])
    eth.write([make_bar(1717200060)])

    assert [b.ts for b in btc.read_1m("BTCUSDT", 1717200000, 1717200120)] == [1717200000]
    assert [b.ts for b in btc.read_1m("ETHUSDT", 1717200000, 1717200120)] == [1717200060]


# Транзакция принадлежит вызывающему: хранилище не коммитит


def test_write_does_not_commit(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000)], zone=Zone.FIT)

    other = connect(tmp_path / "store.db")

    assert CandleStore(other, "BTCUSDT").count("BTCUSDT", 1717200000, 1717200060) == 0
