"""Экспорт сцены канальной стратегии для терминала TradingView-Lightweight (старый проект).

Скрипт живёт вне пакета `strategylab`: пакет в окружение не установлен, поэтому
корень репозитория добавляется в `sys.path` вручную — отсюда и импорт пакета
не в начале файла.

Запуск из корня репозитория (пример: база data\\btcusdt.db, символ BTCUSDT):

    .venv\\Scripts\\python.exe scripts/export_flat_keltner.py DB SYMBOL START_TS END_TS \\
        [--tf 15m|1h] [--capital N] [--param ИМЯ=ЗНАЧЕНИЕ] [--out ПУТЬ]

Необязательная позиция STRATEGY ставится до DB или после END_TS (не между DB
и symbol): flat_keltner по умолчанию, trend_pullback — канал регрессии,
sweep_liquidity — диапазон зачистки ликвидности:

    .venv\\Scripts\\python.exe scripts/export_flat_keltner.py trend_pullback \\
        DB SYMBOL START_TS END_TS

Стратегия обязана иметь журнал канала (`enable_channel_log`/`channel_log`):
канал берётся из самой стратегии, а не пересчитан экспортёром — экспорт обязан
показывать тот канал, по которому стратегия принимала решения.

Таймфрейм сцены свечей — аргумент `--tf`: 15m по умолчанию и для всех
исторических сцен, 1h для sweep_liquidity — её канал живёт на закрытиях
1H-баров, и свечи сцены совпадают с барами журнала. Маркеры округляются вниз
до бара `--tf` тем же якорем по эпохе.

Что делает:
- прогоняет выбранную канальную стратегию по окну базы тем же движком, тем же
  издержками и тем же капиталом, что и `cli run`, и берёт пессимистичную
  вилку; расхождение вилок по вердикту печатается в stderr — вердикт остаётся
  вердиктом CLI;
- выгружает сцену: свечи выбранного `--tf` из 1m-баров базы, канал стратегии
  и маркеры входов и выходов.

Откуда взяты данные сцены
-------------------------

Свечи — агрегаты выбранного `--tf` из 1m-баров базы, собранные агрегатором
`strategylab.data.aggregate`. Агрегатор строг: неполная группа минут — ошибка,
а не бар меньшего размера. Окно базы может быть рваным (пропуск минуты), и
тогда строгий агрегатор упал бы на первом же разрыве. Поэтому экспортёр
оставляет только целые окна и собирает их пачками подряд: неполное окно в
сцену не попадает, полные окна собираются той же функцией.

Канал — из самой стратегии (`ChannelStrategy.channel_log` протокола), а не
пересчитан экспортёром: экспорт обязан показывать тот канал, по которому
стратегия принимала решения. У flat_keltner это средняя линия EMA(20) и
границы ±2·ATR(20) последнего закрытого 5H-бара; у trend_pullback — канал
линейной регрессии 5H с границами ±sigma_mult·σ; у sweep_liquidity — границы
диапазона на закрытии каждого 1H-бара: lower — минимум Low последних
`lookback` ЗАВЕРШЁННЫХ 1H-баров (та самая граница, что проверяется в сигнале
смётки), upper — максимум High тех же баров (зеркало для контекста), mid —
полусумма границ.

Сделки — из журнала закрытых сделок движка (`strategylab.core.trades`).
Журнал включается флагом `record_trades` у `run_fork` и по умолчанию выключен:
без него отчёт прогона не содержит списка сделок, а метрики считаются так же.
У sweep_liquidity сделки частичные: одна позиция закрывается частями
(TP1 — половина, потом остаток на TP2 или стопе), и каждая закрытая часть
отдельная запись журнала с тем же временем входа — маркеры входа повторяются,
это честно: каждая часть журналная сделка со своим выходом.

Маркеры: вход — `arrowUp` под баром, выход по цели (`take_profit`, а также
частичный лимитный филл `limit`) — `circle` над баром с подписью `TP` (с
интрабазовым порядком PESSIMISTIC цель исполняется зеркалом позиции, даже
частичным, и журнал всё равно получает `take_profit`; прямое `limit` — на
случай исключений брокера), выход по стопу — `circle` над баром с подписью
`SL`. Сведение позиции в конце окна — подпись `EXIT`: это не цель и не стоп,
и называть его одним из них было бы враньём.

Время маркеров и линий — Unix-секунды, как ждёт Lightweight Charts. Фикстуры
сделок лежат на минутных барах, поэтому время маркера округляется вниз до
начала содержащего его бара `--tf`: маркер должен попасть на свечу, иначе
Lightweight Charts его не покажет.

Про версии спека. Спек экспорта несёт эффективные параметры прогона (дефолты
стратегии плюс переопределения) и `rules_ref` = `export`: экспорт — не `cli
run`, и версии спеков у них разные. Параметры в `meta` — те же эффективные
значения, поэтому версия спека воспроизводит ровно этот прогон.
"""

# Импорт пакета strategylab стоит после sys.path — сознательно.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import inspect
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import groupby
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_ROOT_PATH = Path(__file__).resolve().parent.parent
_ROOT = str(_ROOT_PATH)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strategylab.broker.costs import CostModel
from strategylab.broker.fork import run_fork
from strategylab.cli import VERDICT_NOT_MEASURABLE, verdict_line
from strategylab.core.bars import Bar, Interval
from strategylab.core.trades import Trade
from strategylab.data.aggregate import aggregate
from strategylab.data.schema import connect
from strategylab.data.store import CandleStore
from strategylab.report.report import Report
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.base import Strategy
from strategylab.strategy.examples import build
from strategylab.strategy.flat_keltner import FlatKeltnerStrategy
from strategylab.strategy.indicators import ChannelPoint
from strategylab.strategy.sweep_liquidity import SweepLiquidityStrategy
from strategylab.strategy.trend_pullback import TrendPullbackStrategy

# Сцены всех канальных стратегий агрегируются из 1m-баров базы; таймфрейм
# свечей сцены задаётся аргументом --tf: 15m — исторические сцены, 1h —
# sweep_liquidity с журналом диапазона на закрытиях 1H-баров.
DEFAULT_TF = Interval.FIFTEEN_MIN

# Имена таймфреймов в meta сцены: отличие 1h от 15m видно в терминале.
_TF_NAMES: Mapping[Interval, str] = {
    Interval.FIFTEEN_MIN: "15m",
    Interval.HOUR: "1h",
}

DEFAULT_STRATEGY = "flat_keltner"

# Капитал прогона по умолчанию: без стартового капитала стратегия с
# риск-сайзингом (размер = equity * риск) не открывает позицию вовсе.
DEFAULT_CAPITAL = Decimal("10000")

# Метка прогона в спеке: экспорт — не `cli run`, версии спеков разные.
RULES_REF = "export"

# Цвета сцены: терминальная палитра (--ema/--up/--down) плюс синий цели.
COLOR_MID = "#f5a623"
COLOR_UPPER = "#ef5350"
COLOR_LOWER = "#26a69a"
COLOR_BUY = "#26a69a"
COLOR_TP = "#2196f3"
COLOR_SL = "#ef5350"
COLOR_EXIT = "#7b8494"

# Причины выхода из филла брокера → подпись и цвет маркера.
EXIT_MARKERS: Mapping[str, tuple[str, str]] = {
    "take_profit": ("TP", COLOR_TP),
    "stop": ("SL", COLOR_SL),
    "market": ("EXIT", COLOR_EXIT),
}


@runtime_checkable
class _ChannelStrategy(Protocol):
    """Стратегия с журналом канала: экспорт показывает канал из решения."""

    def enable_channel_log(self) -> None: ...

    @property
    def channel_log(self) -> Sequence[ChannelPoint]: ...


@dataclass(frozen=True)
class _Scene:
    """Сцена одной стратегии: её класс, префикс линий и подпись в meta."""

    cls: type[Strategy]
    line_prefix: str
    note: str


_SCENES: Mapping[str, _Scene] = {
    "flat_keltner": _Scene(
        cls=FlatKeltnerStrategy,
        line_prefix="keltner",
        note=(
            "пессимистичная вилка; свечи --tf из 1m-баров базы; канал 5H из "
            "индикаторов стратегии; EXIT — сведение позиции в конце окна"
        ),
    ),
    "trend_pullback": _Scene(
        cls=TrendPullbackStrategy,
        line_prefix="regression",
        note=(
            "пессимистичная вилка; свечи --tf из 1m-баров базы; канал регрессии "
            "5H из индикаторов стратегии; EXIT — сведение позиции в конце окна"
        ),
    ),
    "sweep_liquidity": _Scene(
        cls=SweepLiquidityStrategy,
        line_prefix="range",
        note=(
            "пессимистичная вилка; 1H-свечи с --tf 1h; диапазон 1H из "
            "индикаторов стратегии: lower — минимум Low последних lookback "
            "завершённых 1H-баров (граница сигнала смётки), upper — максимум "
            "High того же окна, mid — полусумма; TP — частичный выход по цели "
            "(TP1 половина, остаток на TP2 или стопе), EXIT — сведение "
            "позиции в конце окна"
        ),
    ),
}


def _parse_ts(text: str) -> int:
    """Время окна — целым Decimal-строкой: никакого float из argparse."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"время не является числом: {text!r}") from None
    if value != value.to_integral_value():
        raise argparse.ArgumentTypeError(f"время должно быть целым: {text!r}")
    return int(value)


def _parse_tf(text: str) -> Interval:
    """Таймфрейм сцены: меткой (15m, 1h) или числом секунд (900, 3600)."""
    if text in _TF_NAMES.values():
        return next(k for k, v in _TF_NAMES.items() if v == text)
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(
            f"таймфрейм не метка и не число секунд: {text!r}"
        ) from None
    if value != value.to_integral_value():
        raise argparse.ArgumentTypeError(f"таймфрейм должен быть целым: {text!r}")
    seconds = int(value)
    for known in _TF_NAMES:
        if seconds == known.value:
            return known
    raise argparse.ArgumentTypeError(
        f"таймфрейм {seconds} не поддержан, разрешены: 900, 3600"
    )


def _tf_name(tf: Interval) -> str:
    """Имя таймфрейма в meta и имени файла по умолчанию."""
    return _TF_NAMES[tf]


def _parse_capital(text: str) -> Decimal:
    """Капитал — Decimal-строкой: никакого float из argparse."""
    try:
        return Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"капитал не Decimal-строка: {text!r}") from None


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


def _defaults(cls: type[Strategy]) -> dict[str, Decimal]:
    """Дефолты параметров стратегии из сигнатуры конструктора.

    Пустой словарь — у стратегии нет параметров с дефолтами.
    """
    defaults: dict[str, Decimal] = {}
    signature = inspect.signature(cls.__init__)
    for name, parameter in signature.parameters.items():
        if name == "self" or parameter.default is inspect.Parameter.empty:
            continue
        value = parameter.default
        if not isinstance(value, Decimal):
            raise ValueError(f"дефолт параметра {name} — не Decimal: {value!r}")
        defaults[name] = value
    return defaults


def _params(name: str, overrides: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Эффективные параметры прогона: дефолты стратегии плюс переопределения."""
    defaults = _defaults(_SCENES[name].cls)
    unknown = sorted(set(overrides) - set(defaults))
    if unknown:
        raise ValueError(f"у стратегии {name} нет таких параметров: {unknown}")
    params = dict(defaults)
    params.update(overrides)
    return params


def _spec(
    symbol: str,
    start_ts: int,
    end_ts: int,
    params: Mapping[str, Decimal],
    name: str,
) -> StrategySpec:
    """Спек прогона: стратегия, символ, окно, эффективные параметры."""
    return StrategySpec(
        name=name,
        symbol=symbol,
        interval=Interval.MINUTE,
        start_ts=start_ts,
        end_ts=end_ts,
        params=dict(params),
        rules_ref=RULES_REF,
    )


def _factory(params: Mapping[str, Decimal], name: str) -> Callable[[], Strategy]:
    """Фабрика стратегии: run_fork вызывает её дважды, каждая вилка со своей стратегией.

    Стратегия собирается реестром `build` — у экспортёра нет иного способа
    получить конструктор с параметрами из спека. Запись канала включается до
    прогона: журнал наполняется по ходу баров.
    """

    def factory() -> Strategy:
        strategy = build(name, params)
        channel = strategy if isinstance(strategy, _ChannelStrategy) else None
        if channel is None:
            raise ValueError(f"стратегия {name} не имеет журнала канала")
        channel.enable_channel_log()
        return strategy

    return factory


def _plain(value: Decimal) -> str:
    """Decimal без экспоненты: файл читается человеком и сравнивается diff-ом."""
    return format(value, "f")


def _plain_or_null(value: Decimal | None) -> str | None:
    """Decimal строкой; None остаётся None — в JSON это null, а не «None»."""
    return None if value is None else _plain(value)


def _number(value: Decimal) -> int | float:
    """Decimal в число JSON: целое — int, дробное — float.

    JSON десятичных не знает, а Lightweight Charts ждёт числа, а не строки.
    """
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _bar_time(ts: int, tf: Interval) -> int:
    """Начало бара `tf`, содержащего момент `ts`: якорь по эпохе Unix."""
    return ts - ts % tf.value


def _complete_windows(
    bars: Sequence[Bar], tf: Interval
) -> list[tuple[int, list[Bar]]]:
    """Окна `tf`, собранные целиком: неполные окна в сцену не идут.

    Окно полно, когда в нём есть все минуты шага: якорь по эпохе у всех
    один и тот же, минуты в окне идут подряд, лишних минут в окне быть не
    может.
    """
    windows: list[tuple[int, list[Bar]]] = []
    for start, group in groupby(bars, key=lambda bar: bar.ts - bar.ts % tf.value):
        members = list(group)
        if len(members) == tf.factor:
            windows.append((start, members))
    return windows


def _tf_bars(bars: Sequence[Bar], tf: Interval) -> list[Bar]:
    """Бары `tf` из минутных: агрегатором, но только по целым окнам.

    Агрегатор `aggregate` строг: неполная группа — исключение, а целая группа,
    идущая после пропущенной, — тоже исключение (пропущенное окно целиком).
    Поэтому окна собираются пачками подряд: в пачке окон нет ни неполного, ни
    пропущенного, и агрегатор проходит её целиком.
    """
    out: list[Bar] = []
    run: list[Bar] = []
    previous_start: int | None = None
    for start, members in _complete_windows(bars, tf):
        if previous_start is not None and start != previous_start + tf.value:
            out.extend(aggregate(run, tf))
            run = []
        run.extend(members)
        previous_start = start
    if run:
        out.extend(aggregate(run, tf))
    return out


def _candles(bars: Sequence[Bar]) -> list[dict[str, Any]]:
    """Свечи сцены: OHLCV бара 15m в форме, которую ждёт Lightweight Charts."""
    return [
        {
            "time": bar.ts,
            "open": _number(bar.open),
            "high": _number(bar.high),
            "low": _number(bar.low),
            "close": _number(bar.close),
            "volume": _number(bar.volume),
        }
        for bar in bars
    ]


def _line(color: str, points: Sequence[tuple[int, Decimal]]) -> dict[str, Any]:
    """Линия сцены: цвет и точки `{time, value}`."""
    return {
        "color": color,
        "points": [{"time": ts, "value": _number(value)} for ts, value in points],
    }


def _lines(channel: Sequence[ChannelPoint], prefix: str) -> dict[str, dict[str, Any]]:
    """Три линии канала стратегии: средняя, верхняя и нижняя границы."""
    return {
        f"{prefix}_mid": _line(COLOR_MID, [(point.ts, point.mid) for point in channel]),
        f"{prefix}_upper": _line(COLOR_UPPER, [(point.ts, point.upper) for point in channel]),
        f"{prefix}_lower": _line(COLOR_LOWER, [(point.ts, point.lower) for point in channel]),
    }


def _exit_marker(trade: Trade, tf: Interval) -> dict[str, Any]:
    """Маркер выхода: цель, стоп или сведение позиции в конце окна."""
    text, color = EXIT_MARKERS.get(trade.exit_reason, ("EXIT", COLOR_EXIT))
    return {
        "time": _bar_time(trade.exit_ts, tf),
        "position": "aboveBar",
        "color": color,
        "shape": "circle",
        "text": text,
    }


def _markers(trades: Sequence[Trade], tf: Interval) -> list[dict[str, Any]]:
    """Маркеры сцены: вход под баром, выход над баром.

    Маркеры идут по времени — так их ждёт Lightweight Charts. Внутри одного
    бара вход стоит раньше выхода: сортировка устойчивая, а порядок
    добавления именно такой.
    """
    markers: list[dict[str, Any]] = []
    for trade in trades:
        markers.append(
            {
                "time": _bar_time(trade.entry_ts, tf),
                "position": "belowBar",
                "color": COLOR_BUY,
                "shape": "arrowUp",
                "text": "BUY",
            }
        )
        markers.append(_exit_marker(trade, tf))
    markers.sort(key=lambda marker: marker["time"])
    return markers


def _meta(
    report: Report,
    symbol: str,
    strategy_name: str,
    params: Mapping[str, Decimal],
    capital: Decimal,
    tf_name: str,
    candle_bars: int,
    note: str,
) -> dict[str, Any]:
    """Шапка сцены: что за прогон, на чём, с каким результатом."""
    metrics = report.metrics
    return {
        "symbol": symbol,
        "interval": tf_name,
        "strategy": strategy_name,
        "params": {name: _plain(params[name]) for name in sorted(params)},
        "capital": _number(capital),
        "spec_version": report.spec_version,
        "intrabar": report.intrabar.name,
        "zones_used": [str(zone) for zone in report.zones_used],
        "net_pnl": _plain(metrics.net_pnl),
        "profit_factor": _plain_or_null(metrics.profit_factor()),
        "trades": metrics.trades,
        "wins": metrics.wins,
        "candle_bars": candle_bars,
        "note": note,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export_flat_keltner.py",
        description=(
            "Экспорт сцены канальной стратегии по окну базы: JSON для "
            "терминала TradingView-Lightweight."
        ),
    )
    parser.add_argument("db", type=Path, help="путь к базе SQLite")
    parser.add_argument("symbol", help="символ, например BTCUSDT")
    parser.add_argument("start_ts", type=_parse_ts, help="начало окна, Unix-секунды")
    parser.add_argument("end_ts", type=_parse_ts, help="конец окна, Unix-секунды")
    parser.add_argument(
        "strategy",
        nargs="?",
        default=DEFAULT_STRATEGY,
        choices=tuple(_SCENES),
        help="канальная стратегия сцены; по умолчанию flat_keltner",
    )
    parser.add_argument(
        "--tf",
        type=_parse_tf,
        default=DEFAULT_TF,
        metavar="ТАЙМФРЕЙМ",
        help=(
            "таймфрейм свечей сцены: 15m или 1h; по умолчанию 15m; "
            "sweep_liquidity живёт на закрытиях 1H-баров"
        ),
    )
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
        type=_parse_param,
        metavar="ИМЯ=ЗНАЧЕНИЕ",
        help="параметр стратегии Decimal-строкой; можно повторять",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="ПУТЬ",
        help="путь к файлу сцены; по умолчанию data/<стратегия>_<символ>_<tf>.json",
    )
    return parser


def _out_path(
    strategy_name: str, symbol: str, out: Path | None, tf: Interval
) -> Path:
    """Файл сцены: заданный путь или путь по умолчанию в data/ репозитория."""
    if out is not None:
        return out
    name = f"{strategy_name}_{symbol.lower()}_{_tf_name(tf)}.json"
    return _ROOT_PATH / "data" / name


def _export(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Прогон пессимистичной вилки и запись сцены в JSON."""
    if not args.db.exists():
        parser.error(f"база не найдена: {args.db}")
    scene = _SCENES[args.strategy]
    try:
        params = _params(args.strategy, dict(args.param))
    except ValueError as error:
        parser.error(str(error))

    spec = _spec(args.symbol, args.start_ts, args.end_ts, params, args.strategy)
    conn = connect(args.db)
    try:
        store = CandleStore(conn, args.symbol)
        minute_bars = list(store.read_1m(args.symbol, args.start_ts, args.end_ts))
        fork = run_fork(
            store,
            spec,
            _factory(params, args.strategy),
            CostModel(),
            capital=args.capital,
            record_trades=True,
        )
    finally:
        conn.close()

    verdict = verdict_line(fork)
    if verdict == VERDICT_NOT_MEASURABLE:
        print(
            f"предупреждение: {verdict} — в сцену выгружена пессимистичная вилка",
            file=sys.stderr,
        )

    report = fork.pessimistic
    strategy = fork.pessimistic_strategy
    channel_strategy = strategy if isinstance(strategy, _ChannelStrategy) else None
    if channel_strategy is None:
        parser.error("вилка вернула стратегию без журнала канала")

    candles = _tf_bars(minute_bars, args.tf)
    channel = channel_strategy.channel_log
    payload: dict[str, Any] = {
        "meta": _meta(
            report,
            args.symbol,
            args.strategy,
            params,
            args.capital,
            _tf_name(args.tf),
            len(candles),
            scene.note,
        ),
        "candles": _candles(candles),
        "lines": _lines(channel, scene.line_prefix),
        "markers": _markers(report.trades, args.tf),
    }

    out = _out_path(args.strategy, args.symbol, args.out, args.tf)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    metrics = report.metrics
    print(f"out: {out}")
    print(f"spec_version: {report.spec_version}")
    print(f"intrabar: {report.intrabar.name}")
    print(f"zones_used: {', '.join(str(zone) for zone in report.zones_used)}")
    print(f"candles: {len(candles)}")
    print(f"channel points: {len(channel)}")
    print(f"trades: {metrics.trades} (wins {metrics.wins})")
    print(f"net_pnl: {_plain(metrics.net_pnl)}")
    print(f"profit_factor: {_plain_or_null(metrics.profit_factor()) or 'null'}")
    print(f"verdict: {verdict}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    _export(parser, args)


if __name__ == "__main__":
    main()
