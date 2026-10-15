import sqlite3
from enum import Enum


class Zone(str, Enum):  # noqa: UP042 — форма (str, Enum) зафиксирована контрактом плана
    """Зона данных: fit — подбор, holdout — проверка, live — живые данные."""

    FIT = "fit"
    HOLDOUT = "holdout"
    LIVE = "live"


class InmutableZoneError(Exception):
    """Объявленный диапазон зоны пересекается с уже объявленным."""


_ORDER: dict[Zone, int] = {zone: index for index, zone in enumerate(Zone)}

_OVERLAP = """
SELECT start_ts, end_ts, zone FROM zones
WHERE symbol = ? AND start_ts < ? AND end_ts > ?
"""

_AT = """
SELECT zone FROM zones
WHERE symbol = ? AND start_ts <= ? AND end_ts > ?
"""

_TOUCHED = """
SELECT DISTINCT zone FROM zones
WHERE symbol = ? AND start_ts < ? AND end_ts > ?
"""


def assign_zone(
    conn: sqlite3.Connection,
    symbol: str,
    start_ts: int,
    end_ts: int,
    zone: Zone,
) -> None:
    """Объявляет полуинтервал [start_ts, end_ts) заданной зоной.

    Диапазон неизменен: пересечение с уже объявленным — исключение, а не обновление.
    Соседние полуинтервалы пересечением не считаются: [a, b) и [b, c) не спорят ни за
    один бар, поэтому граница зоны может проходить ровно по началу бара.
    """
    if start_ts >= end_ts:
        raise ValueError(f"пустой диапазон зоны: [{start_ts}, {end_ts})")

    clash = conn.execute(_OVERLAP, (symbol, end_ts, start_ts)).fetchone()
    if clash is not None:
        raise InmutableZoneError(
            f"{symbol}: [{start_ts}, {end_ts}) пересекается с уже объявленной "
            f"зоной {clash[2]} на [{clash[0]}, {clash[1]})"
        )

    conn.execute(
        "INSERT INTO zones VALUES (?,?,?,?)",
        (symbol, start_ts, end_ts, zone.value),
    )


def zone_of(conn: sqlite3.Connection, symbol: str, ts: int) -> Zone | None:
    """Зона бара, открытого в `ts`, или None, если бар вне объявленных диапазонов."""
    row = conn.execute(_AT, (symbol, ts, ts)).fetchone()
    if row is None:
        return None
    return Zone(row[0])


def zones_touched(
    conn: sqlite3.Connection,
    symbol: str,
    start_ts: int,
    end_ts: int,
) -> tuple[Zone, ...]:
    """Зоны, реально пересекающие окно [start_ts, end_ts), в порядке Zone.

    Порядок объявления `Zone`, а не порядок строк в базе: шапка отчёта обязана
    читаться одинаково при любом порядке импорта.
    """
    rows = conn.execute(_TOUCHED, (symbol, end_ts, start_ts)).fetchall()
    return tuple(sorted({Zone(row[0]) for row in rows}, key=_ORDER.__getitem__))