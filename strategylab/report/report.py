from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.core.metrics import Metrics
from strategylab.data.zones import Zone
from strategylab.spec.spec import StrategySpec


class MissingReportHeader(Exception):
    """Отчёт не собран: шапка provenance неполна.

    Отчёт без шапки не собирается — это инвариант 7: проверяемо механически,
    а не обещанием.
    """


@dataclass(frozen=True, slots=True)
class Report:
    """Артефакт прогона: обязательная шапка provenance плюс метрики.

    Заморожен: отчёт — воспоминание о моменте разметки, а не живой объект.
    zones_used — кортеж, а не список: неизменяемость выражена в типе.
    """

    spec_version: str
    zones_used: tuple[Zone, ...]
    costs: CostModel
    trials: int
    intrabar: IntrabarOrder
    metrics: Metrics

    def to_text(self) -> str:
        """Текстовый отчёт: ровно пять строк шапки, метрики ниже."""
        m = self.metrics
        return "\n".join((
            f"spec_version: {self.spec_version}",
            f"zones_used: {', '.join(str(zone) for zone in self.zones_used)}",
            (
                f"costs: taker_fee={_plain(self.costs.taker_fee)}"
                f" maker_fee={_plain(self.costs.maker_fee)}"
                f" slippage={_plain(self.costs.slippage)}"
                f" leverage={_plain(self.costs.leverage)}"
            ),
            f"trials: {self.trials}",
            f"intrabar: {self.intrabar.name}",
            f"trades: {m.trades}",
            f"wins: {m.wins}",
            f"losses: {m.losses}",
            f"net_pnl: {_plain(m.net_pnl)}",
            f"equity: {_plain(m.equity)}",
            f"max_drawdown: {_plain(m.max_drawdown)}",
        ))


def build_report(
    spec: StrategySpec,
    zones_used: tuple[Zone, ...] | None,
    costs: CostModel | None,
    trials: int | None,
    intrabar: IntrabarOrder,
    metrics: Metrics,
) -> Report:
    """Собирает отчёт прогона; неполная шапка — исключение, а не отчёт.

    Проверяются ровно первые четыре поля шапки: spec_version, zones_used,
    costs, trials. metrics в проверке не участвует: его тип не Optional,
    и None здесь был бы нарушением типа, а не случаем времени выполнения.
    """
    spec_version: str | None = spec.version
    if spec_version is None or zones_used is None or costs is None or trials is None:
        missing = ", ".join(
            name
            for name, value in (
                ("spec_version", spec_version),
                ("zones_used", zones_used),
                ("costs", costs),
                ("trials", trials),
            )
            if value is None
        )
        raise MissingReportHeader(f"missing report header: {missing}")
    return Report(
        spec_version=spec_version,
        zones_used=zones_used,
        costs=costs,
        trials=trials,
        intrabar=intrabar,
        metrics=metrics,
    )


def _plain(value: Decimal) -> str:
    """Decimal без экспоненты: отчёты сравнивают по diff, «1E-7» не нужен."""
    return format(value, "f")
