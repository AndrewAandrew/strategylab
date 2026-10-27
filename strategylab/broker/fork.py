from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.core.trades import TradeLog
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, zones_touched
from strategylab.drivers.replay import ReplayDriver
from strategylab.report.report import Report, build_report
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.base import Strategy

# Фиксированные параметры: поиска не было, испытание одно. Вилки
# внутрибазового порядка — не испытания, а два прогона одного окна.
_TRIALS = 1


@dataclass(frozen=True, slots=True)
class Fork:
    """Одно окно в двух внутрибазовых порядках: пессимистичный и оптимистичный отчёты."""

    pessimistic: Report
    optimistic: Report
    pessimistic_strategy: Strategy | None = None
    optimistic_strategy: Strategy | None = None

    def diverges(self, verdict: Callable[[Report], bool]) -> bool:
        """True, если вердикт на вилках расходится.

        Исключение вердикта — ошибка вердикта, а не расхождение: наружу, без обёртки.
        """
        return verdict(self.pessimistic) != verdict(self.optimistic)


def run_fork(
    store: CandleStore,
    spec: StrategySpec,
    strategy: Callable[[], Strategy],
    costs: CostModel,
    capital: Decimal = Decimal("0"),
    record_trades: bool = False,
) -> Fork:
    """Два прогона одного окна: PESSIMISTIC и OPTIMISTIC, каждая со своей стратегией.

    Фабрика стратегии вызывается дважды из одного spec: состоятельная стратегия
    на второй вилке обязана начать с начала, а не продолжить с середины. Зоны
    шапки — от `zones_touched` по окну спека, а не от драйвера: драйвер зон не
    видит и видеть не должен.

    `capital` — начальный капитал контракта: он входит в equity метрик, и
    стратегия с риск-сайзингом видит его в `StrategyState.equity`. Ноль —
    прогон без капитала: стратегия с риск-сайзингом не торгует вовсе.

    `record_trades` включает журнал закрытых сделок: отчёт получает список
    сделок с временем и ценами входа и выхода. Без него журнала нет вовсе и
    метрики прогона отличаются настолько же, на сколько отличаются от прогона
    с журналом: никак.

    Обе стратегии создаются до прогонов — так вилка отдаёт наружу ту
    стратегию, на которой шла её ветка, и экспорт сцены берёт состояние
    именно её.
    """
    zones = zones_touched(store.conn, spec.symbol, spec.start_ts, spec.end_ts)
    pessimistic_strategy = strategy()
    optimistic_strategy = strategy()
    return Fork(
        pessimistic=_run_once(
            store,
            spec,
            pessimistic_strategy,
            costs,
            IntrabarOrder.PESSIMISTIC,
            zones,
            capital,
            record_trades,
        ),
        optimistic=_run_once(
            store,
            spec,
            optimistic_strategy,
            costs,
            IntrabarOrder.OPTIMISTIC,
            zones,
            capital,
            record_trades,
        ),
        pessimistic_strategy=pessimistic_strategy,
        optimistic_strategy=optimistic_strategy,
    )


def _run_once(
    store: CandleStore,
    spec: StrategySpec,
    strategy: Strategy,
    costs: CostModel,
    intrabar: IntrabarOrder,
    zones: tuple[Zone, ...],
    capital: Decimal,
    record_trades: bool,
) -> Report:
    feed = ReplayDriver(store, spec.symbol, spec.interval, spec.start_ts, spec.end_ts)
    broker = BrokerSimulator(costs, intrabar)
    log = TradeLog() if record_trades else None
    metrics = Engine().run(feed, strategy, broker, Metrics(equity=capital), log)
    trades = () if log is None else log.trades
    return build_report(spec, zones, costs, _TRIALS, intrabar, metrics, trades)
