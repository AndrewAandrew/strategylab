"""Тесты загрузчика funding scripts/funding/fetch_funding.py.

Сети нет: живые ответы API записаны в tests/fixtures/funding/ (две настоящие
страницы BTCUSDT по 1000 строк, сняты 2026-10-29), urllib-вызов инжектируется
или подменяется monkeypatch. Все БД — временные (tmp_path); реальные data/*.db
не открываются никогда.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "funding" / "fetch_funding.py"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "funding"

PERIOD = 28800
BASE_TS = 1704067200  # 2024-01-01T00:00:00Z, % 28800 == 0
PAGE1_START_MS = 1704067200000
PAGE2_LAST_MS = 1761638400000  # fundingTime последней строки page2


def _load_module() -> ModuleType:
    """Скрипт живёт вне пакета (scripts/ — не пакет): грузим по пути файла."""
    spec = importlib.util.spec_from_file_location("fetch_funding_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # регистрация до исполнения: dataclass(slots=True) пересобирает класс через sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MOD = _load_module()


def _payload(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _parsed(name: str) -> list[object]:
    return list(MOD.parse_payload(_payload(name)))


def _row(ts: int, rate: str = "0.00010000") -> object:
    """Синтетическая строка на 8h-сетке (не запись API)."""
    return MOD.FundingRow(
        symbol="BTCUSDT",
        ts=ts,
        rate=rate,
        mark_price="100.00000000",
        rate_type="Regular",
        funding_time_ms=ts * 1000,
    )


def _grid(count: int, rate: str = "0.00010000") -> list[object]:
    return [_row(BASE_TS + index * PERIOD, rate) for index in range(count)]


def _dump(db: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT symbol, ts, rate, mark_price, rate_type FROM funding_8h ORDER BY symbol, ts"
        ).fetchall()
    finally:
        conn.close()


def _two_page_fetch(calls: list[tuple[int, int, int]]):
    """Фейк fetch_page: две записанные страницы, дальше пусто."""
    pages = [_parsed("btcusdt_page1.json"), _parsed("btcusdt_page2.json")]

    def fake(symbol: str, start_ms: int, end_ms: int, limit: int) -> list[object]:
        calls.append((start_ms, end_ms, limit))
        return pages[len(calls) - 1] if len(calls) <= 2 else []

    return fake


# --- парсер -----------------------------------------------------------------


def test_parse_recorded_fixture_keeps_decimal_strings() -> None:
    rows = _parsed("btcusdt_page1.json")
    assert len(rows) == 1000
    first = rows[0]
    assert first.symbol == "BTCUSDT"
    assert first.ts == 1704067200
    # Decimal-строки как пришли (конвенция bars_1m), без переформатирования
    assert first.rate == "0.00037409"
    assert first.mark_price == "42313.90000000"
    assert first.rate_type == "Regular"
    assert Decimal(first.rate) == Decimal("0.00037409")


def test_parse_floors_synthetic_ms_jitter() -> None:
    payload = [
        {
            "symbol": "BTCUSDT",
            "fundingTime": 1704067200003,
            "fundingRate": "0.00010000",
            "markPrice": "42000.00000000",
            "rateType": "Regular",
        }
    ]
    rows = MOD.parse_payload(payload)
    assert len(rows) == 1
    assert rows[0].ts == 1704067200
    assert rows[0].funding_time_ms == 1704067200003


def test_parse_floors_real_ms_jitter_from_fixture() -> None:
    payload = _payload("btcusdt_page1.json")
    assert isinstance(payload, list)
    jittered = [item for item in payload if item["fundingTime"] % 1000 != 0]
    assert jittered, "в записанной фикстуре нет строк с мс-джиттером"
    rows = {row.funding_time_ms: row for row in MOD.parse_payload(payload)}
    for item in jittered:
        assert rows[item["fundingTime"]].ts == item["fundingTime"] // 1000


def test_parse_rejects_error_payload() -> None:
    with pytest.raises(MOD.FundingParseError):
        MOD.parse_payload({"code": -1121, "msg": "Invalid symbol."})


def test_parse_rejects_bad_rate() -> None:
    payload = [{"symbol": "BTCUSDT", "fundingTime": 1704067200000, "fundingRate": "oops"}]
    with pytest.raises(MOD.FundingParseError):
        MOD.parse_payload(payload)


def test_fetch_page_builds_request_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_get(url: str) -> object:
        captured.append(url)
        return _payload("btcusdt_page1.json")

    monkeypatch.setattr(MOD, "_http_get_json", fake_get)
    rows = MOD.fetch_page("BTCUSDT", PAGE1_START_MS, PAGE2_LAST_MS)
    assert captured == [
        MOD.ENDPOINT + "?symbol=BTCUSDT&startTime=1704067200000&endTime=1761638400000&limit=1000"
    ]
    assert len(rows) == 1000


# --- пагинация ---------------------------------------------------------------


def test_pagination_walks_two_pages_by_funding_time() -> None:
    calls: list[tuple[int, int, int]] = []
    pauses: list[float] = []
    rows, call_count = MOD.fetch_all(
        "BTCUSDT",
        PAGE1_START_MS,
        PAGE2_LAST_MS,
        fetch=_two_page_fetch(calls),
        sleep=pauses.append,
    )
    assert call_count == 2
    assert len(rows) == 2000
    # курсор второй страницы = fundingTime последней строки первой + 1 мс
    assert calls[0][0] == PAGE1_START_MS
    assert calls[1][0] == 1732838400000 + 1
    assert all(call[2] == MOD.PAGE_LIMIT for call in calls)
    assert pauses == [MOD.PAUSE_S]  # пауза только между вызовами


# --- валидаторы --------------------------------------------------------------


def test_validate_clean_grid_passes() -> None:
    assert MOD.validate_rows(_grid(50)) == []


def test_validate_recorded_pages_pass() -> None:
    rows = [*_parsed("btcusdt_page1.json"), *_parsed("btcusdt_page2.json")]
    assert MOD.validate_rows(rows) == []


def test_validate_empty_fails() -> None:
    assert MOD.validate_rows([]) != []


def test_validate_gap_over_8h_fails() -> None:
    rows = [*_grid(10), _row(BASE_TS + 12 * PERIOD)]
    problems = MOD.validate_rows(rows)
    assert any(problem.startswith("НЕПРЕРЫВНОСТЬ") for problem in problems)


def test_validate_abs_rate_over_cap_fails() -> None:
    for rate in ("0.0500001", "-0.06"):
        rows = _grid(10)
        rows[3] = _row(BASE_TS + 3 * PERIOD, rate)
        problems = MOD.validate_rows(rows)
        assert any(problem.startswith("SANITY") for problem in problems), rate


def test_validate_rate_at_cap_passes() -> None:
    assert MOD.validate_rows(_grid(3, rate="0.05")) == []
    assert MOD.validate_rows(_grid(3, rate="-0.05")) == []


def test_validate_row_count_shortfall_fails() -> None:
    problems = MOD.validate_rows(_grid(100)[::2])
    assert any(problem.startswith("СЧЁТЧИК") for problem in problems)


def test_validate_misaligned_ts_fails() -> None:
    rows = [_row(BASE_TS), _row(BASE_TS + PERIOD // 2), _row(BASE_TS + PERIOD)]
    problems = MOD.validate_rows(rows)
    assert any(problem.startswith("ВЫРАВНИВАНИЕ") for problem in problems)
    assert not any(problem.startswith("НЕПРЕРЫВНОСТЬ") for problem in problems)


# --- запись, идемпотентность, dry-run ----------------------------------------


def test_store_creates_table_and_keeps_text_values(tmp_path: Path) -> None:
    db = tmp_path / "funding.db"
    rows = _parsed("btcusdt_page1.json")
    inserted = MOD.store_rows(db, rows)
    assert inserted == 1000
    conn = sqlite3.connect(db)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'funding_8h'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "PRIMARY KEY (symbol, ts)" in sql
    stored = _dump(db)
    assert len(stored) == 1000
    assert stored[0] == ("BTCUSDT", 1704067200, "0.00037409", "42313.90000000", "Regular")


def test_store_idempotent_same_content(tmp_path: Path) -> None:
    db = tmp_path / "funding.db"
    rows = _parsed("btcusdt_page1.json")
    MOD.store_rows(db, rows)
    first = _dump(db)
    MOD.store_rows(db, rows)  # повторный запуск
    assert _dump(db) == first


def test_store_dedups_same_second_jitter(tmp_path: Path) -> None:
    db = tmp_path / "funding.db"
    rows = [
        MOD.FundingRow("BTCUSDT", BASE_TS, "0.00010000", None, None, BASE_TS * 1000),
        MOD.FundingRow("BTCUSDT", BASE_TS, "0.00020000", None, None, BASE_TS * 1000 + 3),
    ]
    assert MOD.store_rows(db, rows) == 1
    assert _dump(db) == [("BTCUSDT", BASE_TS, "0.00020000", None, None)]


def test_load_symbol_writes_and_reports(tmp_path: Path) -> None:
    db = tmp_path / "funding.db"
    calls: list[tuple[int, int, int]] = []
    provenance, problems = MOD.load_symbol(
        "BTCUSDT",
        db,
        PAGE1_START_MS,
        PAGE2_LAST_MS,
        fetch=_two_page_fetch(calls),
        sleep=lambda _seconds: None,
    )
    assert problems == []
    assert provenance["rows_inserted"] == 2000
    assert len(_dump(db)) == 2000


def test_load_symbol_dry_run_leaves_db_untouched(tmp_path: Path) -> None:
    # БД не существует — dry-run её не создаёт
    missing = tmp_path / "missing.db"
    calls: list[tuple[int, int, int]] = []
    provenance, problems = MOD.load_symbol(
        "BTCUSDT",
        missing,
        PAGE1_START_MS,
        PAGE2_LAST_MS,
        dry_run=True,
        fetch=_two_page_fetch(calls),
        sleep=lambda _seconds: None,
    )
    assert problems == []
    assert provenance["rows_fetched"] == 2000
    assert provenance["rows_inserted"] == 0
    assert not missing.exists()

    # БД существует — dry-run не меняет содержимое
    existing = tmp_path / "existing.db"
    MOD.store_rows(existing, _parsed("btcusdt_page1.json"))
    before = _dump(existing)
    calls.clear()
    provenance, problems = MOD.load_symbol(
        "BTCUSDT",
        existing,
        PAGE1_START_MS,
        PAGE2_LAST_MS,
        dry_run=True,
        fetch=_two_page_fetch(calls),
        sleep=lambda _seconds: None,
    )
    assert problems == []
    assert provenance["rows_inserted"] == 0
    assert _dump(existing) == before


def test_load_symbol_validation_failure_blocks_write(tmp_path: Path) -> None:
    db = tmp_path / "funding.db"
    broken = [*_grid(10), _row(BASE_TS + 12 * PERIOD)]

    def fake(symbol: str, start_ms: int, end_ms: int, limit: int) -> list[object]:
        return broken if start_ms == PAGE1_START_MS else []

    provenance, problems = MOD.load_symbol(
        "BTCUSDT",
        db,
        PAGE1_START_MS,
        PAGE2_LAST_MS,
        fetch=fake,
        sleep=lambda _seconds: None,
    )
    assert problems != []
    assert provenance["rows_inserted"] == 0
    assert not db.exists()


# --- provenance ---------------------------------------------------------------


def test_provenance_has_required_keys(tmp_path: Path) -> None:
    from datetime import datetime

    db = tmp_path / "funding.db"
    calls: list[tuple[int, int, int]] = []
    provenance, problems = MOD.load_symbol(
        "BTCUSDT",
        db,
        PAGE1_START_MS,
        PAGE2_LAST_MS,
        fetch=_two_page_fetch(calls),
        sleep=lambda _seconds: None,
    )
    assert problems == []
    for key in (
        "endpoint",
        "params",
        "calls",
        "rows_inserted",
        "min_ts",
        "max_ts",
        "loaded_at",
    ):
        assert key in provenance, key
    assert provenance["endpoint"] == MOD.ENDPOINT
    params = provenance["params"]
    assert isinstance(params, dict)
    assert set(params) == {"symbol", "startTime", "endTime", "limit"}
    assert params["symbol"] == "BTCUSDT"
    assert params["startTime"] == PAGE1_START_MS
    assert params["endTime"] == PAGE2_LAST_MS
    assert params["limit"] == 1000
    assert provenance["calls"] == 2
    assert provenance["min_ts"] == 1704067200
    assert provenance["max_ts"] == 1761638400
    assert json.dumps(provenance)  # сериализуется без оговорок
    datetime.fromisoformat(str(provenance["loaded_at"]))  # UTC ISO


# --- CLI -----------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    args = MOD._parse_args([])
    assert args.symbols == ["BTCUSDT", "ETHUSDT"]
    assert args.start is None
    assert args.end is None
    assert args.dry_run is False
    assert args.report is None
