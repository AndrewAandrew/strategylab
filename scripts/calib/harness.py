"""Калибровочный харнесс спек spec_02–spec_05 (calibration suite).

Собирает `Engine + BrokerSimulator + TradeLog` напрямую — как `fork._run_once`,
но наружу отдаёт журнал сделок, снимок живой очереди брокера после последнего
бара и метрики обеих вилок. `run_fork` при этом не трогает: харнесс обязан
видеть `pending_orders`, чего `run_fork` не отдаёт (design §10). С фиксом C-3
снимок очереди всегда пуст (конец окна снимает её), а учёт конца окна несёт
счётчик `window_cancelled` — он уходит в учётную строку report ячейки.

CSV-конвенция (null-маркеры, grid-хелперы, формат чисел) наследуется от
scripts/sweep.py — модуль подгружается по пути файла, не копируется.

Прогон разрешён только окну, целиком лежащему в зоне fit:
`zones_touched == (Zone.FIT,)`; пустое множество зон — отказ так же (A9).
Реестр касаний (data/touch_registry.json) харнесс не читает и не пишет.
"""

# Импорт пакета strategylab стоит после sys.path — сознательно, как в scripts/sweep.py.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Order, OrderKind
from strategylab.broker.sim import BrokerSimulator, LiquidationError
from strategylab.core.bars import Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.core.trades import Trade, TradeLog
from strategylab.data.schema import connect
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, zones_touched
from strategylab.drivers.replay import ReplayDriver
from strategylab.report.report import build_report
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.examples import STRATEGY_CLASSES, build

DEFAULT_CAPITAL = Decimal("10000")
RULES_REF = "calibration-suite"
_TRIALS = 1
_DOUBLE_COSTS = CostModel(Decimal("0.0012"), Decimal("0.0004"), Decimal("0.0010"), Decimal("1"))

_INTERVALS: dict[str, Interval] = {
    "hour": Interval.HOUR,
    "15m": Interval.FIFTEEN_MIN,
    "5m": Interval.FIVE_MIN,
    "1m": Interval.MINUTE,
}

# фиксированный хвост схемы строки ячейки (design §10); params — до него
_METRIC_COLUMNS = (
    "trades",
    "wins",
    "win_rate",
    "net_pnl",
    "equity",
    "max_drawdown",
    "profit_factor",
    "sharpe",
    "pending_limit_buy",
    "pending_other",
)

_JOURNAL_HEADER = "spec_version,combination,entry_ts,entry_price,exit_ts,exit_price,pnl,exit_reason"

_SWEEP_PATH = _REPO_ROOT / "scripts" / "sweep.py"


def _load_sweep() -> Any:
    """Модуль скрипта по пути файла: scripts/ не пакет (приём tests/test_sweep.py)."""
    spec = importlib.util.spec_from_file_location("sweep_script", _SWEEP_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # регистрация до исполнения: dataclass(slots=True) пересобирает класс через sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SWEEP: Any = _load_sweep()


class OutsideFitZone(Exception):
    """Окно прогона выходит за пределы зоны fit (или вовсе без разметки)."""


@dataclass(frozen=True, slots=True)
class ForkSide:
    intrabar: IntrabarOrder
    metrics: Metrics
    trades: tuple[Trade, ...]
    pending_after: tuple[Order, ...]
    window_cancelled: int


@dataclass(frozen=True, slots=True)
class CellOutcome:
    spec_version: str
    zones: tuple[Zone, ...]
    sides: tuple[ForkSide, ForkSide]


def ensure_fit(zones: tuple[Zone, ...]) -> None:
    """Пропускаем только строго fit-окно: всё остальное (включая пустое) — отказ."""
    if zones != (Zone.FIT,):
        raise OutsideFitZone(f"прогон разрешён только при зонах == (fit,), получено {zones}")


def _make_spec(
    strategy: str,
    symbol: str,
    params: Mapping[str, Decimal],
    start_ts: int,
    end_ts: int,
    interval: Interval,
) -> StrategySpec:
    return StrategySpec(
        name=f"calib:{strategy}",
        symbol=symbol,
        interval=interval,
        start_ts=start_ts,
        end_ts=end_ts,
        params=dict(params),
        rules_ref=RULES_REF,
    )


def _run_side(
    store: CandleStore,
    spec: StrategySpec,
    strategy: str,
    params: Mapping[str, Decimal],
    costs: CostModel,
    intrabar: IntrabarOrder,
    capital: Decimal,
) -> ForkSide:
    feed = ReplayDriver(store, spec.symbol, spec.interval, spec.start_ts, spec.end_ts)
    broker = BrokerSimulator(costs, intrabar)
    log = TradeLog()
    metrics = Engine().run(
        feed, build(strategy, dict(params)), broker, Metrics(equity=capital), log
    )
    return ForkSide(
        intrabar, metrics, log.trades, tuple(broker.pending_orders), broker.window_cancelled
    )


def run_cell(
    db: Path,
    symbol: str,
    strategy: str,
    params: Mapping[str, Decimal],
    start_ts: int,
    end_ts: int,
    capital: Decimal = DEFAULT_CAPITAL,
    interval: Interval = Interval.HOUR,
    costs: CostModel | None = None,
) -> CellOutcome:
    """Обе вилки на одном окне: метрики, журнал сделок, финальная очередь брокера."""
    effective_costs = costs if costs is not None else CostModel()
    spec = _make_spec(strategy, symbol, params, start_ts, end_ts, interval)
    conn = connect(db)
    try:
        store = CandleStore(conn, symbol)
        zones = zones_touched(conn, symbol, start_ts, end_ts)
        ensure_fit(zones)
        pessimistic = _run_side(
            store, spec, strategy, params, effective_costs, IntrabarOrder.PESSIMISTIC, capital
        )
        optimistic = _run_side(
            store, spec, strategy, params, effective_costs, IntrabarOrder.OPTIMISTIC, capital
        )
    finally:
        conn.close()
    return CellOutcome(spec.version, zones, (pessimistic, optimistic))


def _pending_counts(pending: tuple[Order, ...]) -> tuple[int, int]:
    """Из финальной очереди: живые entry-лимитки (не reduce-only LIMIT) и всё остальное."""
    entry_limits = sum(
        1 for order in pending if order.kind is OrderKind.LIMIT and not order.reduce_only
    )
    return entry_limits, len(pending) - entry_limits


def cell_rows(
    outcome: CellOutcome, combination: Mapping[str, Decimal], names: Sequence[str]
) -> tuple[str, str]:
    """Две CSV-строки (pessimistic, optimistic) по замороженной схеме design §10."""
    plain = _SWEEP._plain
    plain_or_null = _SWEEP._plain_or_null
    params_part = [plain(combination[name]) for name in names]
    rows: list[str] = []
    for side in outcome.sides:
        metrics = side.metrics
        limit_buy, other = _pending_counts(side.pending_after)
        cells = [
            side.intrabar.name.lower(),
            outcome.spec_version,
            *params_part,
            str(metrics.trades),
            str(metrics.wins),
            plain_or_null(metrics.win_rate()),
            plain(metrics.net_pnl),
            plain(metrics.equity),
            plain(metrics.max_drawdown),
            plain_or_null(metrics.profit_factor()),
            plain_or_null(metrics.sharpe()),
            str(limit_buy),
            str(other),
        ]
        rows.append(",".join(cells))
    return rows[0], rows[1]


def journal_lines(side: ForkSide, spec_version: str, combination_text: str) -> list[str]:
    """Заголовок + по строке на сделку; attribution-колонки — Ruling R2 (план Task 1)."""
    plain = _SWEEP._plain
    lines = [_JOURNAL_HEADER]
    for trade in side.trades:
        lines.append(
            ",".join(
                (
                    spec_version,
                    combination_text,
                    str(trade.entry_ts),
                    plain(trade.entry_price),
                    str(trade.exit_ts),
                    plain(trade.exit_price),
                    plain(trade.pnl),
                    trade.exit_reason,
                )
            )
        )
    return lines


def write_csv(path: Path, lines: Sequence[str]) -> None:
    """UTF-8 без BOM, LF; каталоги создаём сами."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as stream:
        for line in lines:
            stream.write(line + "\n")


def _csv_columns(names: Sequence[str]) -> tuple[str, ...]:
    return ("fork", "spec_version", *names, *_METRIC_COLUMNS)


def _header_lines(
    strategy: str,
    symbol: str,
    start_ts: int,
    end_ts: int,
    capital: Decimal,
    grid: Mapping[str, list[Decimal]],
    base_version: str,
    zones: tuple[Zone, ...],
    costs: CostModel,
    rejected: int,
) -> list[str]:
    plain = _SWEEP._plain
    return [
        f"strategy: {strategy}",
        f"symbol: {symbol}",
        f"window: [{start_ts}, {end_ts})",
        f"capital: {plain(capital)}",
        f"grid: {_SWEEP._grid_text(grid)}",
        f"spec_version (база): {base_version}",
        f"zones_used: {', '.join(str(zone) for zone in zones)}",
        (
            f"costs: taker_fee={plain(costs.taker_fee)}"
            f" maker_fee={plain(costs.maker_fee)}"
            f" slippage={plain(costs.slippage)}"
            f" leverage={plain(costs.leverage)}"
        ),
        "touches: holdout=0, live=0",
        "intrabar: both (строки помечены колонкой fork)",
        f"отклонено: {rejected}",
    ]


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="harness.py",
        description="Калибровочный прогон сетки: CSV обеих вилок + журнал сделок + очередь.",
    )
    parser.add_argument("db", type=Path, help="путь к базе SQLite")
    parser.add_argument("symbol", help="символ, например BTCUSDT")
    parser.add_argument("start_ts", type=int, help="начало окна, Unix-секунды")
    parser.add_argument("end_ts", type=int, help="конец окна, Unix-секунды")
    parser.add_argument("strategy", help="имя стратегии из реестра")
    parser.add_argument(
        "--param",
        action="append",
        type=_SWEEP._parse_grid,
        default=[],
        metavar="ИМЯ=в1,в2",
        help="сетка значения через запятую (повторяемо), синтаксис sweep",
    )
    parser.add_argument("--capital", type=_SWEEP._parse_capital, default=DEFAULT_CAPITAL)
    parser.add_argument(
        "--interval", choices=sorted(_INTERVALS), default="hour", help="интервал бара"
    )
    parser.add_argument(
        "--double-costs",
        action="store_true",
        help="удвоенные издержки: taker .0012 maker .0004 slippage .0010",
    )
    parser.add_argument("--out", type=Path, default=Path("data") / "calib")
    args = parser.parse_args(argv)
    if args.strategy not in STRATEGY_CLASSES:
        parser.error(f"неизвестная стратегия: {args.strategy}")
    if not args.db.exists():
        parser.error(f"база не найдена: {args.db}")
    return args


def _run_grid(args: argparse.Namespace) -> None:
    costs = _DOUBLE_COSTS if args.double_costs else CostModel()
    interval = _INTERVALS[args.interval]
    strategy_class = STRATEGY_CLASSES[args.strategy]
    defaults: dict[str, Decimal] = _SWEEP._defaults(strategy_class)
    required: set[str] = _SWEEP._required(strategy_class)
    grid: dict[str, list[Decimal]] = _SWEEP._merge_grid(args.param)
    unknown = sorted(set(grid) - set(defaults) - required)
    if unknown:
        raise SystemExit(f"у стратегии {args.strategy} нет таких параметров: {unknown}")
    missing = sorted(required - set(grid))
    if missing:
        raise SystemExit(
            f"у стратегии {args.strategy} обязательные параметры без дефолта: {missing} — "
            "задайте их через --param"
        )

    names = list(grid)
    base_spec = _make_spec(
        args.strategy, args.symbol, defaults, args.start_ts, args.end_ts, interval
    )
    stem = (
        f"{args.strategy}_{args.symbol}_{args.start_ts}_{args.end_ts}"
        + ("_dc" if args.double_costs else "")
    )
    out_dir: Path = args.out

    csv_lines: list[str] = []
    journal_p: list[str] = []
    journal_o: list[str] = []
    report_lines: list[str] = []
    rejected = 0
    combinations = _SWEEP._combinations(grid)
    zones: tuple[Zone, ...] | None = None

    for combination in combinations:
        params: dict[str, Decimal] = dict(defaults)
        params.update(combination)
        try:
            outcome = run_cell(
                args.db,
                args.symbol,
                args.strategy,
                params,
                args.start_ts,
                args.end_ts,
                capital=args.capital,
                interval=interval,
                costs=costs,
            )
        except (ValueError, LiquidationError) as error:
            rejected += 1
            print(
                f"ОТКЛОНЕНО {_SWEEP._combination_text(combination)}: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )
            continue
        zones = outcome.zones
        combination_text = _SWEEP._combination_text(params)
        pess, opt = outcome.sides
        row_p, row_o = cell_rows(outcome, params, names)
        csv_lines.extend((row_p, row_o))
        for sink, side in ((journal_p, pess), (journal_o, opt)):
            sink.extend(journal_lines(side, outcome.spec_version, combination_text)[1:])
        report = build_report(
            _make_spec(args.strategy, args.symbol, params, args.start_ts, args.end_ts, interval),
            outcome.zones,
            costs,
            _TRIALS,
            IntrabarOrder.PESSIMISTIC,
            pess.metrics,
            pess.trades,
        )
        report_lines.append(
            f"# cell: spec_version={outcome.spec_version} params: {combination_text or '(нет)'}"
        )
        # Учёт конца окна (C-3): очередь снята до сведения, pending-колонки csv
        # всегда 0 — инвариант submits − fills − expired − window_cancelled == 0.
        report_lines.append(f"# window_cancelled: {pess.window_cancelled}")
        report_lines.extend(report.to_text().splitlines())
        report_lines.append("")

    if zones is None:
        raise SystemExit("все ячейки сетки отклонены — артефактов прогона нет")

    write_csv(
        out_dir / f"{stem}.csv",
        [
            *_header_lines(
                args.strategy,
                args.symbol,
                args.start_ts,
                args.end_ts,
                args.capital,
                grid,
                base_spec.version,
                zones,
                costs,
                rejected,
            ),
            ",".join(_csv_columns(names)),
            *csv_lines,
        ],
    )
    write_csv(out_dir / f"{stem}_journal_p.csv", [_JOURNAL_HEADER, *journal_p])
    write_csv(out_dir / f"{stem}_journal_o.csv", [_JOURNAL_HEADER, *journal_o])
    write_csv(out_dir / f"{stem}_report_p.txt", report_lines)

    print(f"комбинаций: {len(combinations)}")
    if rejected:
        print(f"отклонено: {rejected}")
    print(f"артефакты: {out_dir / (stem + '[.csv|_journal_p.csv|_journal_o.csv|_report_p.txt]')}")


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else list(argv))
    _run_grid(args)


if __name__ == "__main__":
    main()
