from __future__ import annotations

from collections.abc import Iterator

from strategylab.core.bars import Bar, Interval
from strategylab.core.feed import BarFeed
from strategylab.data.aggregate import aggregate
from strategylab.data.store import CandleStore


class ReplayDriver(BarFeed):
    """Лента одного прогона: бары интервала из кэша агрегатов.

    Агрегация интервала считается ровно один раз на прогон, а кэш живёт в
    объекте драйвера: два прогона с разными окнами не видят чужих баров, и ключ
    кэша — `(symbol, interval, start_ts, end_ts)`. Минутные бары читаются из
    `CandleStore` один раз внутри `bars()` для агрегации и отдельным доступом
    не отдаются: брокер этой ветки исполняет ордера по барам интервала, а не
    внутри них.

    Зона баров драйвер не видит: `CandleStore.read_1m` её не выбирает, зона
    приходит в шапку отчёта извне.
    """

    def __init__(
        self,
        store: CandleStore,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> None:
        self._store = store
        self._symbol = symbol
        self.interval = interval
        self._start_ts = start_ts
        self._end_ts = end_ts
        self._aggregates: dict[tuple[str, Interval, int, int], list[Bar]] = {}

    def bars(self) -> Iterator[Bar]:
        """Бары интервала стратегии из кэша: агрегация считается один раз на прогон."""
        key = (self._symbol, self.interval, self._start_ts, self._end_ts)
        if key not in self._aggregates:
            minute_bars = list(self._store.read_1m(self._symbol, self._start_ts, self._end_ts))
            self._aggregates[key] = aggregate(minute_bars, self.interval)
        yield from self._aggregates[key]
