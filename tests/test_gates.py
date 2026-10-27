"""Тесты `scripts/gates.py`: пять гейтов спеки 01 по CSV-развёрткам.

Скрипты вне пакета (`scripts/` — не пакет) загружаются через importlib, как в
`test_sweep.py`; фикстуры — синтетические CSV в tmp_path, база не открывается.

Проверяемые ловушки из брифа задачи 12:
- `null` в `profit_factor` ранжируется ВЫШЕ любого числа (бесконечный PF),
  а не как ноль и не выбрасывается — иначе медиана считается неверно;
- гейт 1 различает исходы `<30` («расширять спек») и `30–99` («не пройден»);
- дефолтный ряд опознаётся по значениям трёх именных колонок, порядок строк
  в CSV не влияет;
- парсер читает BOM, обрывает таблицу на первой пустой строке (хвост
  `комбинаций:` / `отклонено:` не становится строкой сетки);
- гейт 5 помечает `одно-система` и не переворачивает строку вердикта.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gates.py"


def _load_gates() -> ModuleType:
    """Загрузка скрипта вне пакета: scripts/ — не пакет.

    Модуль регистрируется в `sys.modules` до исполнения: `@dataclass` с
    аннотациями-строками (`from __future__ import annotations`) резолвит их
    через `sys.modules[cls.__module__]`, а importlib без регистрации оставляет
    там None.
    """
    spec = importlib.util.spec_from_file_location("gates_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gates = _load_gates()

CSV_HEADER = (
    "spec_version,donch_len,atr_mult,bias_len,trades,wins,net_pnl,max_drawdown,"
    "profit_factor,sharpe,long_trades,long_net_pnl,long_profit_factor,"
    "short_trades,short_net_pnl,short_profit_factor"
)

# Порядок как у sweep.py: имена в порядке --param, значения по возрастанию.
COMBOS = [
    (donch, atr, bias)
    for donch in ("24", "48", "96")
    for atr in ("1.0", "1.5", "2.0")
    for bias in ("0", "48")
]
DEFAULT_CELLS = ("48", "1.5", "48")
DEFAULT_INDEX = COMBOS.index(DEFAULT_CELLS)  # 9 — не первый и не последний


def _header_lines(*, pessimistic: bool = True) -> list[str]:
    """Десять строк шапки развёртки в формате `ключ: значение`."""
    lines = [
        "strategy: two_sided_momentum",
        "symbol: TESTUSDT",
        "window: [1761523200, 1790294400)",
        "capital: 10000",
        "grid: donch_len=24,48,96; atr_mult=1.0,1.5,2.0; bias_len=0,48",
        "spec_version (база): v-base",
        "zones_used: fit",
        "costs: taker_fee=0.0006 maker_fee=0.0002 slippage=0.0005 leverage=1",
        "touches: holdout=0, live=0",
    ]
    lines.append("intrabar: PESSIMISTIC" if pessimistic else "intrabar: OPTIMISTIC")
    return lines


def _cells(
    combo: tuple[str, str, str],
    **overrides: str,
) -> list[str]:
    """Строка CSV: базовые метрики «всё проходит», перекрытия — именованные."""
    base = {
        "trades": "100",
        "wins": "50",
        "net_pnl": "500",
        "max_drawdown": "50",
        "profit_factor": "1.5",
        "sharpe": "1.0",
        "long_trades": "60",
        "long_net_pnl": "300",
        "long_profit_factor": "1.4",
        "short_trades": "40",
        "short_net_pnl": "200",
        "short_profit_factor": "1.6",
    }
    base.update(overrides)
    donch, atr, bias = combo
    version = f"v-{donch}-{atr}-{bias}"
    return [
        version,
        donch,
        atr,
        bias,
        base["trades"],
        base["wins"],
        base["net_pnl"],
        base["max_drawdown"],
        base["profit_factor"],
        base["sharpe"],
        base["long_trades"],
        base["long_net_pnl"],
        base["long_profit_factor"],
        base["short_trades"],
        base["short_net_pnl"],
        base["short_profit_factor"],
    ]


def _grid_rows(default_overrides: dict[str, str] | None = None) -> list[list[str]]:
    """18 строк сетки: перекрытия применяются только к ряду дефолтной комбинации."""
    rows = []
    for index, combo in enumerate(COMBOS):
        overrides = default_overrides if index == DEFAULT_INDEX else None
        rows.append(_cells(combo, **(overrides or {})))
    return rows


def _write_csv(
    path: Path,
    rows: list[list[str]],
    *,
    pessimistic: bool = True,
    bom: bool = False,
) -> Path:
    """Файл как печатает sweep.py: шапка, заголовок, строки, пустая строка, хвост."""
    lines = _header_lines(pessimistic=pessimistic)
    lines.append(CSV_HEADER)
    lines.extend(",".join(row) for row in rows)
    lines.append("")
    lines.append(f"комбинаций: {len(rows) + 9}")
    lines.append("отклонено: 9")
    lines.append("elapsed: 21.00s")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
    return path


def _fold(tmp_path: Path, name: str, **overrides: str) -> Path:
    """CSV фолда: одна строка дефолтной комбинации с перекрытыми метриками."""
    return _write_csv(tmp_path / name, [_cells(DEFAULT_CELLS, **overrides)])


def _passing_fixture(tmp_path: Path) -> tuple[Path, Path, list[Path]]:
    """BTC/ETH-сетки и три фолда, на которых все гейты 1–4 проходят."""
    btc = _write_csv(tmp_path / "btc.csv", _grid_rows())
    eth = _write_csv(tmp_path / "eth.csv", _grid_rows())
    folds = [_fold(tmp_path, f"fold{index}.csv") for index in (1, 2, 3)]
    return btc, eth, folds


def _line(lines: list[str], prefix: str) -> str:
    """Первая строка вывода с данным началом: таблица стабильна по префиксам."""
    return next(line for line in lines if line.startswith(prefix))


def test_parse_sweep_reads_table_stops_at_empty_line(tmp_path: Path) -> None:
    """18 строк таблицы; хвост после пустой строки строками не считается."""
    path = _write_csv(tmp_path / "grid.csv", _grid_rows())
    rows = gates.parse_sweep(path)
    assert len(rows) == 18
    first = rows[0]
    assert first.params == {
        "donch_len": Decimal("24"),
        "atr_mult": Decimal("1.0"),
        "bias_len": Decimal("0"),
    }
    assert first.net_pnl == Decimal("500")
    assert first.profit_factor == Decimal("1.5")
    assert first.long_trades == 60
    assert first.short_profit_factor == Decimal("1.6")


def test_parse_sweep_null_profit_factor_is_none_not_zero(tmp_path: Path) -> None:
    """Маркер `null` читается как None: ноль обвинял бы безубыточную комбинацию."""
    rows = [_cells(combo, profit_factor="null") for combo in COMBOS]
    path = _write_csv(tmp_path / "grid.csv", rows)
    parsed = gates.parse_sweep(path)
    assert all(row.profit_factor is None for row in parsed)


def test_parse_sweep_handles_bom(tmp_path: Path) -> None:
    """Файл с BOM (кто-то перенаправил через `>`) читается тем же парсером."""
    path = _write_csv(tmp_path / "grid.csv", _grid_rows(), bom=True)
    assert len(gates.parse_sweep(path)) == 18


def test_parse_sweep_requires_pessimistic_header(tmp_path: Path) -> None:
    """Числа берутся из пессимистичной развёртки: OPTIMISTIC в шапке — ошибка."""
    path = _write_csv(tmp_path / "grid.csv", _grid_rows(), pessimistic=False)
    try:
        gates.parse_sweep(path)
    except ValueError as error:
        assert "PESSIMISTIC" in str(error)
    else:
        raise AssertionError("ожидался ValueError про пессимистичную шапку")


def test_median_finite_odd_and_even() -> None:
    """Медиана: нечётная — середина; чётная — среднее двух средних."""
    values = [Decimal(x) for x in ("1", "2", "3")]
    assert gates.median_pf(values) == Decimal("2")
    values = [Decimal(x) for x in ("1", "2", "3", "4")]
    assert gates.median_pf(values) == Decimal("2.5")
    try:
        gates.median_pf([])
    except ValueError:
        pass
    else:
        raise AssertionError("ожидался ValueError на пустом списке")


def test_median_null_ranks_above_every_number() -> None:
    """null = бесконечность: ранжируется выше чисел, не ноль и не выброс."""
    one = Decimal("1")
    two = Decimal("2")
    # Три ряда: [1, 2, ∞] — середина конечна.
    assert gates.median_pf([one, two, None]) == two
    # Четыре ряда: [1, 2, ∞, ∞] — правая средняя бесконечна → медиана ∞.
    assert gates.median_pf([one, two, None, None]) is None
    # Один ряд: [∞] → медиана ∞ (при «ноле» было бы 0, при «выбросе» — пусто).
    assert gates.median_pf([None]) is None
    # [1, ∞] — среднее конечного и бесконечного ряда бесконечно.
    assert gates.median_pf([one, None]) is None
    # None не мешает конечной середине: [1, 2, 3, ∞] → (2+3)/2 = 2.5.
    assert gates.median_pf([one, two, Decimal("3"), None]) == Decimal("2.5")


def test_evaluate_all_pass_table_and_verdict(tmp_path: Path) -> None:
    """Зелёный сценарий: пять строк гейтов, строка null-счёта, вердикт пройдено."""
    btc, eth, folds = _passing_fixture(tmp_path)
    lines = gates.evaluate(btc, eth, folds)
    for prefix in ("гейт 1", "гейт 2", "гейт 3", "гейт 4", "гейт 5"):
        assert _line(lines, prefix)
    assert _line(lines, "null-рядов").count("BTC=0") == 1
    assert lines[-1] == "ГЕЙТЫ: пройдены"


def test_gate1_exactly_100_passes_99_fails(tmp_path: Path) -> None:
    """Границы гейта 1: 100 (60+40) проходит, 99 (59+40) — «не пройден»."""
    btc, eth, folds = _passing_fixture(tmp_path)
    lines = gates.evaluate(btc, eth, folds)
    assert "пройден" in _line(lines, "гейт 1")
    rows = _grid_rows({"long_trades": "59"})
    btc = _write_csv(tmp_path / "btc99.csv", rows)
    lines = gates.evaluate(btc, eth, folds)
    line = _line(lines, "гейт 1")
    assert "не пройден" in line
    assert "расширять спек" not in line
    assert lines[-1] == "ГЕЙТЫ: не пройдены"


def test_gate1_below_30_prints_expand(tmp_path: Path) -> None:
    """Третий pre-registered исход гейта 1: <30 — «расширять спек», ровно 30 — нет."""
    _, eth, folds = _passing_fixture(tmp_path)
    low = _grid_rows({"long_trades": "16", "short_trades": "13"})  # 29
    mid = _grid_rows({"long_trades": "17", "short_trades": "13"})  # 30
    btc_low = _write_csv(tmp_path / "btc29.csv", low)
    btc_mid = _write_csv(tmp_path / "btc30.csv", mid)
    line = _line(gates.evaluate(btc_low, eth, folds), "гейт 1")
    assert "расширять спек" in line
    assert "не пройден" not in line
    line = _line(gates.evaluate(btc_mid, eth, folds), "гейт 1")
    assert "не пройден" in line
    assert "расширять спек" not in line


def test_gate2_nulls_rank_above_makes_median_infinite(tmp_path: Path) -> None:
    """9 рядов по 0.5 + 9 null: медиана ∞ и гейт пройден; счётчик null напечатан."""
    _, eth, folds = _passing_fixture(tmp_path)
    rows = [
        _cells(combo, profit_factor="null" if index < 9 else "0.5")
        for index, combo in enumerate(COMBOS)
    ]
    btc = _write_csv(tmp_path / "btc_nulls.csv", rows)
    lines = gates.evaluate(btc, eth, folds)
    line = _line(lines, "гейт 2")
    assert "∞" in line
    assert "пройден" in line
    assert _line(lines, "null-рядов").startswith("null-рядов: BTC=9, ETH=0")
    # null-ряды сохранены в числе строк сетки, а не выброшены из медианы.
    assert "BTC=18" in lines[0]


def test_gate2_median_below_threshold_fails(tmp_path: Path) -> None:
    """Все 18 PF = 1.0 → медиана 1.0 < 1.15 → «не пройден»."""
    _, eth, folds = _passing_fixture(tmp_path)
    rows = [_cells(combo, profit_factor="1.0") for combo in COMBOS]
    btc = _write_csv(tmp_path / "btc_low.csv", rows)
    lines = gates.evaluate(btc, eth, folds)
    assert "не пройден" in _line(lines, "гейт 2")
    assert lines[-1] == "ГЕЙТЫ: не пройдены"


def test_default_row_found_by_columns_not_index(tmp_path: Path) -> None:
    """Дефолт ищется по трём колонкам: сдвиг списка строк не меняет гейт 3."""
    _, eth, folds = _passing_fixture(tmp_path)
    rows = _grid_rows({"profit_factor": "1.2"})
    rotated = rows[7:] + rows[:7]  # дефолтный ряд уехал с позиции 9
    btc = _write_csv(tmp_path / "btc_rot.csv", rotated)
    lines = gates.evaluate(btc, eth, folds)
    assert "1.2" in _line(lines, "гейт 3")
    assert "не пройден" in _line(lines, "гейт 3")
    assert "1.5" in _line(lines, "гейт 2")


def test_gate3_infinite_default_pf_passes(tmp_path: Path) -> None:
    """Гейт 3: null PF дефолта = бесконечное преимущество → пройден, напечатан ∞."""
    _, eth, folds = _passing_fixture(tmp_path)
    rows = _grid_rows({"profit_factor": "null"})
    btc = _write_csv(tmp_path / "btc_inf.csv", rows)
    lines = gates.evaluate(btc, eth, folds)
    line = _line(lines, "гейт 3")
    assert "∞" in line
    assert "пройден" in line


def test_missing_or_duplicate_default_row_raises(tmp_path: Path) -> None:
    """Дефолтный ряд обязан быть ровно один: 0 или 2 совпадения — ошибка."""
    without = [row for row in _grid_rows() if tuple(row[1:4]) != DEFAULT_CELLS]
    path = _write_csv(tmp_path / "grid.csv", without)
    try:
        gates.find_default_row(list(gates.parse_sweep(path)), "тест")
    except ValueError as error:
        assert "нет" in str(error)
    else:
        raise AssertionError("ожидался ValueError про отсутствие дефолта")
    dup_rows = _grid_rows() + [_cells(DEFAULT_CELLS)]
    dup_rows[-1][0] = "v-duplicate"
    path = _write_csv(tmp_path / "dup.csv", dup_rows)
    try:
        gates.find_default_row(list(gates.parse_sweep(path)), "тест")
    except ValueError as error:
        assert "несколько" in str(error)
    else:
        raise AssertionError("ожидался ValueError про дубликат дефолта")


def test_gate4_two_of_three_folds_positive(tmp_path: Path) -> None:
    """Гейт 4: 2 из 3 фолдов с net_pnl > 0 проходят, 1 из 3 — нет."""
    btc, eth, _ = _passing_fixture(tmp_path)
    folds = [
        _fold(tmp_path, "fold1.csv", net_pnl="500"),
        _fold(tmp_path, "fold2.csv", net_pnl="-100"),
        _fold(tmp_path, "fold3.csv", net_pnl="1"),
    ]
    lines = gates.evaluate(btc, eth, folds)
    line = _line(lines, "гейт 4")
    assert "пройдено" in line
    assert "2 из 3" in line
    bad_folds = [
        _fold(tmp_path, "bad1.csv", net_pnl="-1"),
        _fold(tmp_path, "bad2.csv", net_pnl="0"),
        _fold(tmp_path, "bad3.csv", net_pnl="500"),
    ]
    lines = gates.evaluate(btc, eth, bad_folds)
    assert "не пройдено" in _line(lines, "гейт 4")
    assert lines[-1] == "ГЕЙТЫ: не пройдены"


def test_gate4_fold_row_found_by_columns_among_several(tmp_path: Path) -> None:
    """В CSV фолда может быть несколько строк: берётся дефолт по колонкам."""
    btc, eth, _ = _passing_fixture(tmp_path)
    fold_rows = [
        _cells(("24", "1.0", "0"), net_pnl="-999"),  # не дефолт, первым рядом
        _cells(DEFAULT_CELLS, net_pnl="500"),
    ]
    fold = _write_csv(tmp_path / "fold.csv", fold_rows)
    lines = gates.evaluate(btc, eth, [fold, fold, fold])
    assert "пройдено" in _line(lines, "гейт 4")
    assert "-999" not in _line(lines, "гейт 4")


def test_gate5_zero_side_not_published_verdict_unchanged(tmp_path: Path) -> None:
    """Гейт 5: trades одной стороны 0 → «не опубликована», вердикт гейтов 1–4 не тронут."""
    _, eth, folds = _passing_fixture(tmp_path)
    # Сумма сторон остаётся 100: проваливается только публикация гейта 5.
    btc = _write_csv(tmp_path / "btc0.csv", _grid_rows({"long_trades": "0", "short_trades": "100"}))
    lines = gates.evaluate(btc, eth, folds)
    assert "не опубликована" in _line(lines, "гейт 5")
    assert lines[-1] == "ГЕЙТЫ: пройдены"


def test_gate5_one_system_annotation_never_flips_verdict(tmp_path: Path) -> None:
    """Пометка `одно-система` при провале одной стороны — вердикт остаётся «пройдены»."""
    _, eth, folds = _passing_fixture(tmp_path)
    btc = _write_csv(
        tmp_path / "btc.csv",
        _grid_rows({"long_net_pnl": "-100", "short_net_pnl": "600"}),
    )
    lines = gates.evaluate(btc, eth, folds)
    line = _line(lines, "гейт 5")
    assert "одно-система" in line
    assert "BTC" in line
    assert lines[-1] == "ГЕЙТЫ: пройдены"


def test_cli_main_prints_table_and_verdict(tmp_path, capsys) -> None:
    """CLI-контракт: --btc/--eth/--folds печатают таблицу и строку вердикта."""
    btc, eth, folds = _passing_fixture(tmp_path)
    gates.main(
        [
            "--btc",
            str(btc),
            "--eth",
            str(eth),
            "--folds",
            *[str(path) for path in folds],
        ]
    )
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "ГЕЙТЫ: пройдены"
    assert any(line.startswith("гейт 5") for line in out)
