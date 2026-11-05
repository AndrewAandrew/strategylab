"""Тесты CLI-раннера Series V на grid.py (scripts/deviation/grid.py, amend §1.3).

V-2-SIM AMENDMENT добавил ровно три вещи: `--regime` (дефолт «а», choices а/в/г —
«б» невалиден), расширенные choices `--exit-mode` (пять режимов) и
`--holdout-ceiling` (дефолт 1793577600 — E-7 закон нетронут, V-7 передаёт
1791849600). Артефакты самописываются режимом: колонка `regime` ДОБАВЛЕНА В КОНЕЦ
(header/строки — позиции E-колонок не едут), `regime=` в meta-строке `grid:` и в
report-строке `# grid:`; `params.regime` входит в базис `deviation_version`
(осознанная метка версии, числа не меняются). Гейты HOLDOUT/WINDOW/PARAM и
one-shot-дисциплина (HOLDOUT_ARMED до чтения БД) — без изменений.

Модуль скрипта грузится по пути файла (приём tests/test_deviation_grid.py), база —
tmp sqlite с реальным DDL, 1m-синтетика 91ч с одним ложным пробоем (h68).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from strategylab.data.schema import SCHEMA, connect

_GRID = Path(__file__).resolve().parent.parent / "scripts" / "deviation" / "grid.py"
SYMBOL = "ETHUSDT"
END = 91 * 3600
HOLDOUT_FLOOR = 1790294400
E7_CEILING = 1793577600
V7_CEILING = 1791849600
STEM = f"dev_V-tst_{SYMBOL}_0_{END}"

# Замороженные позиции E-колонок (amend §1.3: regime — только добавка в конец).
E_CSV_HEAD = (
    "fork",
    "deviation_version",
    "symbol",
    "cell",
    "start_ts",
    "end_ts",
    "ctx_hours",
    "zone_tf_secs",
    "touches",
    "tol",
    "depth",
    "conf_min",
    "vol_filter",
    "exit_mode",
    "capital",
    "taker_fee",
    "slippage",
    "zones",
    "events",
    "rounds",
    "n_stops",
    "n_takes",
    "n_tb",
    "n_forced",
    "price_gross",
    "friction",
    "net_pnl",
    "equity",
    "annualized_pct",
    "flat_fold",
)


def _load_grid() -> ModuleType:
    cached = sys.modules.get("deviation_v_grid_under_test")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("deviation_v_grid_under_test", _GRID)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["deviation_v_grid_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def grid() -> ModuleType:
    return _load_grid()


def make_db(tmp_path: Path) -> Path:
    """91 1h-час из 1m-баров: касания h12/h52 (S@99, R@101), ложный пробой h68."""
    db = tmp_path / "dev_v.db"
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
    return make_db(tmp_path_factory.mktemp("devvdb"))


def _argv(
    db: Path,
    out: Path,
    *,
    end: int = END,
    regime: str | None = None,
    exit_mode: str = "V1_48",
    ceiling: int | None = None,
    holdout_ok: bool = False,
) -> list[str]:
    argv = [
        "--db", str(db),
        "--symbol", SYMBOL,
        "--start", "0",
        "--end", str(end),
        "--cell", "V-tst",
        "--tol", "0.005",
        "--depth", "0.003",
        "--conf", "0",
        "--vol-filter", "1",
        "--exit-mode", exit_mode,
        "--out", str(out),
    ]
    if regime is not None:
        argv += ["--regime", regime]
    if ceiling is not None:
        argv += ["--holdout-ceiling", str(ceiling)]
    if holdout_ok:
        argv.append("--holdout-ok")
    return argv


def _read(out: Path) -> tuple[dict[str, str], list[str]]:
    """(CSV-строка по заголовку, все строки csv-артефакта вместе с метаблоком)."""
    lines = (out / f"{STEM}.csv").read_text(encoding="utf-8").splitlines()
    head = lines[-2].split(",")
    row = lines[-1].split(",")
    assert len(head) == len(row)
    return dict(zip(head, row, strict=True)), lines


def test_parser_regime_default_choices_and_rejects_b(grid: ModuleType) -> None:
    """--regime по умолчанию «а», принимает ровно а/в/г; «б»/латиница — ошибка
    argparse (exit 2). --exit-mode принимает пять режимов amend, V2_R3 — нет.
    --holdout-ceiling по умолчанию = E-7 потолок 1793577600."""
    base = [
        "--db", "x.db", "--symbol", "S", "--start", "0", "--end", "1",
        "--tol", "0.005", "--depth", "0.003", "--conf", "0", "--vol-filter", "1",
        "--exit-mode", "V1_48", "--out", "o",
    ]
    p = grid.build_parser()
    assert p.parse_args(base).regime == "а"
    for reg in ("а", "в", "г"):
        assert p.parse_args([*base, "--regime", reg]).regime == reg
    for mode in ("V1_48", "V2_R2", "V1_24", "V2_R1", "V2_R15"):
        assert p.parse_args([*base, "--exit-mode", mode]).exit_mode == mode
    assert p.parse_args(base).holdout_ceiling == E7_CEILING
    assert p.parse_args([*base, "--holdout-ceiling", str(V7_CEILING)]).holdout_ceiling == V7_CEILING
    for bad in ("б", "a", ""):
        with pytest.raises(SystemExit) as exc:
            p.parse_args([*base, "--regime", bad])
        assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        p.parse_args([*base, "--exit-mode", "V2_R3"])
    assert exc.value.code == 2


def test_cli_flags_reach_deviation_params(grid: ModuleType, monkeypatch) -> None:
    """Wiring: args.regime/args.exit_mode ложатся в DeviationParams до прогона
    (run_deviation шпионится, БД не читается — стопаемся сразу после сборки клетки)."""
    seen: list[object] = []

    class _Conn:
        def close(self) -> None:
            return None

    class _Store:
        def __init__(self, _conn: object, _symbol: str) -> None:
            return None

        def read_1m(self, _symbol: str, _start: int, _end: int) -> list[object]:
            return []

    def _spy(_bars: object, params: object, *_a: object, **_k: object) -> object:
        seen.append(params)
        raise RuntimeError("стоп после сборки клетки")

    monkeypatch.setattr(grid, "connect", lambda _db: _Conn())
    monkeypatch.setattr(grid, "CandleStore", _Store)
    monkeypatch.setattr(grid, "run_deviation", _spy)
    argv = _argv(Path("nowhere.db"), Path("unused-out"), regime="в", exit_mode="V2_R1")
    with pytest.raises(RuntimeError):
        grid.run(grid.build_parser().parse_args(argv))
    assert len(seen) == 1
    assert seen[0].regime == "в" and seen[0].exit_mode == "V2_R1"


def test_cli_regime_flows_into_artifacts(tmp_path: Path, db: Path, grid: ModuleType) -> None:
    """--regime «в» самоописывается в артефактах: последняя колонка `regime` в csv,
    `regime=в` в meta-строке grid: и в report `# grid:`; дефолтный прогон — «а»."""
    out_v = tmp_path / "v"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, out_v, regime="в", exit_mode="V1_24"))
    assert exc.value.code == 0
    row_v, csv_lines = _read(out_v)
    assert row_v["regime"] == "в" and row_v["exit_mode"] == "V1_24"
    assert any(line.startswith("grid:") and "regime=в" in line for line in csv_lines)
    report_v = (out_v / f"{STEM}_report_p.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("# grid:") and "regime=в" in line for line in report_v)
    journal = (out_v / f"{STEM}_journal_p.csv").read_text(encoding="utf-8").splitlines()
    assert [line.split(",")[1] for line in journal[1:]] == ["entry", "exit"]

    out_a = tmp_path / "a"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, out_a))
    assert exc.value.code == 0
    row_a, lines_a = _read(out_a)
    assert row_a["regime"] == "а"
    assert any(line.startswith("grid:") and "regime=а" in line for line in lines_a)


def test_cli_csv_columns_append_regime_at_end(grid: ModuleType, tmp_path: Path, db: Path) -> None:
    """Позиции E-колонок не тронуты, regime — последняя колонка; header/строка равной
    длины (старые потребители артефактов читают прежние индексы)."""
    assert tuple(grid._CSV_COLUMNS[: len(E_CSV_HEAD)]) == E_CSV_HEAD
    assert grid._CSV_COLUMNS[-1] == "regime"
    out = tmp_path / "cols"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, out))
    assert exc.value.code == 0
    lines = (out / f"{STEM}.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines[-2].split(",")) == len(grid._CSV_COLUMNS) == len(lines[-1].split(","))


def test_cli_version_basis_includes_regime(tmp_path: Path, db: Path, grid: ModuleType) -> None:
    """deviation_version меняется от regime (метка amend §1.2), а ЧИСЛА клетки на этой
    синтетике (круги/события/net) в «а» и «в» одинаковы: единственное событие свежее 30d."""
    versions: dict[str, str] = {}
    rows: dict[str, dict[str, str]] = {}
    for reg in ("а", "в"):
        out = tmp_path / f"cell_{reg}"
        with pytest.raises(SystemExit) as exc:
            grid.main(_argv(db, out, regime=reg))
        assert exc.value.code == 0
        row, _ = _read(out)
        versions[reg] = row["deviation_version"]
        rows[reg] = row
    assert versions["а"] != versions["в"]
    assert rows["а"]["rounds"] == rows["в"]["rounds"] == "1"
    assert rows["а"]["events"] == rows["в"]["events"] == "1"
    assert rows["а"]["net_pnl"] == rows["в"]["net_pnl"]
    again = tmp_path / "again"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, again, regime="а"))
    assert exc.value.code == 0
    row_again, _ = _read(again)
    assert row_again["deviation_version"] == versions["а"]


def test_cli_holdout_guard_still_blocks_without_flag(
    tmp_path: Path, db: Path, grid: ModuleType, capsys, monkeypatch
) -> None:
    """Новые флаги не ослабляют R-next-2: end > floor без --holdout-ok — exit 2,
    HOLDOUT_GUARD, артефактов нет, БД не тронута; WARN не печатается."""

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("connect() вызван до отклонения холдаут-окна")

    monkeypatch.setattr(grid, "connect", _boom)
    out = tmp_path / "blocked"
    argv = _argv(db, out, end=HOLDOUT_FLOOR + 1, regime="в", ceiling=V7_CEILING)
    with pytest.raises(SystemExit) as exc:
        grid.main(argv)
    assert exc.value.code == 2
    text = capsys.readouterr().out
    assert "HOLDOUT_GUARD" in text and "WARN" not in text
    assert not out.exists()


def test_cli_custom_holdout_ceiling_warns_only_when_exceeded(
    tmp_path: Path, db: Path, grid: ModuleType, capsys
) -> None:
    """V-7: --holdout-ceiling 1791849600 — WARN ровно при превышении переданного
    потолка; на самом потолке — без WARN; вооружённое чтение по-прежнему exit 0."""
    over = tmp_path / "over"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, over, end=V7_CEILING + 1, ceiling=V7_CEILING, holdout_ok=True))
    assert exc.value.code == 0
    text_over = capsys.readouterr().out
    assert "HOLDOUT_ARMED" in text_over and "WARN" in text_over

    exact = tmp_path / "exact"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, exact, end=V7_CEILING, ceiling=V7_CEILING, holdout_ok=True))
    assert exc.value.code == 0
    text_exact = capsys.readouterr().out
    assert "HOLDOUT_ARMED" in text_exact and "WARN" not in text_exact


def test_cli_default_ceiling_is_e7_unchanged(
    tmp_path: Path, db: Path, grid: ModuleType, capsys
) -> None:
    """E-7 закон нетронут: без --holdout-ceiling потолок 1793577600 — на нём WARN нет,
    на +1 есть."""
    exact = tmp_path / "e7_exact"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, exact, end=E7_CEILING, holdout_ok=True))
    assert exc.value.code == 0
    text_exact = capsys.readouterr().out
    assert "HOLDOUT_ARMED" in text_exact and "WARN" not in text_exact

    over = tmp_path / "e7_over"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, over, end=E7_CEILING + 1, holdout_ok=True))
    assert exc.value.code == 0
    assert "WARN" in capsys.readouterr().out


def test_cli_regime_g_v1_24_run_end_to_end(tmp_path: Path, db: Path, grid: ModuleType) -> None:
    """Боевая клетка V-c1 (regime=г, exit V1_24) через CLI: фолд 91ч короче time-boxа
    (69+24 > 90) => форс-выход; regime и exit видны в csv, journal 2 строки."""
    out = tmp_path / "c1"
    with pytest.raises(SystemExit) as exc:
        grid.main(_argv(db, out, regime="г", exit_mode="V1_24"))
    assert exc.value.code == 0
    row, _ = _read(out)
    assert row["exit_mode"] == "V1_24" and row["regime"] == "г"
    assert row["rounds"] == "1" and row["n_forced"] == "1" and row["n_tb"] == "0"
    journal = (out / f"{STEM}_journal_p.csv").read_text(encoding="utf-8").splitlines()
    assert len(journal) == 3  # header + entry + exit
