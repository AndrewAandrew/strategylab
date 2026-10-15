import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from strategylab.data.schema import SCHEMA, connect
from strategylab.data.zones import (
    InmutableZoneError,
    Zone,
    assign_zone,
    zone_of,
    zones_touched,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "zones.db")
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


def _insert_bar(conn: sqlite3.Connection, ts: int, zone: str) -> None:
    """Бар пишется напрямую: зона материализуется в строке при импорте."""
    conn.execute(
        "INSERT INTO bars_1m VALUES (?,?,?,?,?,?,?,?)",
        ("BTCUSDT", ts, "67659.5", "67735.6", "67650.0", "67663.2", "1", zone),
    )


# Значение зоны — обычная строка: шапка отчёта печатает и сериализует зону как есть


def test_zone_compares_equal_to_its_string_value() -> None:
    assert Zone.FIT == "fit"
    assert Zone.HOLDOUT == "holdout"
    assert Zone.LIVE == "live"


def test_zone_prints_as_its_string_value() -> None:
    assert str(Zone.FIT) == "fit"
    assert str(Zone.HOLDOUT) == "holdout"
    assert str(Zone.LIVE) == "live"
    assert f"{Zone.HOLDOUT}" == "holdout"


# (а) zone_of отвечает по объявленному диапазону


def test_zone_of_is_fit_inside_the_declared_range(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)

    assert zone_of(conn, "BTCUSDT", 1000) is Zone.FIT
    assert zone_of(conn, "BTCUSDT", 1999) is Zone.FIT


def test_zone_of_is_none_outside_the_declared_range(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)

    assert zone_of(conn, "BTCUSDT", 999) is None
    assert zone_of(conn, "BTCUSDT", 2000) is None


def test_declared_ranges_of_two_symbols_do_not_see_each_other(
    conn: sqlite3.Connection,
) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)

    assert zone_of(conn, "ETHUSDT", 1500) is None


# (б) объявленный диапазон неизменен


@pytest.mark.parametrize(
    ("start_ts", "end_ts", "zone"),
    [
        (1000, 2000, Zone.HOLDOUT),
        (1500, 2500, Zone.HOLDOUT),
        (500, 1500, Zone.FIT),
        (1000, 2000, Zone.FIT),
        (1200, 1800, Zone.LIVE),
    ],
)
def test_assign_zone_refuses_an_intersecting_range(
    conn: sqlite3.Connection,
    start_ts: int,
    end_ts: int,
    zone: Zone,
) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)

    with pytest.raises(InmutableZoneError):
        assign_zone(conn, "BTCUSDT", start_ts, end_ts, zone)

    assert zone_of(conn, "BTCUSDT", 1500) is Zone.FIT


def test_adjacent_ranges_do_not_intersect(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)
    assign_zone(conn, "BTCUSDT", 2000, 3000, Zone.HOLDOUT)

    assert zone_of(conn, "BTCUSDT", 1999) is Zone.FIT
    assert zone_of(conn, "BTCUSDT", 2000) is Zone.HOLDOUT


def test_assign_zone_refuses_an_empty_range(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        assign_zone(conn, "BTCUSDT", 2000, 2000, Zone.FIT)

    assert zones_touched(conn, "BTCUSDT", 1000, 3000) == ()


# (в) объявление решает, а не материализованная в строке бара колонка


def test_declared_range_wins_over_the_materialised_bar_column(
    conn: sqlite3.Connection,
) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)
    _insert_bar(conn, 1500, "fit")
    _insert_bar(conn, 5000, "fit")

    inside = conn.execute("SELECT zone FROM bars_1m WHERE ts = ?", (1500,)).fetchone()
    outside = conn.execute("SELECT zone FROM bars_1m WHERE ts = ?", (5000,)).fetchone()

    assert inside is not None and outside is not None
    assert inside[0] == "fit"
    assert zone_of(conn, "BTCUSDT", 1500) is Zone.FIT
    assert outside[0] == "fit"
    assert zone_of(conn, "BTCUSDT", 5000) is None


# (г) граница зоны может проходить посреди окна — пункт 4 Review Focus


def test_zones_touched_returns_both_zones_when_the_boundary_is_inside(
    conn: sqlite3.Connection,
) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)
    assign_zone(conn, "BTCUSDT", 2000, 3000, Zone.HOLDOUT)

    assert zones_touched(conn, "BTCUSDT", 1900, 2100) == (Zone.FIT, Zone.HOLDOUT)


def test_zones_touched_is_ordered_by_zone_not_by_declaration(
    conn: sqlite3.Connection,
) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.HOLDOUT)
    assign_zone(conn, "BTCUSDT", 2000, 3000, Zone.FIT)

    assert zones_touched(conn, "BTCUSDT", 1900, 2100) == (Zone.FIT, Zone.HOLDOUT)


def test_zones_touched_is_single_inside_one_zone(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)
    assign_zone(conn, "BTCUSDT", 2000, 3000, Zone.HOLDOUT)

    assert zones_touched(conn, "BTCUSDT", 1200, 1300) == (Zone.FIT,)


def test_zones_touched_is_empty_outside_every_range(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.FIT)

    assert zones_touched(conn, "BTCUSDT", 500, 1000) == ()
    assert zones_touched(conn, "BTCUSDT", 2000, 2500) == ()
    assert zones_touched(conn, "BTCUSDT", 2000, 2000) == ()


def test_zones_touched_lists_a_zone_once_for_two_ranges(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 1500, Zone.FIT)
    assign_zone(conn, "BTCUSDT", 1500, 2000, Zone.FIT)

    assert zones_touched(conn, "BTCUSDT", 1200, 1800) == (Zone.FIT,)


def test_zones_touched_spans_all_three_zones(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.LIVE)
    assign_zone(conn, "BTCUSDT", 2000, 3000, Zone.HOLDOUT)
    assign_zone(conn, "BTCUSDT", 3000, 4000, Zone.FIT)

    canonical = (Zone.FIT, Zone.HOLDOUT, Zone.LIVE)
    assert zones_touched(conn, "BTCUSDT", 1500, 3500) == canonical
    assert zones_touched(conn, "BTCUSDT", 1900, 3100) == canonical
    assert zones_touched(conn, "BTCUSDT", 1000, 4000) == canonical
    assert zones_touched(conn, "BTCUSDT", 3999, 4000) == (Zone.FIT,)
    assert zones_touched(conn, "BTCUSDT", 2500, 3500) == (Zone.FIT, Zone.HOLDOUT)


def test_zones_touched_is_the_same_on_every_call(conn: sqlite3.Connection) -> None:
    assign_zone(conn, "BTCUSDT", 1000, 2000, Zone.LIVE)
    assign_zone(conn, "BTCUSDT", 2000, 3000, Zone.HOLDOUT)
    assign_zone(conn, "BTCUSDT", 3000, 4000, Zone.FIT)

    first = zones_touched(conn, "BTCUSDT", 1500, 3500)
    second = zones_touched(conn, "BTCUSDT", 1500, 3500)

    assert first == second == (Zone.FIT, Zone.HOLDOUT, Zone.LIVE)