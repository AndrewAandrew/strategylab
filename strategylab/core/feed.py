from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from strategylab.core.bars import Bar, Interval


@runtime_checkable
class BarFeed(Protocol):
    """Источник закрытых баров для прогона стратегии.

    Протокол живёт в `core/` и не знает о драйвере; реализации — в `drivers/`.
    """

    interval: Interval

    def bars(self) -> Iterator[Bar]:
        """Бары интервала стратегии по возрастанию времени."""
        ...
