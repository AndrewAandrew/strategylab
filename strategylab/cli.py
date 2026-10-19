from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from strategylab.broker.costs import CostModel
from strategylab.broker.fork import Fork, run_fork
from strategylab.core.bars import Interval
from strategylab.data.schema import connect
from strategylab.data.store import CandleStore
from strategylab.data.sync import sync_1m
from strategylab.report.report import Report
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.base import Strategy
from strategylab.strategy.examples import build

VERDICT_MEASURABLE = "ВЕРДИКТ: измерима"
VERDICT_NOT_MEASURABLE = "ВЕРДИКТ: НЕ ИЗМЕРИМА — вилки расходятся по вердикту"

DEFAULT_RULES_REF = "cli"


def _verdict(report: Report) -> bool:
    """Вердикт измеримости: стратегия прибыльна при любом внутрибазовом порядке."""
    return report.metrics.net_pnl > 0


def verdict_line(fork: Fork) -> str:
    """Строка вердикта дословно из брифа: измерима либо вилки расходятся."""
    if fork.diverges(_verdict):
        return VERDICT_NOT_MEASURABLE
    return VERDICT_MEASURABLE


def _parse_ts(text: str) -> int:
    """Время окна — целым Decimal-строкой: никакого float из argparse."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"время не является целым числом: {text!r}") from None
    if value != value.to_integral_value():
        raise argparse.ArgumentTypeError(f"время должно быть целым: {text!r}")
    return int(value)


def _parse_interval(text: str) -> Interval:
    """Интервал — именем члена Interval, а не числом."""
    try:
        return Interval[text]
    except KeyError:
        names = ", ".join(member.name for member in Interval)
        raise argparse.ArgumentTypeError(f"интервал {text!r} не член Interval: {names}") from None


def _parse_param(text: str) -> tuple[str, Decimal]:
    """Параметр стратегии — парой имя=Decimal-строка."""
    name, sep, raw_value = text.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"параметр не в виде имя=значение: {text!r}")
    try:
        return name, Decimal(raw_value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(
            f"параметр {name}: значение не Decimal-строка: {raw_value!r}"
        ) from None


def _strategy_factory(name: str, params: dict[str, Decimal]) -> Callable[[], Strategy]:
    """Фабрика без аргументов: run_fork вызывает её дважды, каждая вилка со своей стратегией."""

    def factory() -> Strategy:
        return build(name, params)

    return factory


def _run(args: argparse.Namespace) -> None:
    params = dict(_parse_param(pair) for pair in args.param)
    spec = StrategySpec(
        name=args.strategy,
        symbol=args.symbol,
        interval=args.interval,
        start_ts=args.start_ts,
        end_ts=args.end_ts,
        params=params,
        rules_ref=DEFAULT_RULES_REF,
    )
    conn = connect(args.db)
    try:
        store = CandleStore(conn, args.symbol)
        fork = run_fork(store, spec, _strategy_factory(args.strategy, params), CostModel())
    finally:
        conn.close()
    print(fork.pessimistic.to_text())
    print(fork.optimistic.to_text())
    print(verdict_line(fork))


def _sync(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    try:
        loaded = sync_1m(conn, args.symbol, args.start_ts, args.end_ts)
    finally:
        conn.close()
    print(f"загружено баров: {loaded}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="strategylab",
        description="Прогон парой: пессимистичный и оптимистичный внутрибазовый порядок.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="офлайн-прогон парой вилок по существующей базе"
    )
    run_parser.add_argument("db", type=Path, help="путь к базе SQLite")
    run_parser.add_argument("symbol", help="символ, например BTCUSDT")
    run_parser.add_argument(
        "interval", type=_parse_interval, help="интервал — именем члена Interval"
    )
    run_parser.add_argument("start_ts", type=_parse_ts, help="начало окна, Unix-секунды")
    run_parser.add_argument("end_ts", type=_parse_ts, help="конец окна, Unix-секунды")
    run_parser.add_argument("strategy", help="имя стратегии из реестра")
    run_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="ИМЯ=ЗНАЧЕНИЕ",
        help="параметр стратегии Decimal-строкой; можно повторять",
    )
    run_parser.set_defaults(handler=_run)

    sync_parser = subparsers.add_parser("sync", help="загрузка 1m-баров окна с Bybit")
    sync_parser.add_argument("db", type=Path, help="путь к базе SQLite")
    sync_parser.add_argument("symbol", help="символ, например BTCUSDT")
    sync_parser.add_argument("start_ts", type=_parse_ts, help="начало окна, Unix-секунды")
    sync_parser.add_argument("end_ts", type=_parse_ts, help="конец окна, Unix-секунды")
    sync_parser.set_defaults(handler=_sync)

    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
