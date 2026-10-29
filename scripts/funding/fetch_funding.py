"""Загрузчик funding-ставок Binance USDT-M в SQLite-базы лаборатории.

Только stdlib (urllib.request, sqlite3, json, decimal, argparse, time,
datetime) — ноль новых зависимостей. Данные складываются в таблицу
`funding_8h` внутри data/btcusdt.db и data/ethusdt.db (БД git-НЕ трекаются —
локальный артефакт, как bars_1m).

Зонная дисциплина (правило R-next-2 лаборатории). Данные скачиваются ЦЕЛИКОМ,
включая holdout/live отрезок (ts ≥ 1790294400): он ХРАНИТСЯ (сырое зеркало
биржи, прецедент bars_1m), но замерами НЕ ЗАПРАШИВАЕТСЯ. Границы зон:
fit [1761523200, 1790294400), BTC-предыстория [1704067200, 1761523200).
Zone-колонки в таблице нет — зонная дисциплина живёт в запросах замеров,
не в хранилище.

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe scripts/funding/fetch_funding.py [--dry-run]

CLI: `--symbols` (дефолт BTCUSDT ETHUSDT), `--start`/`--end` (опциональные
переопределения, Unix-секунды; дефолты старта: BTCUSDT 1704067200 — старт
bars_1m, ETHUSDT 1761523200 — старт ETH-баров; конец по умолчанию — текущий
момент), `--dry-run` (загрузка + валидация БЕЗ записи в БД), `--report <path>`
(provenance-JSON в файл; в stdout он печатается всегда).

Идемпотентность: INSERT OR REPLACE по первичному ключу (symbol, ts) —
повторный запуск сходится к тому же содержимому. ts = fundingTime // 1000
(мс-джиттер биржи вроде ...001/...003 flooring до секунд); rate/mark_price —
Decimal-строки как пришли (TEXT, конвенция bars_1m).

Валидация (любой провал — явное сообщение в stderr, данные НЕ пишутся,
exit ≠ 0): непрерывность (нет гэпов > 28800с между последовательными ts),
sanity (|rate| ≤ 0.05 у всех строк), счётчик (rows ≥ 8h-сетка × 0.98),
выравнивание (ts % 28800 == 0 у всех строк).
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
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"
PAGE_LIMIT = 1000
PERIOD_S = 28800  # период funding: 8ч, сетка 00/08/16 UTC
PAUSE_S = 0.5  # пауза между вызовами API (rate-limit friendly)
TIMEOUT_S = 30
MAX_ABS_RATE = Decimal("0.05")
MAX_PROBLEMS_PER_CHECK = 10

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
# Дефолтные старты, Unix-секунды: старт bars_1m символа (design «Факты источника»).
DEFAULT_START_TS: dict[str, int] = {"BTCUSDT": 1704067200, "ETHUSDT": 1761523200}

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

FUNDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_8h (
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    rate TEXT NOT NULL,
    mark_price TEXT,
    rate_type TEXT,
    PRIMARY KEY (symbol, ts)
);
"""


class FundingParseError(Exception):
    """Ответ биржи не разбирается: не список записей, нет полей, rate не Decimal-строка."""


@dataclass(frozen=True, slots=True)
class FundingRow:
    """Строка funding: ts — секунды (fundingTime floored), rate/mark_price — строки как пришли.

    funding_time_ms — сырые миллисекунды биржи: только курсор пагинации (+1 мс),
    в БД не хранится.
    """

    symbol: str
    ts: int
    rate: str
    mark_price: str | None
    rate_type: str | None
    funding_time_ms: int


def parse_payload(payload: object) -> list[FundingRow]:
    """JSON-ответ биржи → строки.

    fundingTime floor-ится до секунд (мс-джиттер вроде ...001/...003),
    rate/mark_price остаются Decimal-строками как пришли (конвенция bars_1m).
    """
    if not isinstance(payload, list):
        raise FundingParseError(f"ожидался список записей funding, получено: {str(payload)[:200]}")
    rows: list[FundingRow] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise FundingParseError(
                f"запись #{index}: ожидался объект, получен {type(item).__name__}"
            )
        try:
            symbol = item["symbol"]
            funding_time = item["fundingTime"]
            rate = item["fundingRate"]
        except KeyError as error:
            raise FundingParseError(f"запись #{index}: нет поля {error}") from error
        if not isinstance(symbol, str) or not isinstance(rate, str):
            raise FundingParseError(f"запись #{index}: symbol/fundingRate обязаны быть строками")
        if isinstance(funding_time, bool) or not isinstance(funding_time, int):
            raise FundingParseError(f"запись #{index}: fundingTime обязан быть int (мс)")
        try:
            Decimal(rate)  # проверяем парсимость; храним исходную строку
        except InvalidOperation as error:
            raise FundingParseError(
                f"запись #{index}: fundingRate не Decimal-строка: {rate!r}"
            ) from error
        mark_price = item.get("markPrice")
        rate_type = item.get("rateType")
        rows.append(
            FundingRow(
                symbol=symbol,
                ts=funding_time // 1000,
                rate=rate,
                mark_price=mark_price if isinstance(mark_price, str) else None,
                rate_type=rate_type if isinstance(rate_type, str) else None,
                funding_time_ms=funding_time,
            )
        )
    return rows


def _http_get_json(url: str) -> object:
    """GET через urllib; ответ — разобранный JSON."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode())


def fetch_page(
    symbol: str,
    start_ms: int,
    end_ms: int,
    limit: int = PAGE_LIMIT,
    get_json: Callable[[str], object] | None = None,
) -> list[FundingRow]:
    """Один вызов fundingRate (symbol, startTime, endTime, limit) → строки страницы."""
    getter = get_json if get_json is not None else _http_get_json
    query = urllib.parse.urlencode(
        {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": limit}
    )
    return parse_payload(getter(f"{ENDPOINT}?{query}"))


def fetch_all(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    fetch: Callable[[str, int, int, int], list[FundingRow]] = fetch_page,
    sleep: Callable[[float], None] = time.sleep,
    pause_s: float = PAUSE_S,
) -> tuple[list[FundingRow], int]:
    """Пагинация по startTime: курсор = fundingTime последней строки + 1 мс.

    Пауза между вызовами (rate-limit friendly). Возвращает (строки, число вызовов).
    """
    rows: list[FundingRow] = []
    calls = 0
    cursor = start_ms
    while cursor <= end_ms:
        if calls:
            sleep(pause_s)
        page = fetch(symbol, cursor, end_ms, PAGE_LIMIT)
        calls += 1
        if not page:
            break
        rows.extend(page)
        cursor = max(row.funding_time_ms for row in page) + 1
    return rows, calls


def _unique_sorted(rows: Sequence[FundingRow]) -> list[FundingRow]:
    """Дедуп по ts: мс-джиттер способен дать две записи одной секунды — оставляем последнюю."""
    by_ts = {row.ts: row for row in rows}
    return [by_ts[ts] for ts in sorted(by_ts)]


def _report(prefix: str, messages: Sequence[str]) -> list[str]:
    """Сообщения проверки со сквозным префиксом, не больше MAX_PROBLEMS_PER_CHECK штук."""
    capped = [f"{prefix}: {message}" for message in messages[:MAX_PROBLEMS_PER_CHECK]]
    if len(messages) > MAX_PROBLEMS_PER_CHECK:
        capped.append(f"{prefix}: …и ещё {len(messages) - MAX_PROBLEMS_PER_CHECK}")
    return capped


def _abs_rate(rate: str) -> Decimal | None:
    try:
        return abs(Decimal(rate))
    except InvalidOperation:
        return None


def validate_rows(rows: Sequence[FundingRow]) -> list[str]:
    """Четыре проверки design: выравнивание, sanity, непрерывность, счётчик.

    Возвращает список нарушений (пустой — данные чистые). Строки сперва
    уникализируются по ts и сортируются.
    """
    ordered = _unique_sorted(rows)
    if not ordered:
        return ["СЧЁТЧИК: ноль строк — загружать нечего"]
    problems: list[str] = []

    # (г) Выравнивание: ts % 28800 == 0 у всех строк (после floor мс-джиттера).
    problems += _report(
        "ВЫРАВНИВАНИЕ",
        [
            f"ts={row.ts} не кратен {PERIOD_S} (остаток {row.ts % PERIOD_S})"
            for row in ordered
            if row.ts % PERIOD_S != 0
        ],
    )

    # (б) Sanity: |rate| ≤ 0.05 у всех строк (guard от ошибок парсинга).
    sanity = []
    for row in ordered:
        magnitude = _abs_rate(row.rate)
        if magnitude is None:
            sanity.append(f"ts={row.ts}: rate не Decimal-строка: {row.rate!r}")
        elif magnitude > MAX_ABS_RATE:
            sanity.append(f"ts={row.ts}: |rate| = {row.rate} > {MAX_ABS_RATE}")
    problems += _report("SANITY", sanity)

    # (а) Непрерывность: нет гэпов > 28800с между последовательными ts.
    problems += _report(
        "НЕПРЕРЫВНОСТЬ",
        [
            f"гэп {b.ts - a.ts}с между ts={a.ts} и ts={b.ts} (> {PERIOD_S}с)"
            for a, b in zip(ordered, ordered[1:], strict=False)
            if b.ts - a.ts > PERIOD_S
        ],
    )

    # (в) Счётчик: rows ≥ ((last−first)/28800 + 1) × 0.98.
    first, last = ordered[0].ts, ordered[-1].ts
    expected = (last - first) // PERIOD_S + 1
    if len(ordered) * 100 < expected * 98:
        problems.append(
            f"СЧЁТЧИК: недобор — {len(ordered)} строк на [{first}, {last}], "
            f"8h-сетка ждёт {expected} (допуск ×0.98)"
        )
    return problems


def store_rows(db: Path, rows: Sequence[FundingRow]) -> int:
    """CREATE TABLE IF NOT EXISTS + INSERT OR REPLACE по PK; возвращает число записанных строк."""
    unique = _unique_sorted(rows)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(FUNDING_SCHEMA)
        conn.executemany(
            "INSERT OR REPLACE INTO funding_8h (symbol, ts, rate, mark_price, rate_type)"
            " VALUES (?, ?, ?, ?, ?)",
            [(row.symbol, row.ts, row.rate, row.mark_price, row.rate_type) for row in unique],
        )
        conn.commit()
    finally:
        conn.close()
    return len(unique)


def load_symbol(
    symbol: str,
    db: Path,
    start_ms: int,
    end_ms: int,
    *,
    dry_run: bool = False,
    fetch: Callable[[str, int, int, int], list[FundingRow]] = fetch_page,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, object], list[str]]:
    """Загрузка + валидация символа; запись только при чистых данных и не dry-run.

    Возвращает (provenance-сводка, список нарушений). Провал валидации — данные
    в БД не пишутся (report + stop).
    """
    rows, calls = fetch_all(symbol, start_ms, end_ms, fetch=fetch, sleep=sleep)
    problems = validate_rows(rows)
    ordered = _unique_sorted(rows)
    inserted = 0
    if ordered and not problems and not dry_run:
        inserted = store_rows(db, ordered)
    provenance: dict[str, object] = {
        "endpoint": ENDPOINT,
        "params": {
            "symbol": symbol,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": PAGE_LIMIT,
        },
        "calls": calls,
        "rows_fetched": len(rows),
        "rows_unique": len(ordered),
        "rows_inserted": inserted,
        "min_ts": ordered[0].ts if ordered else None,
        "max_ts": ordered[-1].ts if ordered else None,
        "dry_run": dry_run,
        "db": str(db),
        "loaded_at": datetime.now(UTC).isoformat(),
    }
    return provenance, problems


def _db_path(symbol: str) -> Path:
    """База лаборатории для символа: data/<symbol>.db в нижнем регистре (как bars_1m)."""
    return _REPO_ROOT / "data" / f"{symbol.lower()}.db"


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fetch_funding.py",
        description="Funding-ставки Binance USDT-M в таблицу funding_8h баз data/<symbol>.db.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS),
        help="символы (дефолт: BTCUSDT ETHUSDT)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="переопределение старта, Unix-секунды (дефолт на символ: "
        "BTCUSDT 1704067200, ETHUSDT 1761523200)",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="переопределение конца, Unix-секунды (дефолт: текущий момент)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="загрузка + валидация БЕЗ записи в БД",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="путь provenance-JSON (в stdout сводка печатается всегда)",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else list(argv))
    end_ms = args.end * 1000 if args.end is not None else int(time.time() * 1000)
    provenances: list[dict[str, object]] = []
    failures: list[tuple[str, list[str]]] = []
    for symbol in args.symbols:
        start_s = args.start if args.start is not None else DEFAULT_START_TS.get(symbol)
        if start_s is None:
            raise SystemExit(f"для {symbol} нет дефолтного старта — задайте --start (Unix-секунды)")
        provenance, problems = load_symbol(
            symbol, _db_path(symbol), start_s * 1000, end_ms, dry_run=args.dry_run
        )
        provenances.append(provenance)
        if problems:
            failures.append((symbol, problems))

    text = json.dumps(provenances, ensure_ascii=False, indent=2)
    print(text)
    if args.report is not None:
        report: Path = args.report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text + "\n", encoding="utf-8")

    if failures:
        for symbol, problems in failures:
            print(f"ПРОВАЛ ВАЛИДАЦИИ {symbol} — данные НЕ записаны:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
