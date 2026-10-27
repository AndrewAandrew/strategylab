"""Смоук-тест скрипта развёртки: маленькая база, две комбинации, проверка строк CSV.

Скрипт лежит вне пакета (`scripts/` — не пакет), поэтому грузится по пути
файла, а не импортом пакета. Сеть не нужна: база собирается в tmp.
"""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Side
from strategylab.core.bars import Bar, Interval
from strategylab.core.metrics import Metrics
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, assign_zone
from strategylab.report.report import Report, build_report
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.two_sided_momentum import TwoSidedMomentumStrategy
from tests.test_metrics import make_fill

SYMBOL = "BTCUSDT"
START_TS = 1717200000  # 2024-06-01T00:00:00Z
WARMUP = 1529  # прогрев 5H при коротких длинах индикаторов
TAIL = 30
END_TS = START_TS + 60 * (WARMUP + 1 + TAIL)
PRICE = Decimal("100")
SPAN = Decimal("0.5")
DIP_LOW = Decimal("96.5")

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sweep.py"

CSV_HEADER = (
    "spec_version,adx_len,ema_len,atr_len_regime,atr_len_exit,exit_mult,"
    "trades,wins,net_pnl,max_drawdown,profit_factor,sharpe,"
    "long_trades,long_net_pnl,long_profit_factor,short_trades,short_net_pnl,short_profit_factor"
)

EXIT_MULT_COLUMN = 5


def _load_sweep() -> ModuleType:
    """Модуль скрипта по пути файла: scripts/ не пакет."""
    spec = importlib.util.spec_from_file_location("sweep_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flat_bars() -> list[Bar]:
    """Пила без направления (ADX держится нулём) и проколка вниз в конце окна."""
    bars = [
        Bar(
            ts=START_TS + 60 * index,
            open=PRICE,
            high=PRICE + SPAN,
            low=PRICE - SPAN,
            close=PRICE,
            volume=Decimal("1"),
        )
        for index in range(WARMUP)
    ]
    bars.append(
        Bar(
            ts=START_TS + 60 * WARMUP,
            open=PRICE,
            high=PRICE,
            low=DIP_LOW,
            close=PRICE,
            volume=Decimal("1"),
        )
    )
    bars += [
        Bar(
            ts=START_TS + 60 * index,
            open=PRICE,
            high=PRICE + SPAN,
            low=PRICE - SPAN,
            close=PRICE,
            volume=Decimal("1"),
        )
        for index in range(WARMUP + 1, WARMUP + 1 + TAIL)
    ]
    return bars


def make_db(path: Path) -> None:
    """База с серией flat_keltner: зона fit на всё окно прогона."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        assign_zone(conn, SYMBOL, START_TS, END_TS, Zone.FIT)
        CandleStore(conn, SYMBOL).write(_flat_bars())
        conn.commit()
    finally:
        conn.close()


def _argv(db: Path, exit_mult: str = "1.5,2") -> list[str]:
    """Аргументы развёртки: окно серии, короткие длины индикаторов, сетка exit_mult."""
    return [
        str(db),
        SYMBOL,
        str(START_TS),
        str(END_TS),
        "flat_keltner",
        "--param",
        "adx_len=3",
        "--param",
        "ema_len=3",
        "--param",
        "atr_len_regime=3",
        "--param",
        "atr_len_exit=2",
        "--param",
        f"exit_mult={exit_mult}",
    ]


def _rows(out: str) -> list[list[str]]:
    """Строки CSV под заголовком: список списков ячеек (до пустой строки итога)."""
    lines = out.splitlines()
    header = lines.index(CSV_HEADER)
    rows: list[list[str]] = []
    for line in lines[header + 1 :]:
        if not line:
            break
        rows.append(line.split(","))
    return rows


def test_sweep_prints_one_row_per_combination(tmp_path: Path, capsys) -> None:
    """Две комбинации — две строки: значения параметров и метрики пессимистичной вилки."""
    db = tmp_path / "flat.db"
    make_db(db)

    _load_sweep().main(_argv(db))

    out = capsys.readouterr().out
    assert CSV_HEADER in out.splitlines()
    rows = _rows(out)
    assert len(rows) == 2
    assert [row[EXIT_MULT_COLUMN] for row in rows] == ["1.5", "2"]
    assert all(len(row) == 18 for row in rows)
    # Сделка в каждой комбинации одна: сигнал на проколке, выход — по закрытию окна.
    assert [row[6] for row in rows] == ["1", "1"]
    # Размер разный (ширина стопа разная), значит и P&L разный.
    assert rows[0][8] != rows[1][8]
    assert "комбинаций: 2" in out


def test_sweep_skips_a_combination_rejected_by_the_strategy(tmp_path: Path, capsys) -> None:
    """Параметр, не прошедший валидацию стратегии, пропускается: развёртка не падает."""
    db = tmp_path / "flat.db"
    make_db(db)

    _load_sweep().main(_argv(db, "1.5,0"))

    captured = capsys.readouterr()
    assert "exit_mult=0" in captured.err
    assert "отклонено: 1" in captured.out
    assert "комбинаций: 2" in captured.out
    assert len(_rows(captured.out)) == 1

# Реестр касаний зон: инкремент по holdout при каждом прогоне, структура jsonl


def make_holdout_db(path: Path) -> None:
    """База с зоной holdout на всё окно: fit касаний не пишет, holdout пишет."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        assign_zone(conn, SYMBOL, START_TS, END_TS, Zone.HOLDOUT)
        CandleStore(conn, SYMBOL).write(_flat_bars())
        conn.commit()
    finally:
        conn.close()


def test_touch_registry_counts_holdout_peeks(tmp_path: Path, monkeypatch, capsys) -> None:
    """Прогон по holdout инкрементирует счётчик в шапке и пишет строку jsonl."""
    module = _load_sweep()
    registry = tmp_path / "touch_registry.json"
    monkeypatch.setattr(module, "TOUCH_REGISTRY", registry)
    db = tmp_path / "holdout.db"
    make_holdout_db(db)

    module.main(_argv(db, "2"))
    assert "touches: holdout=1, live=0" in capsys.readouterr().out

    module.main(_argv(db, "2"))
    assert "touches: holdout=2, live=0" in capsys.readouterr().out

    events = [json.loads(line) for line in registry.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2
    assert all(
        set(event) == {"spec_version", "window_role", "symbol", "iso_ts"} for event in events
    )
    assert all(event["window_role"] == "holdout" for event in events)
    assert all(event["symbol"] == SYMBOL for event in events)


def test_touch_registry_silent_on_fit_only_window(tmp_path: Path, monkeypatch, capsys) -> None:
    """Окно в fit зоне касаний не пишет и раздутый счётчик не показывает."""
    module = _load_sweep()
    registry = tmp_path / "touch_registry.json"
    monkeypatch.setattr(module, "TOUCH_REGISTRY", registry)
    db = tmp_path / "flat.db"
    make_db(db)

    module.main(_argv(db))
    out = capsys.readouterr().out
    assert "touches: holdout=0, live=0" in out
    assert not registry.exists()


# Реестр и стороны в CSV: дефолты из конструктора и шесть колонок SideMetrics


def test_sweep_defaults_take_constructor_values() -> None:
    """Гейт «≤3 свободных параметра»: ровно donch_len/atr_mult/bias_len из конструктора."""
    sweep = _load_sweep()
    assert sweep._defaults(TwoSidedMomentumStrategy) == {
        "donch_len": Decimal("48"),
        "atr_mult": Decimal("1.5"),
        "bias_len": Decimal("48"),
    }


def report_with(metrics: Metrics) -> Report:
    """Отчёт с заданными метриками: тот же idiom build_report, что в test_report.py."""
    return build_report(
        spec=StrategySpec(
            name="two_sided_momentum",
            symbol=SYMBOL,
            interval=Interval.MINUTE,
            start_ts=START_TS,
            end_ts=END_TS,
            params={"donch_len": Decimal("48")},
            rules_ref="strategydocs/spec_01_two_sided_momentum_20261022.md",
        ),
        zones_used=(Zone.FIT,),
        costs=CostModel(),
        trials=1,
        intrabar=IntrabarOrder.PESSIMISTIC,
        metrics=metrics,
    )


def test_sweep_row_includes_side_columns() -> None:
    sweep = _load_sweep()
    metrics = Metrics(equity=Decimal("10000"))
    fill = make_fill()
    for pnl in (Decimal("-20"), Decimal("-20")):
        metrics.record(fill, pnl, Side.BUY)
    metrics.record(fill, Decimal("100"), Side.SELL)
    row = sweep._row(report_with(metrics), {"donch_len": Decimal("48")}, ["donch_len"])
    assert row.split(",")[-6:] == ["2", "-40", "0", "1", "100", "null"]
