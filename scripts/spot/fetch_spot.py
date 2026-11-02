"""Сборщик спотовых 1m-баров Bybit (category=spot) — база для Series C basis capture.

Идиомы заимствованы у scripts/funding/fetch_funding.py: stdlib urllib, Decimal-
строки как пришли, пагинация с паузой, валидация (выравнивание/непрерывность/
sanity/счётчик) с отказом от записи при любой проблеме, INSERT OR IGNORE
(идемпотентный повтор = resume; первая запись liveёт вечно — потому и гейт).

Таблица `spot_1m` живёт в той же базе символа, что и `bars_1m` (перп Bybit):
basis = перп/спот одного venue, поперечные биржи в ценовой паре исключены.

Бары отбираются целыми минутами: страница = окно в PAGE_LIMIT минут от курсора;
незакрытая текущая минута в историческом окне невозможна, при --end «сейчас»
последний бар может быть незакрытым — отрезается по ts < end.

Пример:
  python scripts/spot/fetch_spot.py --db data/btcusdt.db --symbol BTCUSDT \
      --start 1761523200 --end 1790294400
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

ENDPOINT = "https://api.bybit.com/v5/market/kline"
PAGE_LIMIT = 1000  # максимум баров на вызов Bybit
MINUTE_MS = 60_000
TIMEOUT_S = 30
PAUSE_S = 0.15
MAX_PROBLEMS_PER_CHECK = 10
RETRY_ATTEMPTS = 5  # транзиентные сетевые сбои (DNS/timeouts/5xx) на длинном бэкфилле
RETRY_BASE_S = 1.0  # экспоненциальный backoff: 1,2,4,8 сек между попытками

SPOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS spot_1m (
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume TEXT NOT NULL,
    PRIMARY KEY (symbol, ts)
) WITHOUT ROWID;
"""


class SpotParseError(Exception):
    """Ответ биржи не разбирается: не список kline, поля не строки, цена не Decimal."""


@dataclass(frozen=True, slots=True)
class SpotBar:
    """Один спот-бар: ts — секунды начала минуты, цены — сырые Decimal-строки."""

    symbol: str
    ts: int
    open: str
    high: str
    low: str
    close: str
    volume: str


def parse_payload(symbol: str, payload: object) -> list[SpotBar]:
    """JSON-ответ v5/market/kline → бары по возрастанию ts.

    Список Bybit нисходящий; строка = [start_ms, open, high, low, close,
    volume, turnover]. start_ms floor-ится в секунды; turnover не хранится.
    """
    if not isinstance(payload, dict):
        raise SpotParseError(f"ожидался объект ответа, получено: {str(payload)[:200]}")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("list"), list):
        raise SpotParseError(f"нет result.list: {str(payload)[:200]}")
    rows: list[SpotBar] = []
    for index, item in enumerate(result["list"]):
        if (
            not isinstance(item, list)
            or len(item) != 7
            or not all(isinstance(cell, str) for cell in item)
        ):
            raise SpotParseError(f"строка #{index}: ожидался list из 7 строк: {item!r}")
        try:
            start_ms = int(item[0])
        except ValueError as error:
            raise SpotParseError(f"строка #{index}: start не int: {item[0]!r}") from error
        for field_name, value in zip(
            ("open", "high", "low", "close", "volume"), item[1:6], strict=True
        ):
            try:
                parsed = Decimal(value)
            except InvalidOperation as error:
                raise SpotParseError(
                    f"строка #{index}: {field_name} не Decimal-строка: {value!r}"
                ) from error
            if not parsed.is_finite():
                raise SpotParseError(f"строка #{index}: {field_name} не конечное число: {value!r}")
        rows.append(SpotBar(symbol, start_ms // 1000, item[1], item[2], item[3], item[4], item[5]))
    rows.sort(key=lambda bar: bar.ts)
    return rows


def _http_get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode())


def get_json_with_retry(
    url: str,
    *,
    raw: Callable[[str], object] = _http_get_json,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = RETRY_ATTEMPTS,
    backoff_s: float = RETRY_BASE_S,
) -> object:
    """raw-GET с экспоненциальным backoff на сетевых сбоях (бэкфилл ~960 вызовов).

    Ловим OSError: URLError/HTTPError, socket.gaierror (DNS), socket.timeout,
    ConnectionReset* — все его подклассы. Плохой JSON/логика биржи сюда не
    доходят — это не транзиент, ретраить бессмысленно. После attempts неудач
    последняя ошибка летит вверх (run падает громко, повтор окна = resume).
    """
    last: OSError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return raw(url)
        except OSError as error:
            last = error
            if attempt < attempts:
                sleep(backoff_s * 2 ** (attempt - 1))
    assert last is not None
    raise last


def fetch_page(
    symbol: str,
    start_ms: int,
    end_ms: int,
    limit: int = PAGE_LIMIT,
    get_json: Callable[[str], object] | None = None,
) -> list[SpotBar]:
    """Один вызов kline (category=spot, interval=1) → бары окна [start_ms, end_ms]."""
    getter = get_json if get_json is not None else get_json_with_retry
    query = urllib.parse.urlencode(
        {
            "category": "spot",
            "symbol": symbol,
            "interval": "1",
            "start": start_ms,
            "end": end_ms,
            "limit": limit,
        }
    )
    return parse_payload(symbol, getter(f"{ENDPOINT}?{query}"))


def fetch_all(
    symbol: str,
    start_ts: int,
    end_ts: int,
    *,
    fetch: Callable[..., list[SpotBar]] = fetch_page,
    sleep: Callable[[float], None] = time.sleep,
    pause_s: float = PAUSE_S,
    page_limit: int = PAGE_LIMIT,
) -> tuple[list[SpotBar], int]:
    """Пагинация жёсткими минутными окнами: [cursor, cursor+page*60000−60000] включительно.

    Окно шьётся по page = min(page_limit, PAGE_LIMIT) минут и тот же page уезжает
    в запрос `limit`: страница Bybit не может быть обрезана cap'ом ниже окна.
    Требует start_ts кратным 60 (CLI-guard обеспечивает), иначе невыровненный
    первый курсор теряет слоты сетки. Пустые окна (гэп данных) просто продвигают
    курсор. Возвращает (бары, число вызовов).
    """
    page = max(1, min(page_limit, PAGE_LIMIT))
    bars: list[SpotBar] = []
    calls = 0
    cursor_ms = start_ts * 1000
    end_ms = (end_ts - 1) * 1000  # end exclusive: последний запрошенный старт минуты
    while cursor_ms <= end_ms:
        page_end_ms = min(cursor_ms + page * MINUTE_MS - MINUTE_MS, end_ms)
        if calls:
            sleep(pause_s)
        fetched = fetch(symbol, cursor_ms, page_end_ms, limit=page)
        page_bars = [bar for bar in fetched if bar.ts < end_ts]
        calls += 1
        bars.extend(page_bars)
        cursor_ms = page_end_ms + MINUTE_MS
    return dedup_sorted(bars), calls


def dedup_sorted(bars: Sequence[SpotBar]) -> list[SpotBar]:
    """Дедуп по ts (пересечение окон невозможно, но defense-in-depth), сортировка."""
    by_ts = {bar.ts: bar for bar in bars}
    return [by_ts[ts] for ts in sorted(by_ts)]


def _report(prefix: str, messages: Sequence[str]) -> list[str]:
    capped = [f"{prefix}: {message}" for message in messages[:MAX_PROBLEMS_PER_CHECK]]
    if len(messages) > MAX_PROBLEMS_PER_CHECK:
        capped.append(f"{prefix}: …и ещё {len(messages) - MAX_PROBLEMS_PER_CHECK}")
    return capped


def validate_rows(symbol: str, bars: Sequence[SpotBar]) -> list[str]:
    """Выравнивание (кратность 60с), непрерывность 1m-сетки, sanity цен, счётчик."""
    ordered = dedup_sorted(bars)
    if not ordered:
        return ["СЧЁТЧИК: ноль строк — загружать нечего"]
    problems: list[str] = []
    problems += _report(
        "ВЫРАВНИВАНИЕ",
        [f"ts={bar.ts} не кратно 60" for bar in ordered if bar.ts % 60 != 0],
    )
    problems += _report(
        "НЕПРЕРЫВНОСТЬ",
        [
            f"гэп {b.ts - a.ts}с между ts={a.ts} и ts={b.ts}"
            for a, b in zip(ordered, ordered[1:], strict=False)
            if b.ts - a.ts != 60
        ],
    )
    sanity: list[str] = []
    for bar in ordered:
        try:
            prices = [Decimal(bar.open), Decimal(bar.high), Decimal(bar.low), Decimal(bar.close)]
        except InvalidOperation:
            sanity.append(f"ts={bar.ts}: не Decimal в ценах")
            continue
        if any(price <= 0 for price in prices):
            sanity.append(f"ts={bar.ts}: неценовая цена (<=0)")
        if not (min(prices) >= Decimal(bar.low) and max(prices) <= Decimal(bar.high)):
            sanity.append(f"ts={bar.ts}: open/close вне [low, high]")
        if Decimal(bar.volume) < 0:
            sanity.append(f"ts={bar.ts}: отрицательный volume")
    problems += _report("SANITY", sanity)
    span = (ordered[-1].ts - ordered[0].ts) // 60 + 1
    if len(ordered) * 100 < span * 99:
        problems.append(
            f"СЧЁТЧИК: недобор — {len(ordered)} баров на [{ordered[0].ts}, {ordered[-1].ts}], "
            f"1m-сетка ждёт {span} (допуск ×0.99)"
        )
    mismatch = [bar.symbol for bar in ordered if bar.symbol != symbol]
    if mismatch:
        problems.append(f"SYMBOL: в ответе {mismatch[0]}, запрошен {symbol}")
    return problems


def store_rows(db: Path, bars: Sequence[SpotBar]) -> int:
    """CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE; возвращает реально вставленные строки."""
    unique = dedup_sorted(bars)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(SPOT_SCHEMA)
        cur = conn.executemany(
            "INSERT OR IGNORE INTO spot_1m (symbol, ts, open, high, low, close, volume)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(b.symbol, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in unique],
        )
        conn.commit()
        # rowcount — вставлено именно этим executemany; SELECT changes() заражен
        # внутренними statement'ами executescript (см. SQLite docs).
        return max(cur.rowcount, 0)
    finally:
        conn.close()


def load_symbol(
    db: Path,
    symbol: str,
    start_ts: int,
    end_ts: int,
    *,
    dry_run: bool = False,
    fetch: Callable[..., list[SpotBar]] = fetch_page,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """fetch_all → validate → store. Любая проблема валидации = отказ записи.

    Ординал funding-собрата (fetch_funding.load_symbol): INSERT OR IGNORE —
    first-write-wins, поэтому одна запись битых строк была бы вечной; при
    проблемах база не трогается вообще. Возвращает код выхода CLI.
    """
    bars, calls = fetch_all(symbol, start_ts, end_ts, fetch=fetch, sleep=sleep)
    problems = validate_rows(symbol, bars)
    stored = 0 if (not bars or problems or dry_run) else store_rows(db, bars)
    print(f"{symbol}: баров={len(bars)} вызовов={calls} вставлено={stored}")
    for problem in problems:
        print("ПРОБЛЕМА", problem, file=sys.stderr)
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", type=int, required=True, help="unix сек, вкл.")
    parser.add_argument("--end", type=int, required=True, help="unix сек, искл.")
    parser.add_argument("--dry-run", action="store_true", help="без записи в БД")
    args = parser.parse_args(argv)
    if args.end - args.start < 60 or args.start % 60 or args.end % 60:
        print("ОКНО: требуйте целых минут, длина >= 60с")
        return 2
    return load_symbol(args.db, args.symbol, args.start, args.end, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
