"""Тесты CLI-раннера Series E deviation (scripts/deviation/grid.py): 3-артефактный
конвенс серии A, детерминизм байт-в-байт, HOLDOUT_GUARD (R-next-2), WINDOW/PARAM
guards. Модуль скрипта грузится по пути файла (приём tests/test_carry_grid.py);
база — tmp sqlite с реальным DDL, 1m-синтетика 91ч с одним ложным пробоем."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from strategylab.data.schema import SCHEMA, connect

_GRID = Path(__file__).resolve().parent.parent / "scripts" / "deviation" / "grid.py"
SYMBOL = "ETHUSDT"
END = 91 * 3600


def _load_grid() -> ModuleType:
    cached = sys.modules.get("deviation_grid_under_test")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("deviation_grid_under_test", _GRID)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["deviation_grid_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def grid() -> ModuleType:
    return _load_grid()


def make_db(tmp_path: Path) -> Path:
    """91 1h-час из 1m-баров: касания h12/h52 (S@99, R@101), ложный пробой h68."""
    db = tmp_path / "dev.db"
    conn = connect(db)
    conn.executescript(SCHEMA)
    rows: list[tuple[object, ...]] = []
    for ht in range(END // 3600):
        if ht in (12, 52):
            spec = ("100", "101.0", "99.0", "100")
        elif ht == 68:
            spec = ("99.8", "100.2", "98.4", "100.0")
        else:
            spec = ("100", "100.6", "99.5", "100")
        o, h, lo, c = spec
        for m in range(60):
            rows.append((SYMBOL, ht * 3600 + m * 60, o, h, lo, c, "1", None))
    conn.executemany("INSERT INTO bars_1m VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_db(tmp_path_factory.mktemp("devdb"))


def _argv(db: Path, out: Path) -> list[str]:
    return [
        "--db", str(db),
        "--symbol", SYMBOL,
        "--start", "0",
        "--end", str(END),
        "--cell", "E-tst",
        "--tol", "0.005",
        "--depth", "0.003",
        "--conf", "0",
        "--vol-filter", "1",
        "--exit-mode", "V1_48",
        "--out", str(out),
    ]


def test_cli_artifacts_and_cash_flow(tmp_path: Path, db: Path, grid: ModuleType, capsys) -> None:
    out = tmp_path / "cell"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, out))
    assert exc.value.code == 0
    stem = f"dev_E-tst_{SYMBOL}_0_{END}"
    files = sorted(p.name for p in out.iterdir())
    assert files == [f"{stem}.csv", f"{stem}_journal_p.csv", f"{stem}_report_p.txt"]
    csv_text = (out / f"{stem}.csv").read_text(encoding="utf-8")
    assert csv_text.splitlines()[0] == "strategy: deviation_zone_reversal"
    for name in files:
        assert b"\r" not in (out / name).read_bytes()  # LF-конвенция серии A
    data_row = csv_text.splitlines()[-1].split(",")
    assert data_row[0] == "pessimistic"
    assert re.fullmatch(r"[0-9a-f]{12}", data_row[1])
    journal = (out / f"{stem}_journal_p.csv").read_text(encoding="utf-8")
    kinds = [line.split(",")[1] for line in journal.splitlines()[1:]]
    assert kinds == ["entry", "exit"]
    # ручной net: вход open(h69)*1.0005, форс close(h90)*0.9995, fee 2*0.0006*capital
    entry = Decimal("100") * Decimal("1.0005")
    exit_fill = Decimal("100") * Decimal("0.9995")
    net = Decimal("10000") * (exit_fill / entry - 1) - 2 * Decimal("10000") * Decimal("0.0006")
    assert format(net, "f") in data_row
    assert "rounds: 1" in capsys.readouterr().out


def test_cli_determinism_byte_identical(tmp_path: Path, db: Path, grid: ModuleType) -> None:
    hashes: list[str] = []
    for i in (1, 2):
        out = tmp_path / f"cell{i}"
        with pytest.raises(SystemExit) as exc:
            grid.main(_argv(db, out))
        assert exc.value.code == 0
        blob = b"".join(sorted(p.read_bytes() for p in out.iterdir()))
        hashes.append(hashlib.sha256(blob).hexdigest())
    assert hashes[0] == hashes[1]


def test_cli_holdout_guard(tmp_path: Path, db: Path, grid: ModuleType, capsys) -> None:
    out = tmp_path / "cell"
    argv = _argv(db, out)
    argv[argv.index("--end") + 1] = "1790294401"
    with pytest.raises(SystemExit) as exc:
        grid.main(argv)
    assert exc.value.code == 2
    assert "HOLDOUT_GUARD" in capsys.readouterr().out
    assert not out.exists()


def test_cli_window_guard_degenerate(tmp_path: Path, db: Path, grid: ModuleType, capsys) -> None:
    out = tmp_path / "cell"
    argv = _argv(db, out)
    argv[argv.index("--start") + 1] = str(END)
    with pytest.raises(SystemExit) as exc:
        grid.main(argv)
    assert exc.value.code == 2
    assert "WINDOW_GUARD" in capsys.readouterr().out
    assert not out.exists()


def test_cli_param_guard_scan_depth(tmp_path: Path, db: Path, grid: ModuleType, capsys) -> None:
    """depth ниже замороженного порога скана (0.003) — клетка вне решётки: отказ,
    артефактов нет, БД не читается (guard до чтения)."""
    out = tmp_path / "cell"
    argv = _argv(db, out)
    argv[argv.index("--depth") + 1] = "0.001"
    with pytest.raises(SystemExit) as exc:
        grid.main(argv)
    assert exc.value.code == 2
    assert "PARAM_GUARD" in capsys.readouterr().out
    assert not out.exists()
