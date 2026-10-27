from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.core.metrics import Metrics
from strategylab.core.trades import Trade
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

    `trades` — закрытые сделки прогона: журнал ведётся по запросу и по
    умолчанию пуст. В текст и JSON отчёта сделки не входят — формат отчёта
    зафиксирован, — но доступны экспорту сцены.
    """

    spec_version: str
    zones_used: tuple[Zone, ...]
    costs: CostModel
    trials: int
    intrabar: IntrabarOrder
    metrics: Metrics
    trades: tuple[Trade, ...] = ()

    def to_text(self) -> str:
        """Текстовый отчёт: ровно пять строк шапки, затем метрики — те же, что в JSON."""
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
            f"profit_factor: {_plain_or_null(m.profit_factor())}",
            f"win_rate: {_plain_or_null(m.win_rate())}",
            f"sharpe: {_plain_or_null(m.sharpe())}",
            f"long_trades: {m.long.trades}",
            f"long_net_pnl: {_plain(m.long.net_pnl)}",
            f"long_profit_factor: {_plain_or_null(m.long.profit_factor())}",
            f"short_trades: {m.short.trades}",
            f"short_net_pnl: {_plain(m.short.net_pnl)}",
            f"short_profit_factor: {_plain_or_null(m.short.profit_factor())}",
        ))

    def to_json(self) -> str:
        """JSON отчёта: фиксированный порядок ключей — пять полей шапки, затем метрики.

        Поля метрик перечислены белым списком: у Metrics есть приватные накопители,
        которые asdict/vars выдали бы в отчёт. Decimal — строкой, None — как null.
        """
        m = self.metrics
        payload: dict[str, Any] = {
            "spec_version": self.spec_version,
            "zones_used": [str(zone) for zone in self.zones_used],
            "costs": {
                "taker_fee": _plain(self.costs.taker_fee),
                "maker_fee": _plain(self.costs.maker_fee),
                "slippage": _plain(self.costs.slippage),
                "leverage": _plain(self.costs.leverage),
            },
            "trials": self.trials,
            "intrabar": self.intrabar.name,
            "trades": m.trades,
            "wins": m.wins,
            "losses": m.losses,
            "net_pnl": _plain(m.net_pnl),
            "equity": _plain(m.equity),
            "max_drawdown": _plain(m.max_drawdown),
            "profit_factor": _optional_plain(m.profit_factor()),
            "win_rate": _optional_plain(m.win_rate()),
            "sharpe": _optional_plain(m.sharpe()),
            "long_trades": m.long.trades,
            "long_net_pnl": _plain(m.long.net_pnl),
            "long_profit_factor": _optional_plain(m.long.profit_factor()),
            "short_trades": m.short.trades,
            "short_net_pnl": _plain(m.short.net_pnl),
            "short_profit_factor": _optional_plain(m.short.profit_factor()),
        }
        return json.dumps(payload)


def build_report(
    spec: StrategySpec,
    zones_used: tuple[Zone, ...] | None,
    costs: CostModel | None,
    trials: int | None,
    intrabar: IntrabarOrder,
    metrics: Metrics,
    trades: tuple[Trade, ...] = (),
) -> Report:
    """Собирает отчёт прогона; неполная шапка — исключение, а не отчёт.

    Проверяются ровно первые четыре поля шапки: spec_version, zones_used,
    costs, trials. metrics в проверке не участвует: его тип не Optional,
    и None здесь был бы нарушением типа, а не случаем времени выполнения.
    Журнал сделок — необязательный: без него отчёт пуст по сделкам.
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
        trades=trades,
    )


def _plain(value: Decimal) -> str:
    """Decimal без экспоненты: отчёты сравнивают по diff, «1E-7» не нужен."""
    return format(value, "f")


def _optional_plain(value: Decimal | None) -> str | None:
    """Decimal строкой; None остаётся None — в JSON это null, а не «Infinity»."""
    return None if value is None else _plain(value)


def _plain_or_null(value: Decimal | None) -> str:
    """Decimal строкой; None — маркером null, как в JSON, а не «None»."""
    return "null" if value is None else _plain(value)
