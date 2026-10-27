"""Развёртка параметров стратегии по окну базы: CSV-таблица по пессимистичной вилке.

Скрипт живёт вне пакета `strategylab`: пакет в окружение не установлен, поэтому
корень репозитория добавляется в `sys.path` вручную — отсюда и импорт пакета
не в начале файла.

Запуск из корня репозитория (пример: база data\\btcusdt.db, символ BTCUSDT):

    .venv\\Scripts\\python.exe scripts/sweep.py DB SYMBOL START END STRATEGY --param exit_mult=1.5,2

Что делает:
- собирает все `--param ИМЯ=знач1,знач2,...` в сетку и перебирает её декартовым
  произведением; пустая сетка — одна комбинация из дефолтов стратегии;
- неуказанные параметры берёт из дефолтов конструктора стратегии (`inspect`),
  поэтому в сетке достаточно задать только те параметры, которые перебираются;
- каждую комбинацию прогоняет через `run_fork` — так же, как `cli run`, — и
  печатает одну строку CSV по пессимистичной вилке.

Оптимистичная вилка не печатается: расхождение вилок — вердикт CLI, а не
метрика развёртки. Комбинации идут последовательно, база открывается и
закрывается на каждую комбинацию.

Про версии спека. У каждой комбинации свой спек: параметры в нём — фактические
значения этой комбинации, поэтому версия спека в её строке воспроизводит ровно
этот прогон (`cli run` с теми же параметрами даст тот же `spec_version`).
`spec_version (база)` в шапке — версия спека с дефолтными параметрами стратегии:
это идентичность самой развёртки, а не отдельной строки.

Про касания зон. Испытаний у развёртки нет (внутрибазовая вилка — не
повтор), поэтому вместо «trials» шапка печатает число касаний live/holdout
зон этой спекой: `touches: holdout=N, live=M`. Каждое касание — строка jsonl
в `data/touch_registry.json` ({spec_version, window_role, symbol, iso_ts});
инкремент при каждом прогоне по не-fit зоне, и шапка всегда показывает
суммарное значение после текущего касания.
"""

# Импорт пакета strategylab стоит после sys.path — сознательно.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import inspect
import itertools
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strategylab.broker.costs import CostModel
from strategylab.broker.fork import run_fork
from strategylab.core.bars import Interval
from strategylab.data.schema import connect
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, zones_touched
from strategylab.report.report import Report
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.base import Strategy
from strategylab.strategy.examples import STRATEGY_CLASSES, build

# Капитал прогона по умолчанию: без стартового капитала стратегия с
# риск-сайзингом (размер = equity * риск) не открывает позицию вовсе.
DEFAULT_CAPITAL = Decimal("10000")

# Метка прогона в шапке спека: развёртка — не `cli run`, версии спеков разные.
RULES_REF = "sweep"

# Реестр касаний зон: одна строка jsonl — одно касание не-fit зоны при одном
# прогоне развёртки. Файл в репозитории, имя закреплено.
TOUCH_REGISTRY = Path(__file__).resolve().parent.parent / "data" / "touch_registry.json"

# Колонки метрик в CSV: тот же набор, что в отчёте прогона.
METRIC_COLUMNS = (
    "trades",
    "wins",
    "net_pnl",
    "max_drawdown",
    "profit_factor",
    "sharpe",
)

# Зоны, за касания которых отвечает реестр: fit — подбор, он свободен.
TOUCH_ROLES = (Zone.HOLDOUT, Zone.LIVE)


def _parse_ts(text: str) -> int:
    """Время окна — целым Decimal-строкой: никакого float из argparse."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"время не является числом: {text!r}") from None
    if value != value.to_integral_value():
        raise argparse.ArgumentTypeError(f"время должно быть целым: {text!r}")
    return int(value)


def _parse_capital(text: str) -> Decimal:
    """Капитал — Decimal-строкой: никакого float из argparse."""
    try:
        return Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"капитал не Decimal-строка: {text!r}") from None


def _parse_grid(text: str) -> tuple[str, list[Decimal]]:
    """Сетка значений параметра: `ИМЯ=знач1,знач2` → имя и список Decimal-значений."""
    name, sep, raw_values = text.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"параметр не в виде имя=значения: {text!r}")
    values: list[Decimal] = []
    for raw in raw_values.split(","):
        if not raw.strip():
            raise argparse.ArgumentTypeError(f"пустое значение в сетке параметра {name}: {text!r}")
        try:
            values.append(Decimal(raw))
        except InvalidOperation:
            raise argparse.ArgumentTypeError(
                f"параметр {name}: значение не Decimal-строка: {raw!r}"
            ) from None
    return name, values


def _merge_grid(pairs: Sequence[tuple[str, list[Decimal]]]) -> dict[str, list[Decimal]]:
    """Сетка из всех `--param`: одно имя — один список значений.

    Повтор имени не затирает предыдущие значения, а дополняет список: так
    `--param x=1 --param x=2` значит сетку {1, 2}, а не {2}.
    """
    grid: dict[str, list[Decimal]] = {}
    for name, values in pairs:
        grid.setdefault(name, []).extend(values)
    return grid


def _signature_params(strategy_class: type[Strategy]) -> dict[str, inspect.Parameter]:
    """Параметры конструктора стратегии: всё, кроме `self`."""
    signature = inspect.signature(strategy_class.__init__)
    return {name: parameter for name, parameter in signature.parameters.items() if name != "self"}


def _defaults(strategy_class: type[Strategy]) -> dict[str, Decimal]:
    """Дефолты параметров стратегии из сигнатуры конструктора.

    Пустой словарь — у стратегии нет параметров с дефолтами (`buy_hold`).
    """
    defaults: dict[str, Decimal] = {}
    for name, parameter in _signature_params(strategy_class).items():
        if parameter.default is inspect.Parameter.empty:
            continue
        value = parameter.default
        if not isinstance(value, Decimal):
            raise ValueError(f"дефолт параметра {name} — не Decimal: {value!r}")
        defaults[name] = value
    return defaults


def _required(strategy_class: type[Strategy]) -> set[str]:
    """Параметры без дефолта: их обязан задать пользователь (`sma_cross`)."""
    return {
        name
        for name, parameter in _signature_params(strategy_class).items()
        if parameter.default is inspect.Parameter.empty
    }


def _combinations(grid: Mapping[str, list[Decimal]]) -> list[dict[str, Decimal]]:
    """Декартово произведение сетки: список словарей имя→значение.

    Порядок детерминирован: имена — в порядке первого появления `--param`,
    значения — в порядке перечисления. Пустая сетка даёт одну комбинацию без
    параметров — прогон на дефолтах.
    """
    names = list(grid)
    if not names:
        return [{}]
    return [
        dict(zip(names, values, strict=True))
        for values in itertools.product(*(grid[name] for name in names))
    ]


def _spec(
    name: str,
    symbol: str,
    start_ts: int,
    end_ts: int,
    params: Mapping[str, Decimal],
) -> StrategySpec:
    """Спек прогона: стратегия, символ, окно, параметры. Версия — производная от них."""
    return StrategySpec(
        name=name,
        symbol=symbol,
        interval=Interval.MINUTE,
        start_ts=start_ts,
        end_ts=end_ts,
        params=dict(params),
        rules_ref=RULES_REF,
    )


def _factory(name: str, params: Mapping[str, Decimal]) -> Callable[[], Strategy]:
    """Фабрика без аргументов: run_fork вызывает её дважды, каждая вилка со своей стратегией."""

    def factory() -> Strategy:
        return build(name, params)

    return factory


def _zones(db: Path, symbol: str, start_ts: int, end_ts: int) -> tuple[Zone, ...]:
    """Зоны окна из базы: шапка развёртки — та же, что в отчёте прогона."""
    conn = connect(db)
    try:
        return zones_touched(conn, symbol, start_ts, end_ts)
    finally:
        conn.close()


def _registry_counts(registry: Path, spec_version: str) -> dict[str, int]:
    """Число касаний спеки по каждой не-fit зоне из реестра jsonl.

    Реестр отсутствует — счётчики пусты. Битая строка реестра — ошибка, а не
    молчаливая потеря истории касаний: счётчик существует ради честности.
    """
    counts: dict[str, int] = {}
    if not registry.exists():
        return counts
    with registry.open("r", encoding="utf-8") as file:
        for number, line in enumerate(file, start=1):
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError(
                    f"{registry}: строка {number} не объект JSON: {line.strip()!r}"
                )
            if event.get("spec_version") != spec_version:
                continue
            role = event.get("window_role")
            if role in TOUCH_ROLES:
                counts[role] = counts.get(role, 0) + 1
    return counts


def _touch_counts(
    registry: Path,
    spec_version: str,
    symbol: str,
    zones: tuple[Zone, ...],
) -> dict[str, int]:
    """Записывает касание каждой не-fit зоны окна и отдаёт суммарные счётчики спеки.

    Один прогон развёртки — одно касание на каждую non-fit зону, которую окно
    задевает; счётчик возвращается уже после инкремента, поэтому шапка
    печатает реальное число касаний. Fit-зона касаний не пишет: подбор
    свободен, в отличие от holdout и live.
    """
    recorded = [zone for zone in zones if zone in TOUCH_ROLES]
    if not recorded:
        return _registry_counts(registry, spec_version)
    with registry.open("a", encoding="utf-8") as file:
        for zone in recorded:
            event = {
                "spec_version": spec_version,
                "window_role": zone.value,
                "symbol": symbol,
                "iso_ts": datetime.now(UTC).isoformat(),
            }
            file.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    return _registry_counts(registry, spec_version)


def _touches_text(counts: Mapping[str, int]) -> str:
    """Счётчики касаний одной строкой шапки: `holdout=1, live=0`."""
    return ", ".join(
        f"{role.value}={counts.get(role.value, 0)}" for role in TOUCH_ROLES
    )


def _run_combination(
    db: Path,
    symbol: str,
    strategy: str,
    params: Mapping[str, Decimal],
    start_ts: int,
    end_ts: int,
    capital: Decimal,
) -> Report:
    """Прогон одной комбинации: своя база, свой спек, своя стратегия на вилку.

    База открывается и закрывается на комбинацию: соединение переживать не
    должно, а следующая комбинация может упасть иначе, чем предыдущая.
    """
    spec = _spec(strategy, symbol, start_ts, end_ts, params)
    conn = connect(db)
    try:
        store = CandleStore(conn, symbol)
        fork = run_fork(
            store,
            spec,
            _factory(strategy, params),
            CostModel(),
            capital=capital,
        )
    finally:
        conn.close()
    return fork.pessimistic


def _row(report: Report, combination: Mapping[str, Decimal], names: Sequence[str]) -> str:
    """Строка CSV: версия спека, значения перебираемых параметров, метрики."""
    metrics = report.metrics
    cells = [
        report.spec_version,
        *(_plain(combination[name]) for name in names),
        str(metrics.trades),
        str(metrics.wins),
        _plain(metrics.net_pnl),
        _plain(metrics.max_drawdown),
        _plain_or_null(metrics.profit_factor()),
        _plain_or_null(metrics.sharpe()),
    ]
    return ",".join(cells)


def _plain(value: Decimal) -> str:
    """Decimal без экспоненты: CSV читается человеком и сравнивается diff-ом."""
    return format(value, "f")


def _plain_or_null(value: Decimal | None) -> str:
    """Decimal строкой; None — маркером null, как в отчёте прогона."""
    return "null" if value is None else _plain(value)


def _grid_text(grid: Mapping[str, list[Decimal]]) -> str:
    """Сетка одной строкой: `имя=значение,значение; имя=значение`."""
    return "; ".join(
        f"{name}={','.join(_plain(value) for value in values)}" for name, values in grid.items()
    )


def _combination_text(combination: Mapping[str, Decimal]) -> str:
    """Комбинация одной строкой: `имя=значение; имя=значение`."""
    return "; ".join(f"{name}={_plain(value)}" for name, value in combination.items())


def _header(
    strategy: str,
    symbol: str,
    start_ts: int,
    end_ts: int,
    capital: Decimal,
    grid: Mapping[str, list[Decimal]],
    base_version: str,
    zones: tuple[Zone, ...],
    costs: CostModel,
    touches: Mapping[str, int],
) -> list[str]:
    """Шапка развёртки: как у отчёта прогона, только одна строка на всё."""
    return [
        f"strategy: {strategy}",
        f"symbol: {symbol}",
        f"window: [{start_ts}, {end_ts})",
        f"capital: {_plain(capital)}",
        f"grid: {_grid_text(grid)}",
        f"spec_version (база): {base_version}",
        f"zones_used: {', '.join(str(zone) for zone in zones)}",
        (
            f"costs: taker_fee={_plain(costs.taker_fee)}"
            f" maker_fee={_plain(costs.maker_fee)}"
            f" slippage={_plain(costs.slippage)}"
            f" leverage={_plain(costs.leverage)}"
        ),
        f"touches: {_touches_text(touches)}",
        "intrabar: PESSIMISTIC",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep.py",
        description=(
            "Развёртка параметров стратегии по окну базы: CSV-таблица по пессимистичной вилке."
        ),
    )
    parser.add_argument("db", type=Path, help="путь к базе SQLite")
    parser.add_argument("symbol", help="символ, например BTCUSDT")
    parser.add_argument("start_ts", type=_parse_ts, help="начало окна, Unix-секунды")
    parser.add_argument("end_ts", type=_parse_ts, help="конец окна, Unix-секунды")
    parser.add_argument("strategy", help="имя стратегии из реестра")
    parser.add_argument(
        "--capital",
        type=_parse_capital,
        default=DEFAULT_CAPITAL,
        metavar="КАПИТАЛ",
        help=(
            "начальный капитал контракта Decimal-строкой; "
            "0 — стратегия с риск-сайзингом не торгует вовсе"
        ),
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        type=_parse_grid,
        metavar="ИМЯ=ЗНАЧ1,ЗНАЧ2",
        help="сетка значений параметра Decimal-строками; можно повторять",
    )
    return parser


def _sweep(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Перебор сетки: строка CSV на комбинацию, итог — счётчик и время."""
    strategy_class = STRATEGY_CLASSES.get(args.strategy)
    if strategy_class is None:
        parser.error(f"неизвестная стратегия: {args.strategy}")
    if not args.db.exists():
        parser.error(f"база не найдена: {args.db}")

    defaults = _defaults(strategy_class)
    required = _required(strategy_class)
    grid = _merge_grid(args.param)
    unknown = sorted(set(grid) - set(defaults) - required)
    if unknown:
        parser.error(f"у стратегии {args.strategy} нет таких параметров: {unknown}")
    missing = sorted(required - set(grid))
    if missing:
        parser.error(
            f"у стратегии {args.strategy} обязательные параметры без дефолта: {missing} — "
            "задайте их через --param"
        )

    combinations = _combinations(grid)
    names = list(grid)
    costs = CostModel()
    base_spec = _spec(args.strategy, args.symbol, args.start_ts, args.end_ts, defaults)
    zones = _zones(args.db, args.symbol, args.start_ts, args.end_ts)
    touches = _touch_counts(TOUCH_REGISTRY, base_spec.version, args.symbol, zones)

    for line in _header(
        args.strategy,
        args.symbol,
        args.start_ts,
        args.end_ts,
        args.capital,
        grid,
        base_spec.version,
        zones,
        costs,
        touches,
    ):
        print(line)
    print(",".join(("spec_version", *names, *METRIC_COLUMNS)))

    started = time.perf_counter()
    failed = 0
    for combination in combinations:
        params = dict(defaults)
        params.update(combination)
        try:
            report = _run_combination(
                args.db,
                args.symbol,
                args.strategy,
                params,
                args.start_ts,
                args.end_ts,
                args.capital,
            )
        except ValueError as error:
            # Параметры не прошли валидацию стратегии: комбинация в прогон не годится.
            failed += 1
            print(
                f"комбинация {_combination_text(combination)} отклонена: {error}",
                file=sys.stderr,
            )
            continue
        print(_row(report, combination, names))
    elapsed = time.perf_counter() - started

    print()
    print(f"комбинаций: {len(combinations)}")
    if failed:
        print(f"отклонено: {failed}")
    print(f"elapsed: {elapsed:.2f}s")


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    _sweep(parser, args)


if __name__ == "__main__":
    main()
