from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from strategylab.core.bars import Bar, Interval


@runtime_checkable
class BarFeed(Protocol):
    """Источник закрытых баров для прогона стратегии.

    Драйвер отдаёт бары интервала стратегии и, по запросу вызывающего, минутные
    бары диапазона: ленивость — обязанность драйвера, а не брокера. Протокол
    живёт в `core/` и не знает о драйвере; реализации — в `drivers/`.
    """

    interval: Interval

    def bars(self) -> Iterator[Bar]:
        """Бары интервала стратегии по возрастанию времени."""
        ...

    def minutes(self, start_ts: int, end_ts: int) -> Iterator[Bar]:
        """Минутные бары диапазона `[start_ts, end_ts)` по возрастанию времени."""
        ...
