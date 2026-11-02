"""CLI-раннер одной клетки×фолда Series B carry (B-5).

Читает `bars_1m` (CandleStore) и `funding_8h` (rate — Decimal-строка, конвенция
коллектора), гоняет `strategylab.carry.run_carry`, пишет 3-файловый артефакт
в конвенции серии A: csv метрик (metаблок + header + строка fork=pessimistic),
journal_p (cash-flow строки), report_p (свертка). `carry_version` — sha256 от
исходника модуля + окна + параметров (12 хекс), детерминирован.

R-next-2: окно обязано кончаться до holdout (end_ts <= 1790294400).
LIQUIDATED_RUN_DEAD: CarryLiquidationError -> строка в stdout, артефактов нет,
exit code 4 (идиом серии A: исход, не баг).

Пример:
  python scripts/carry/grid.py --db data/btcusdt.db --symbol BTCUSDT \
      --start 1761523200 --end 1771113600 --theta-entry 0.00005 \
      --theta-exit 0.00003 --out data/calib/rB_t1_btc_f1
"""

# Импорт пакета strategylab стоит после sys.path — сознательно, как в scripts/sweep.py.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from strategylab.carry import (
    CarryLiquidationError,
    CarryParams,
    FundingSettle,
    run_carry,
)
from strategylab.core.bars import Bar
from strategylab.data.schema import connect
from strategylab.data.store import CandleStore

HOLDOUT_FLOOR = 1790294400  # R-next-2: никогда не читается
_DAYS_SEC = Decimal(86400)
_CSV_COLUMNS = (
    "fork",
    "carry_version",
    "symbol",
    "start_ts",
    "end_ts",
    "theta_entry",
    "theta_exit",
    "lev",
    "capital",
    "holds",
    "n_settles",
    "net_pnl",
    "equity",
    "annualized_pct",
    "max_leg_dd_frac",
    "margin_call",
    "flat_fold",
    "funding_gross",
    "friction",
)
_JOURNAL_HEADER = "ts,kind,ref,fill,qty,rate,notional,cash_delta,cash_after"


def _carry_version(params: CarryParams, symbol: str, start: int, end: int) -> str:
    source = Path(__file__).resolve().parents[2] / "strategylab" / "carry.py"
    basis = "|".join(
        (
            hashlib.sha256(source.read_bytes()).hexdigest(),
            symbol,
            str(start),
            str(end),
            str(params.theta_entry),
            str(params.theta_exit),
            str(params.lev),
            str(params.capital),
            str(params.taker_fee),
            str(params.slippage),
        )
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def _load_funding(
    conn: sqlite3.Connection, symbol: str, start: int, end: int
) -> list[FundingSettle]:
    rows = conn.execute(
        "SELECT ts, rate FROM funding_8h WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts",
        (symbol, start, end),
    ).fetchall()
    return [FundingSettle(ts=int(ts), rate=Decimal(rate)) for ts, rate in rows]


def _annualized(net: Decimal, capital: Decimal, start: int, end: int) -> Decimal:
    days = Decimal(end - start) / _DAYS_SEC
    return net / capital * Decimal(365) / days * Decimal(100)


def _format_row(values: Sequence[object]) -> str:
    return ",".join("" if v is None else str(v) for v in values)


def _journal_value(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _write_text(path: Path, lines: Sequence[str]) -> None:
    """UTF-8 без BOM, LF — конвенция серии A (harness.write_csv)."""
    with open(path, "w", encoding="utf-8", newline="") as stream:
        for line in lines:
            stream.write(line + "\n")


def run(args: argparse.Namespace) -> int:
    if args.end > HOLDOUT_FLOOR:
        print(f"HOLDOUT_GUARD: end_ts {args.end} > {HOLDOUT_FLOOR} — отказ (R-next-2)")
        return 2
    if args.end <= args.start:
        print(f"WINDOW_GUARD: end {args.end} <= start {args.start} — пустое/инверсное окно")
        return 2
    params = CarryParams(
        theta_entry=args.theta_entry,
        theta_exit=args.theta_exit,
        capital=args.capital,
        lev=args.lev,
        taker_fee=args.taker_fee,
        slippage=args.slippage,
    )
    if params.theta_exit >= params.theta_entry:
        print("PARAM_GUARD: theta_exit >= theta_entry — гистерезис нарушен, отказ")
        return 2
    version = _carry_version(params, args.symbol, args.start, args.end)

    conn = connect(args.db)
    try:
        bars: list[Bar] = list(
            CandleStore(conn, args.symbol).read_1m(args.symbol, args.start, args.end)
        )
        settles = _load_funding(conn, args.symbol, args.start, args.end)
    finally:
        conn.close()

    try:
        result = run_carry(bars, settles, params, args.end)
    except CarryLiquidationError as exc:
        print(f"LIQUIDATED_RUN_DEAD: {exc}")
        print(f"cell: {args.symbol} [{args.start}, {args.end}) v={version} — артефактов нет")
        return 4

    net_ann = _annualized(result.net, params.capital, args.start, args.end)
    stem = f"carry_{args.symbol}_{args.start}_{args.end}"
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = [
        "strategy: carry_funding",
        f"symbol: {args.symbol}",
        f"window: [{args.start}, {args.end})",
        f"capital: {format(params.capital, 'f')}",
        f"grid: theta_entry={format(params.theta_entry, 'f')}; theta_exit="
        f"{format(params.theta_exit, 'f')}; lev={format(params.lev, 'f')}",
        f"carry_version: {version}",
        "zones_used: n/a (funding-driven, bars только mark/risk)",
        f"costs: taker_fee={format(params.taker_fee, 'f')} slippage="
        f"{format(params.slippage, 'f')} leverage(маржа)=1/lev={format(params.lev, 'f')}",
    ]
    csv_rows = [
        *meta,
        _format_row(_CSV_COLUMNS),
        _format_row(
            (
                "pessimistic",
                version,
                args.symbol,
                args.start,
                args.end,
                format(params.theta_entry, "f"),
                format(params.theta_exit, "f"),
                format(params.lev, "f"),
                format(params.capital, "f"),
                result.holds,
                result.settles,
                format(result.net, "f"),
                format(result.equity, "f"),
                format(net_ann, "f"),
                format(result.max_leg_dd_frac, "f"),
                int(result.margin_call),
                int(result.flat_fold),
                format(result.funding_gross, "f"),
                format(result.friction, "f"),
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
                _journal_value(r.ref),
                _journal_value(r.fill),
                _journal_value(r.qty),
                _journal_value(r.rate),
                _journal_value(r.notional),
                format(r.cash_delta, "f"),
                format(r.cash_after, "f"),
            )
        )
        for r in result.journal
    ]
    _write_text(out_dir / f"{stem}_journal_p.csv", journal)

    report = [
        f"# cell: {args.symbol} [{args.start}, {args.end}) carry_version={version}",
        f"# grid: theta_entry={format(params.theta_entry, 'f')} theta_exit="
        f"{format(params.theta_exit, 'f')} lev={format(params.lev, 'f')} "
        f"capital={format(params.capital, 'f')}",
        "fork: pessimistic (единственный — оптимистичного форка у carry нет)",
        f"bars_in_window: {len(bars)} settlements_in_window: {len(settles)}",
        f"holds: {result.holds}",
        f"n_settles (засчитанные): {result.settles}",
        f"funding_gross: {format(result.funding_gross, 'f')}",
        f"friction (fees+slip, обе стороны): {format(result.friction, 'f')}",
        f"net_pnl: {format(result.net, 'f')}",
        f"equity: {format(result.equity, 'f')}",
        f"annualized_pct: {format(net_ann, 'f')}",
        f"max_leg_dd_frac (доля маржи N0): {format(result.max_leg_dd_frac, 'f')}",
        f"margin_call (>=0.5): {int(result.margin_call)}",
        f"flat_fold (ноль засчитанных): {int(result.flat_fold)}",
        "# инвариант: equity == capital + net; net == funding_gross - friction",
    ]
    _write_text(out_dir / f"{stem}_report_p.txt", report)

    print(f"carry_version: {version}")
    print(
        f"net_pnl: {format(result.net, 'f')} annualized_pct: {format(net_ann, 'f')} "
        f"n_settles: {result.settles} holds: {result.holds} "
        f"margin_call: {int(result.margin_call)} flat_fold: {int(result.flat_fold)}"
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
    p.add_argument("--theta-entry", type=_decimal, required=True, dest="theta_entry")
    p.add_argument("--theta-exit", type=_decimal, required=True, dest="theta_exit")
    p.add_argument("--lev", type=_decimal, default=Decimal("1"))
    p.add_argument("--capital", type=_decimal, default=Decimal("10000"))
    p.add_argument("--taker-fee", type=_decimal, default=Decimal("0.0006"), dest="taker_fee")
    p.add_argument("--slippage", type=_decimal, default=Decimal("0.0005"))
    p.add_argument("--out", type=Path, required=True)
    return p


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(run(args))


if __name__ == "__main__":
    main()
