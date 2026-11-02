"""Тесты CLI-раннера Series B carry (scripts/carry/grid.py): артефактный конвенс,
детерминизм байт-в-байт, HOLDOUT_GUARD (R-next-2), LIQUIDATED_RUN_DEAD без артефактов,
PARAM_GUARD гистерезиса. Модуль скрипта грузится по пути файла — приём как
tests/test_calib_harness.py::_load_harness; база — tmp sqlite с реальным DDL."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from strategylab.data.schema import SCHEMA, connect

_GRID = Path(__file__).resolve().parent.parent / "scripts" / "carry" / "grid.py"


def _load_grid() -> ModuleType:
    cached = sys.modules.get("carry_grid_under_test")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("carry_grid_under_test", _GRID)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["carry_grid_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def grid() -> ModuleType:
    return _load_grid()


FUNDING_DDL = """
CREATE TABLE funding_8h (
    symbol TEXT, ts INTEGER, rate TEXT, mark_price TEXT, rate_type TEXT,
    PRIMARY KEY (symbol, ts)
);
"""


def make_db(tmp_path: Path, *, adverse_high: str | None = None) -> Path:
    db = tmp_path / "test.db"
    conn = connect(db)
    conn.executescript(SCHEMA)
    conn.executescript(FUNDING_DDL)
    for ts in range(0, 600, 60):
        high = str(adverse_high or "100") if (adverse_high and ts == 240) else "100"
        conn.execute(
            "INSERT INTO bars_1m VALUES (?,?,?,?,?,?,?,?)",
            ("BTCUSDT", ts, "100", high, "100", "100", "1", None),
        )
    conn.execute("INSERT INTO funding_8h VALUES (?,?,?,?,?)", ("BTCUSDT", 0, "0.001", None, None))
    conn.execute(
        "INSERT INTO funding_8h VALUES (?,?,?,?,?)", ("BTCUSDT", 120, "0.0005", None, None)
    )
    conn.execute(
        "INSERT INTO funding_8h VALUES (?,?,?,?,?)", ("BTCUSDT", 240, "0.0000001", None, None)
    )
    conn.commit()
    conn.close()
    return db


def _argv(db: Path, out: Path) -> list[str]:
    return [
        "--db",
        str(db),
        "--symbol",
        "BTCUSDT",
        "--start",
        "0",
        "--end",
        "600",
        "--theta-entry",
        "0.00005",
        "--theta-exit",
        "0.00003",
        "--out",
        str(out),
    ]


def test_cli_artifacts_and_cash_flow(tmp_path: Path, grid: ModuleType, capsys) -> None:
    db = make_db(tmp_path)
    out = tmp_path / "cell"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, out))
    assert exc.value.code == 0
    files = sorted(p.name for p in out.iterdir())
    assert files == [
        "carry_BTCUSDT_0_600.csv",
        "carry_BTCUSDT_0_600_journal_p.csv",
        "carry_BTCUSDT_0_600_report_p.txt",
    ]
    csv_text = (out / "carry_BTCUSDT_0_600.csv").read_text(encoding="utf-8")
    assert csv_text.splitlines()[0] == "strategy: carry_funding"
    assert "fork,carry_version,symbol" in csv_text
    data_row = csv_text.splitlines()[-1].split(",")
    assert data_row[0] == "pessimistic"
    journal = (out / "carry_BTCUSDT_0_600_journal_p.csv").read_text(encoding="utf-8")
    kinds = [line.split(",")[1] for line in journal.splitlines()[1:]]
    # вход на баре 60; interior 120 и decision-такт 240 (перед выходом на 300)
    assert kinds == ["entry", "settle", "settle", "exit"]


def test_cli_determinism_byte_identical(tmp_path: Path, grid: ModuleType) -> None:
    db = make_db(tmp_path)
    hashes: list[str] = []
    for i in (1, 2):
        out = tmp_path / f"cell{i}"
        with pytest.raises(SystemExit) as exc:
            grid.main(_argv(db, out))
        assert exc.value.code == 0
        blob = b"".join(sorted(p.read_bytes() for p in out.iterdir()))
        hashes.append(hashlib.sha256(blob).hexdigest())
    assert hashes[0] == hashes[1]


def test_cli_liquidation_no_artifacts(tmp_path: Path, grid: ModuleType, capsys) -> None:
    db = make_db(tmp_path, adverse_high="210")
    out = tmp_path / "cell"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, out))
    assert exc.value.code == 4
    assert not out.exists() or not list(out.iterdir())
    assert "LIQUIDATED_RUN_DEAD" in capsys.readouterr().out


def test_cli_holdout_guard(tmp_path: Path, grid: ModuleType, capsys) -> None:
    db = make_db(tmp_path)
    out = tmp_path / "cell"
    argv = [
        "--db",
        str(db),
        "--symbol",
        "BTCUSDT",
        "--start",
        "1790200000",
        "--end",
        "1790294401",
        "--theta-entry",
        "0.00005",
        "--theta-exit",
        "0.00003",
        "--out",
        str(out),
    ]
    with pytest.raises(SystemExit) as exc:
        grid.main(argv)
    assert exc.value.code == 2
    assert "HOLDOUT_GUARD" in capsys.readouterr().out
    assert not out.exists()


def test_cli_param_guard_hysteresis(tmp_path: Path, grid: ModuleType, capsys) -> None:
    db = make_db(tmp_path)
    out = tmp_path / "cell"
    argv = _argv(db, out)
    argv[argv.index("--theta-exit") + 1] = "0.00005"
    with pytest.raises(SystemExit) as exc:
        grid.main(argv)
    assert exc.value.code == 2
    assert "PARAM_GUARD" in capsys.readouterr().out
