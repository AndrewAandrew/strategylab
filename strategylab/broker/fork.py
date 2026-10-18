from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, zones_touched
from strategylab.drivers.replay import ReplayDriver
from strategylab.report.report import Report, build_report
from strategylab.spec.spec import StrategySpec
from strategylab.strategy.base import Strategy

_TRIALS = 2


@dataclass(frozen=True, slots=True)
class Fork:
    """Одно окно в двух внутрибазовых порядках: пессимистичный и оптимистичный отчёты."""

    pessimistic: Report
    optimistic: Report

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
) -> Fork:
    """Два прогона одного окна: PESSIMISTIC и OPTIMISTIC, каждая со своей стратегией.

    Фабрика стратегии вызывается дважды из одного spec: состоятельная стратегия
    на второй вилке обязана начать с начала, а не продолжить с середины. Зоны
    шапки — от `zones_touched` по окну спека, а не от драйвера: драйвер зон не
    видит и видеть не должен.
    """
    zones = zones_touched(store.conn, spec.symbol, spec.start_ts, spec.end_ts)
    return Fork(
        pessimistic=_run_once(store, spec, strategy, costs, IntrabarOrder.PESSIMISTIC, zones),
        optimistic=_run_once(store, spec, strategy, costs, IntrabarOrder.OPTIMISTIC, zones),
    )


def _run_once(
    store: CandleStore,
    spec: StrategySpec,
    strategy: Callable[[], Strategy],
    costs: CostModel,
    intrabar: IntrabarOrder,
    zones: tuple[Zone, ...],
) -> Report:
    feed = ReplayDriver(store, spec.symbol, spec.interval, spec.start_ts, spec.end_ts)
    broker = BrokerSimulator(costs, intrabar)
    metrics = Engine().run(feed, strategy(), broker, Metrics())
    return build_report(spec, zones, costs, _TRIALS, intrabar, metrics)
