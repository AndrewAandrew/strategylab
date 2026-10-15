import sqlite3
from decimal import Decimal
from typing import Any

import httpx

from strategylab.core.bars import Bar, Interval
from strategylab.data.store import CandleStore

BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"

_KLINE_WIDTH = 7
_MILLISECONDS_IN_SECOND = 1000
_PAGE_LIMIT = 1000
_TIMEOUT = 10.0

_BYBIT_INTERVAL = {
    Interval.MINUTE: "1",
    Interval.FIVE_MIN: "5",
    Interval.FIFTEEN_MIN: "15",
    Interval.HOUR: "60",
}


class MalformedRow(Exception):
    """Строка ответа биржи не является минутным баром."""


class BybitError(Exception):
    """Биржа ответила кодом, отличным от нуля."""


def rows_to_bars(rows: list[list[str]]) -> list[Bar]:
    """Биржевые строки kline в бары по возрастанию времени.

    Bybit отдаёт свежие бары первыми, поэтому список разворачивается, а миллисекунды
    делятся на тысячу: здесь, на границе сети, время биржи становится внутренним
    временем в секундах.
    """
    bars: list[Bar] = []
    for row in reversed(rows):
        if len(row) != _KLINE_WIDTH:
            raise MalformedRow(f"строка kline длиной {len(row)}, а не {_KLINE_WIDTH}: {row}")
        bars.append(
            Bar(
                ts=int(row[0]) // _MILLISECONDS_IN_SECOND,
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[5]),
            )
        )
    return bars


def fetch_page(
    cursor_end: int, symbol: str, interval: Interval = Interval.MINUTE
) -> list[list[str]]:
    """Страница баров интервала, заканчивающаяся баром `cursor_end` включительно.

    Курсор приходит в секундах и уходит в миллисекундах: обратное направление той
    же границы сети, поэтому преобразование живёт здесь, а не у вызывающего.
    Наш `Interval` переводится в интервал биржи здесь же — HTTP-клиент в проекте
    один, и сверка агрегации с биржей идёт через него, а не в обход.
    """
    response = httpx.get(
        BYBIT_KLINE,
        params={
            "category": "linear",
            "symbol": symbol,
            "interval": _BYBIT_INTERVAL[interval],
            "end": cursor_end * _MILLISECONDS_IN_SECOND,
            "limit": _PAGE_LIMIT,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload: Any = response.json()
    if payload["retCode"] != 0:
        raise BybitError(f"{symbol}: retCode={payload['retCode']} {payload.get('retMsg', '')}")
    rows: list[list[str]] = payload["result"]["list"]
    return rows


def sync_1m(conn: sqlite3.Connection, symbol: str, start_ts: int, end_ts: int) -> int:
    """Догружает минутные бары окна `[start_ts, end_ts)`, возвращает их число.

    Единственный писатель в проекте: `CandleStore.write` вызывается отсюда и
    больше нигде. Страницы запрашиваются назад по курсору, пока окно не исчерпано;
    повторный запуск ничего не добавляет, потому что вставка идёт через
    `INSERT OR IGNORE`. Коммит здесь: хранилище транзакцию не закрывает, а
    `sync_1m` — верхний вызывающий, который её начал.
    """
    store = CandleStore(conn, symbol)
    fresh: dict[int, Bar] = {}
    cursor_end = end_ts
    while True:
        bars = rows_to_bars(fetch_page(cursor_end, symbol))
        if not bars:
            break
        for bar in bars:
            if start_ts <= bar.ts < end_ts:
                fresh[bar.ts] = bar
        oldest = bars[0].ts
        if oldest <= start_ts:
            break
        cursor_end = oldest - 1
    loaded = store.write((fresh[ts] for ts in sorted(fresh)), zone=None)
    conn.commit()
    return loaded
