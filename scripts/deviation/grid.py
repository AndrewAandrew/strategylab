"""CLI-раннер одной клетки×фолда Series E deviation (E-5, решётка E-2-F).

Читает `bars_1m` (CandleStore, включая `--ctx-hours` контекста ДО фолда — зоны,
пивоты и trailing-объём не видят будущего, но нуждаются в истории), гоняет
`strategylab.deviation.run_deviation`, пишет 3-файловый артефакт в конвенции
серии A: csv метрик (метаблок + header + строка fork=pessimistic), journal_p
(cash-flow), report_p (свертка). `deviation_version` — sha256 от исходника
модуля + символа + окна + параметров + ctx (12 хекс), детерминирован.

Эйты E2-1…E2-5 (включая bootstrap 30d seed=42) считаются замерщиком по
артефактам, здесь — только сырые числа клетки.

R-next-2: окно обязано кончаться до holdout (end_ts <= 1790294400). Исключение —
E-7: явный флаг `--holdout-ok` вооружает чтение холдаута (печатает HOLDOUT_ARMED
до чтения БД); без флага guard живёт — случайное чтение по-прежнему невозможно.
Guards HOLDOUT/WINDOW/PARAM — отказ до чтения БД, exit code 2, артефактов нет.

Пример (клетка E-b3, фолд f1):
  python scripts/deviation/grid.py --db data/ethusdt.db --symbol ETHUSDT \
      --start 1761523200 --end 1771113600 --cell E-b3 --tol 0.020 \
      --depth 0.005 --conf 30 --vol-filter 1 --exit-mode V1_48 \
      --out data/calib/dev_E-b3_ETHUSDT_f1
(--out — папка; имена файлов внутри — полный stem dev_<cell>_<symbol>_<start>_<end>.)
"""

# Импорт пакета strategylab стоит после sys.path — сознательно, как в scripts/carry/grid.py.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from strategylab.data.schema import connect
from strategylab.data.store import CandleStore
from strategylab.deviation import SCAN_MIN_DEPTH, DeviationParams, run_deviation

HOLDOUT_FLOOR = 1790294400  # R-next-2: никогда не читается без явного --holdout-ok
HOLDOUT_CEILING = 1793577600  # E-7: потолок замороженного холдаут-окна (полурединтервал)
_DAYS_SEC = Decimal(86400)
_CSV_COLUMNS = (
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
_JOURNAL_HEADER = "ts,kind,side,ref,fill,qty,reason,cash_delta,cash_after"
_CELL_RE = re.compile(r"[A-Za-z0-9_.-]{1,24}")


def _deviation_version(params: DeviationParams, symbol: str, start: int, end: int, ctx: int) -> str:
    source = Path(__file__).resolve().parents[2] / "strategylab" / "deviation.py"
    basis = "|".join(
        (
            hashlib.sha256(source.read_bytes()).hexdigest(),
            symbol,
            str(start),
            str(end),
            str(ctx),
            str(params.zone_tf_secs),
            str(params.touches),
            str(params.tol),
            str(params.depth),
            str(params.conf_min),
            str(params.vol_filter),
            params.exit_mode,
            str(params.capital),
            str(params.taker_fee),
            str(params.slippage),
        )
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def _annualized(net: Decimal, capital: Decimal, start: int, end: int) -> Decimal:
    days = Decimal(end - start) / _DAYS_SEC
    return net / capital * Decimal(365) / days * Decimal(100)


def _format_row(values: Sequence[object]) -> str:
    return ",".join("" if v is None else str(v) for v in values)


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def _journal_value(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _write_text(path: Path, lines: Sequence[str]) -> None:
    """UTF-8 без BOM, LF — конвенция серии A (harness.write_csv)."""
    with open(path, "w", encoding="utf-8", newline="") as stream:
        for line in lines:
            stream.write(line + "\n")


def run(args: argparse.Namespace) -> int:
    if args.end > HOLDOUT_FLOOR and not args.holdout_ok:
        print(f"HOLDOUT_GUARD: end_ts {args.end} > {HOLDOUT_FLOOR} — отказ (R-next-2)")
        return 2
    if args.end <= args.start:
        print(f"WINDOW_GUARD: end {args.end} <= start {args.start} — пустое/инверсное окно")
        return 2
    if not _CELL_RE.fullmatch(args.cell):
        print(f"PARAM_GUARD: cell {args.cell!r} — id клетки для артефактов: [A-Za-z0-9_.-]")
        return 2
    if args.depth < SCAN_MIN_DEPTH:
        print(f"PARAM_GUARD: depth {args.depth} < порога скана {SCAN_MIN_DEPTH} — вне решётки")
        return 2
    if args.tol <= 0 or args.capital <= 0 or args.ctx_hours < 0:
        print("PARAM_GUARD: tol/capital обязаны быть > 0, ctx_hours >= 0")
        return 2
    # Все guards пройдены: событие arms печатается здесь, до первого чтения БД;
    # отклонённый выше прогон не оставляет HOLDOUT_ARMED в логе (E-7 one-shot).
    if args.end > HOLDOUT_FLOOR:
        print(f"HOLDOUT_ARMED: end_ts {args.end} > {HOLDOUT_FLOOR} — чтение холдаута "
              "вооружено явным --holdout-ok (E-7, одноразово)")
        if args.end > HOLDOUT_CEILING:
            print(f"WARN: end_ts {args.end} > потолка E-7 {HOLDOUT_CEILING} — выход за "
                  "замороженное окно [1790294400, 1793577600), проверь опечатку")
    params = DeviationParams(
        tol=args.tol,
        depth=args.depth,
        conf_min=args.conf,
        vol_filter=args.vol_filter,
        exit_mode=args.exit_mode,
        zone_tf_secs=args.zone_tf_secs,
        touches=args.touches,
        capital=args.capital,
        taker_fee=args.taker_fee,
        slippage=args.slippage,
    )
    version = _deviation_version(params, args.symbol, args.start, args.end, args.ctx_hours)

    ctx_start = max(0, args.start - args.ctx_hours * 3600)
    conn = connect(args.db)
    try:
        bars = list(CandleStore(conn, args.symbol).read_1m(args.symbol, ctx_start, args.end))
    finally:
        conn.close()

    result = run_deviation(bars, params, args.start, args.end)

    net_ann = _annualized(result.net, params.capital, args.start, args.end)
    stem = f"dev_{args.cell}_{args.symbol}_{args.start}_{args.end}"
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = [
        "strategy: deviation_zone_reversal",
        f"symbol: {args.symbol}",
        f"window: [{args.start}, {args.end}) (фолд; события/решения только здесь)",
        f"ctx_hours: {args.ctx_hours} (1m-контекст до фолда: зоны, пивоты, trailing-объём)",
        f"capital: {_fmt(params.capital)}",
        f"grid: tol={_fmt(params.tol)}; depth={_fmt(params.depth)}; conf={params.conf_min}; "
        f"k={params.vol_filter}; exit={params.exit_mode}; tf={params.zone_tf_secs}s; "
        f"tch={params.touches}",
        f"deviation_version: {version}",
        f"costs: taker_fee={_fmt(params.taker_fee)} slippage={_fmt(params.slippage)} "
        "(adverse, обе ноги; fee от capital на ногу, lev 1)",
    ]
    csv_rows = [
        *meta,
        _format_row(_CSV_COLUMNS),
        _format_row(
            (
                "pessimistic",
                version,
                args.symbol,
                args.cell,
                args.start,
                args.end,
                args.ctx_hours,
                params.zone_tf_secs,
                params.touches,
                _fmt(params.tol),
                _fmt(params.depth),
                params.conf_min,
                params.vol_filter,
                params.exit_mode,
                _fmt(params.capital),
                _fmt(params.taker_fee),
                _fmt(params.slippage),
                result.zones_count,
                result.events_count,
                result.rounds_count,
                result.n_stops,
                result.n_takes,
                result.n_tb,
                result.n_forced,
                _fmt(result.price_gross),
                _fmt(result.friction),
                _fmt(result.net),
                _fmt(result.equity),
                _fmt(net_ann),
                int(result.flat_fold),
            )
        ),
    ]
    _write_text(out_dir / f"{stem}.csv", csv_rows)

    journal = [_JOURNAL_HEADER]
    journal += [
        _format_row(
            (
                r.ts,
                r.kind,
                r.side,
                _journal_value(r.ref),
                _journal_value(r.fill),
                _journal_value(r.qty),
                r.reason,
                format(r.cash_delta, "f"),
                format(r.cash_after, "f"),
            )
        )
        for r in result.journal
    ]
    _write_text(out_dir / f"{stem}_journal_p.csv", journal)

    report = [
        f"# cell: {args.symbol} [{args.start}, {args.end}) {args.cell} "
        f"deviation_version={version}",
        f"# grid: tol={_fmt(params.tol)} depth={_fmt(params.depth)} conf={params.conf_min} "
        f"k={params.vol_filter} exit={params.exit_mode} tf={params.zone_tf_secs}s "
        f"tch={params.touches} capital={_fmt(params.capital)}",
        "fork: pessimistic (стоп-перед-тейком в V2 — консервативный мир; оптимистичного форка нет)",
        f"bars_1m_in_window: {len(bars)} (включая контекст с {ctx_start})",
        f"zones: {result.zones_count} events (скан фолда): {result.events_count}",
        f"rounds: {result.rounds_count}",
        f"stops/takes/tb/forced: {result.n_stops}/{result.n_takes}/{result.n_tb}/{result.n_forced}",
        f"price_gross: {_fmt(result.price_gross)}",
        f"friction (2 ноги × fee×capital за круг): {_fmt(result.friction)}",
        f"net_pnl: {_fmt(result.net)}",
        f"equity: {_fmt(result.equity)}",
        f"annualized_pct (стоимость фолда к капиталу): {_fmt(net_ann)}",
        f"flat_fold (ноль кругов): {int(result.flat_fold)}",
        "# инвариант: net == price_gross - friction; equity == capital + net",
    ]
    _write_text(out_dir / f"{stem}_report_p.txt", report)

    print(f"deviation_version: {version}")
    print(
        f"net_pnl: {_fmt(result.net)} annualized_pct: {_fmt(net_ann)} "
        f"rounds: {result.rounds_count} events: {result.events_count} "
        f"flat_fold: {int(result.flat_fold)}"
    )
    print(f"артефакты: {out_dir / (stem + '[.csv|_journal_p.csv|_report_p.txt]')}")
    return 0


def _decimal(text: str) -> Decimal:
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise argparse.ArgumentTypeError(f"не Decimal-строка: {text!r}")
    return Decimal(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--end", type=int, required=True)
    p.add_argument("--cell", default="cell")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--ctx-hours", type=int, default=960, dest="ctx_hours")
    p.add_argument("--zone-tf-secs", type=int, default=4 * 3600, dest="zone_tf_secs")
    p.add_argument("--touches", type=int, default=2)
    p.add_argument("--tol", type=_decimal, required=True)
    p.add_argument("--depth", type=_decimal, required=True)
    p.add_argument("--conf", type=int, required=True, choices=(0, 30, 60))
    p.add_argument("--vol-filter", type=int, required=True, dest="vol_filter", choices=(1, 2))
    p.add_argument("--exit-mode", required=True, dest="exit_mode", choices=("V1_48", "V2_R2"))
    p.add_argument("--capital", type=_decimal, default=Decimal("10000"))
    p.add_argument("--taker-fee", type=_decimal, default=Decimal("0.0006"), dest="taker_fee")
    p.add_argument("--slippage", type=_decimal, default=Decimal("0.0005"))
    p.add_argument(
        "--holdout-ok",
        action="store_true",
        dest="holdout_ok",
        help="явное вооружение чтения холдаута (E-7); по умолчанию HOLDOUT_GUARD активен",
    )
    return p


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(run(args))


if __name__ == "__main__":
    main()
