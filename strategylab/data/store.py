import sqlite3
from collections.abc import Iterable, Iterator

from strategylab.core.bars import Bar
from strategylab.data.zones import Zone

_INSERT = """
INSERT OR IGNORE INTO bars_1m (symbol, ts, open, high, low, close, volume, zone)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_READ_1M = """
SELECT ts, open, high, low, close, volume FROM bars_1m
WHERE symbol = ? AND ts >= ? AND ts < ?
ORDER BY ts
"""

_COUNT = """
SELECT COUNT(*) FROM bars_1m
WHERE symbol = ? AND ts >= ? AND ts < ?
"""


class CandleStore:
    """Минутные свечи: единственное место, где бары пишутся и читаются.

    Символ задаётся при создании: `Bar` символа не несёт, а писатель — `sync_1m`,
    который работает с одним символом за вызов. Чтение символ принимает явно,
    потому что окно чтения принадлежит вызывающему, а не хранилищу.

    Коммита здесь нет: транзакция принадлежит вызывающему, который её начал.
    """

    def __init__(self, conn: sqlite3.Connection, symbol: str) -> None:
        self._conn = conn
        self._symbol = symbol

    @property
    def conn(self) -> sqlite3.Connection:
        """Соединение с базой, только для чтения: сеттера нет, писатель один — вызывающий."""
        return self._conn

    def write(self, bars: Iterable[Bar], zone: Zone | None = None) -> int:
        """Пишет бары и возвращает число действительно вставленных строк.

        `Decimal` приводится к `str`: sqlite3 не биндит `Decimal` и бросил бы
        `InterfaceError`. `INSERT OR IGNORE` делает повторный импорт no-op и не
        даёт переписать зону бара, который уже записан.
        """
        rows = [
            (
                self._symbol,
                bar.ts,
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                None if zone is None else zone.value,
            )
            for bar in bars
        ]
        if not rows:
            return 0
        cursor = self._conn.executemany(_INSERT, rows)
        return cursor.rowcount

    def read_1m(self, symbol: str, start_ts: int, end_ts: int) -> Iterator[Bar]:
        """Бары окна `[start_ts, end_ts)` по возрастанию времени.

        Колонка `zone` не выбирается: стратегия не должна видеть границу своих
        данных, зона приходит в шапку отчёта извне. Значения не переоборачиваются
        в `Decimal` — фабрика `connect()` уже отдаёт числа как `Decimal`.
        """
        cursor = self._conn.execute(_READ_1M, (symbol, start_ts, end_ts))
        for ts, open_, high, low, close, volume in cursor:
            yield Bar(ts=ts, open=open_, high=high, low=low, close=close, volume=volume)

    def count(self, symbol: str, start_ts: int, end_ts: int) -> int:
        """Сколько баров символа лежит в окне `[start_ts, end_ts)`."""
        row = self._conn.execute(_COUNT, (symbol, start_ts, end_ts)).fetchone()
        return int(row[0])
