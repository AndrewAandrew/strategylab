"""Тесты сборщика спот-баров scripts/spot/fetch_spot.py (Series C, данные).

Сеть не трогаем: getter/fetch внедряем. Проверяем парсер (нисходящий список →
восходящий, floor мс, не-finite отбраковка), пагинацию жёсткими окнами (+cap и
прямая передача limit), store idempotent (INSERT OR IGNORE), validate_rows
(каждая проверка по отдельности), load_symbol (отказ записи при проблемах,
dry-run, чистый путь), CLI window guard.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

_GRID = Path(__file__).resolve().parent.parent / "scripts" / "spot" / "fetch_spot.py"


def _load() -> ModuleType:
    cached = sys.modules.get("fetch_spot_under_test")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("fetch_spot_under_test", _GRID)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_spot_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def spot() -> ModuleType:
    return _load()


def payload(rows: list[list[str]]) -> dict:
    return {"retCode": 0, "result": {"category": "spot", "list": rows}}


def bar(ts_s: int, price: str = "100") -> list[str]:
    return [
        str(ts_s * 1000),
        price,
        str(float(price) + 1),
        str(float(price) - 1),
        price,
        "1.5",
        "150",
    ]


def test_parse_orders_floors_validates(spot: ModuleType) -> None:
    rows = spot.parse_payload("BTCUSDT", payload([bar(120), bar(60), bar(0)]))
    assert [r.ts for r in rows] == [0, 60, 120]
    assert rows[0].open == "100" and rows[0].high == "101.0"
    with pytest.raises(spot.SpotParseError):
        spot.parse_payload("X", {"result": {"list": [["1", "2"]]}})  # не 7 полей
    with pytest.raises(spot.SpotParseError):
        spot.parse_payload("X", payload([[str(0), "abc", "1", "1", "1", "1", "1"]]))
    with pytest.raises(spot.SpotParseError):
        spot.parse_payload("X", payload([[str(0), "NaN", "1", "1", "1", "1", "1"]]))  # не finite
    with pytest.raises(spot.SpotParseError):
        spot.parse_payload("X", "not a dict")  # type: ignore[arg-type]


def test_pagination_hard_windows(spot: ModuleType) -> None:
    def fake(symbol: str, start_ms: int, end_ms: int, **kw: object) -> list[spot.SpotBar]:
        n = (end_ms - start_ms) // 60_000 + 1
        return [
            spot.SpotBar(symbol, start_ms // 1000 + i * 60, "1", "1", "1", "1", "1")
            for i in range(n)
        ]

    # окно короче страницы: один вызов, ts < end (exclusive) отсечён
    bars, calls = spot.fetch_all("BTCUSDT", 0, 250, fetch=fake, sleep=lambda s: None)
    assert calls == 1
    assert [b.ts for b in bars] == list(range(0, 250, 60))  # 0..240
    assert all(b.ts < 250 for b in bars)

    # многостраничная разбивка: page_limit=2 → окна ровно по 2 минуты, без пропусков
    bars2, calls2 = spot.fetch_all(
        "BTCUSDT", 0, 1200, fetch=fake, sleep=lambda s: None, page_limit=2
    )
    assert calls2 == 10
    assert [b.ts for b in bars2] == list(range(0, 1200, 60))


def test_pagination_dedups_crossing(spot: ModuleType) -> None:
    # если страница вернула бар за границей окна (биржа округляет) — дедуп не ломает порядок
    def fake(symbol: str, start_ms: int, end_ms: int, **kw: object) -> list[spot.SpotBar]:
        return [
            spot.SpotBar(symbol, end_ms // 1000, "1", "1", "1", "1", "1"),
            spot.SpotBar(symbol, start_ms // 1000, "1", "1", "1", "1", "1"),
        ]

    bars, calls = spot.fetch_all("BTCUSDT", 0, 300, fetch=fake, sleep=lambda s: None, page_limit=1)
    assert calls == 5
    assert [b.ts for b in bars] == [0, 60, 120, 180, 240]  # ts<300, дедуп по ts


def test_pagination_forwards_capped_limit(spot: ModuleType) -> None:
    # page_limit сверх капса: окно шьётся по cap'у, limit в запросе = cap (нет тихого обрезания)
    seen_limits: list[int] = []

    def fake(symbol: str, start_ms: int, end_ms: int, limit: int = 1000, **kw: object) -> list:
        seen_limits.append(limit)
        n = (end_ms - start_ms) // 60_000 + 1
        return [
            spot.SpotBar(symbol, start_ms // 1000 + i * 60, "1", "1", "1", "1", "1")
            for i in range(n)
        ]

    bars, calls = spot.fetch_all(
        "BTCUSDT", 0, 4000 * 60, fetch=fake, sleep=lambda s: None, page_limit=4000
    )
    assert calls == 4
    assert seen_limits == [1000, 1000, 1000, 1000]
    assert len(bars) == 4000


def test_store_idempotent(spot: ModuleType, tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    rows = [spot.SpotBar("BTCUSDT", t * 60, "100", "101", "99", "100", "1") for t in range(3)]
    assert spot.store_rows(db, rows) == 3
    assert spot.store_rows(db, rows) == 0  # OR IGNORE: повтор ничего не добавляет
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM spot_1m").fetchone()[0] == 3
    conn.close()


def _bars(spot: ModuleType, ts_list: list[int]) -> list:
    return [spot.SpotBar("BTCUSDT", t, "100", "101", "99", "100", "1") for t in ts_list]


def test_validate_ok(spot: ModuleType) -> None:
    assert spot.validate_rows("BTCUSDT", _bars(spot, [0, 60, 120])) == []


def test_validate_flags(spot: ModuleType) -> None:
    assert any("ВЫРАВНИВАНИЕ" in p for p in spot.validate_rows("BTCUSDT", _bars(spot, [0, 30])))
    assert any("НЕПРЕРЫВНОСТЬ" in p for p in spot.validate_rows("BTCUSDT", _bars(spot, [0, 180])))
    bad = [spot.SpotBar("BTCUSDT", 0, "100", "101", "99", "200", "1")]  # close выше high
    assert any("SANITY" in p for p in spot.validate_rows("BTCUSDT", bad))
    empty = spot.validate_rows("BTCUSDT", [])
    assert any("СЧЁТЧИК" in p for p in empty)
    wrong = [
        spot.SpotBar("BTCUSDT", 0, "100", "101", "99", "100", "1"),
        spot.SpotBar("ETHUSDT", 60, "100", "101", "99", "100", "1"),  # не первый бар — тоже ловим
    ]
    assert any("SYMBOL" in p for p in spot.validate_rows("BTCUSDT", wrong))


def test_load_refuses_write_on_problems(spot: ModuleType, tmp_path: Path, capsys) -> None:
    # BLOCKER-гейт fresh-eyes: любая проблема валидации = база не создаётся вообще
    def bad(symbol: str, start_ms: int, end_ms: int, **kw: object) -> list:
        return [
            spot.SpotBar(symbol, 0, "1", "1", "1", "1", "1"),
            spot.SpotBar(symbol, 300, "1", "1", "1", "1", "1"),  # гэп 300с
        ]

    db = tmp_path / "never.db"
    code = spot.load_symbol(db, "BTCUSDT", 0, 600, fetch=bad, sleep=lambda s: None)
    assert code == 1
    assert not db.exists()
    captured = capsys.readouterr()
    assert "вставлено=0" in captured.out  # сводка — в stdout
    assert "НЕПРЕРЫВНОСТЬ" in captured.err  # проблемы — в stderr, как у funding


def test_load_empty_no_write(spot: ModuleType, tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    code = spot.load_symbol(
        db, "NOPEUSDT", 0, 600, fetch=lambda s, a, b, **k: [], sleep=lambda x: None
    )
    assert code == 1  # ноль строк — громко, и база-пустышка не создаётся
    assert not db.exists()


def test_load_dry_run_and_clean(spot: ModuleType, tmp_path: Path) -> None:
    def good(symbol: str, start_ms: int, end_ms: int, **kw: object) -> list:
        n = (end_ms - start_ms) // 60_000 + 1
        return [
            spot.SpotBar(symbol, start_ms // 1000 + i * 60, "1", "1", "1", "1", "1")
            for i in range(n)
        ]

    db = tmp_path / "ok.db"
    assert (
        spot.load_symbol(db, "BTCUSDT", 0, 180, dry_run=True, fetch=good, sleep=lambda s: None) == 0
    )
    assert not db.exists()  # dry-run не трогает файловую систему
    assert spot.load_symbol(db, "BTCUSDT", 0, 180, fetch=good, sleep=lambda s: None) == 0
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM spot_1m").fetchone()[0] == 3
    finally:
        conn.close()


def test_cli_window_guard(spot: ModuleType, tmp_path: Path, capsys) -> None:
    code = spot.main(
        ["--db", str(tmp_path / "x.db"), "--symbol", "BTCUSDT", "--start", "0", "--end", "30"]
    )
    assert code == 2
    code = spot.main(
        ["--db", str(tmp_path / "x.db"), "--symbol", "BTCUSDT", "--start", "1", "--end", "601"]
    )
    assert code == 2
    assert "ОКНО" in capsys.readouterr().out
