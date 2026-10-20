import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Side
from strategylab.core.bars import Interval
from strategylab.core.metrics import Metrics
from strategylab.data.zones import Zone
from strategylab.report.report import MissingReportHeader, Report, build_report
from strategylab.spec.spec import StrategySpec


def make_spec() -> StrategySpec:
    return StrategySpec(
        name="ema_cross",
        symbol="BTCUSDT",
        interval=Interval.MINUTE,
        start_ts=1_717_200_000,
        end_ts=1_717_203_600,
        params={"fast": Decimal("9"), "slow": Decimal("21")},
        rules_ref="rules/ema-cross.md",
    )


def test_report_refuses_missing_spec_version() -> None:
    # Ветка оборонительная: у настоящего StrategySpec version всегда str,
    # поэтому объект с version=None приходит снаружи типа.
    with pytest.raises(MissingReportHeader):
        build_report(
            spec=cast(Any, SimpleNamespace(version=None)),
            zones_used=(Zone.FIT,),
            costs=CostModel(),
            trials=1,
            intrabar=IntrabarOrder.PESSIMISTIC,
            metrics=Metrics(),
        )


def test_report_refuses_missing_zones_used() -> None:
    with pytest.raises(MissingReportHeader):
        build_report(
            spec=make_spec(),
            zones_used=None,
            costs=CostModel(),
            trials=1,
            intrabar=IntrabarOrder.PESSIMISTIC,
            metrics=Metrics(),
        )


def test_report_refuses_missing_costs() -> None:
    with pytest.raises(MissingReportHeader):
        build_report(
            spec=make_spec(),
            zones_used=(Zone.FIT,),
            costs=None,
            trials=1,
            intrabar=IntrabarOrder.PESSIMISTIC,
            metrics=Metrics(),
        )


def test_report_refuses_missing_trials() -> None:
    with pytest.raises(MissingReportHeader):
        build_report(
            spec=make_spec(),
            zones_used=(Zone.FIT,),
            costs=CostModel(),
            trials=None,
            intrabar=IntrabarOrder.PESSIMISTIC,
            metrics=Metrics(),
        )


def test_to_text_puts_header_in_first_five_lines() -> None:
    spec = make_spec()
    report = build_report(
        spec=spec,
        zones_used=(Zone.FIT, Zone.HOLDOUT),
        costs=CostModel(),
        trials=7,
        intrabar=IntrabarOrder.OPTIMISTIC,
        metrics=Metrics(),
    )
    assert isinstance(report, Report)
    lines = report.to_text().splitlines()
    assert len(lines) == 14
    assert lines[0] == f"spec_version: {spec.version}"
    assert lines[1] == "zones_used: fit, holdout"
    assert lines[2].startswith("costs: taker_fee=")
    assert "leverage=1" in lines[2]
    assert lines[3] == "trials: 7"
    assert lines[4] == "intrabar: OPTIMISTIC"
    assert lines[5] == "trades: 0"
    assert lines[6] == "wins: 0"
    assert lines[7] == "losses: 0"
    assert lines[8] == "net_pnl: 0"
    assert lines[9] == "equity: 0"
    assert lines[10] == "max_drawdown: 0"
    assert lines[11] == "profit_factor: null"
    assert lines[12] == "win_rate: null"
    assert lines[13] == "sharpe: null"


def make_report_with_metrics(
    trades: int = 0,
    wins: int = 0,
    losses: int = 0,
) -> Report:
    """Отчёт с метриками, собранными записью fills: выигрыш +10+i, убыток -(10+j)."""
    if wins + losses > trades:
        raise ValueError("wins + losses cannot exceed trades")
    metrics = Metrics()
    fill = Fill(
        ts=1_717_200_000,
        side=Side.SELL,
        price=Decimal("100"),
        qty=Decimal("1"),
        fee=Decimal("0"),
        reason="close",
    )
    for index in range(wins):
        metrics.record(fill, Decimal(10 + index))
    for index in range(losses):
        metrics.record(fill, Decimal(-(10 + index)))
    for _ in range(trades - wins - losses):
        metrics.record(fill, Decimal("0"))
    return build_report(
        spec=make_spec(),
        zones_used=(Zone.FIT,),
        costs=CostModel(),
        trials=1,
        intrabar=IntrabarOrder.PESSIMISTIC,
        metrics=metrics,
    )


def test_to_text_includes_the_ratio_metrics_with_the_json_values() -> None:
    report = make_report_with_metrics(trades=2, wins=1, losses=1)
    lines = report.to_text().splitlines()
    assert lines[-3:] == ["profit_factor: 1", "win_rate: 0.5", "sharpe: 0"]


def test_profit_factor_serialises_as_null() -> None:
    wins_only = make_report_with_metrics(trades=3, wins=3)
    payload = json.loads(wins_only.to_json())
    assert payload["profit_factor"] is None
    assert '"profit_factor": null' in wins_only.to_json()

    with_losses = make_report_with_metrics(trades=2, wins=1, losses=1)
    payload = json.loads(with_losses.to_json())
    assert payload["profit_factor"] == "1"
    assert '"profit_factor": "1"' in with_losses.to_json()


def test_strategy_that_never_trades_still_produces_a_report() -> None:
    idle = make_report_with_metrics(trades=0)
    payload = json.loads(idle.to_json())
    assert payload["trades"] == 0
    assert payload["profit_factor"] is None
    assert payload["win_rate"] is None
    assert payload["sharpe"] is None
    assert '"trades": 0' in idle.to_json()

    traded = make_report_with_metrics(trades=2, wins=1, losses=1)
    payload = json.loads(traded.to_json())
    assert payload["trades"] == 2
    assert payload["win_rate"] == "0.5"
    assert payload["sharpe"] == "0"


def test_single_bar_window_does_not_divide_by_zero() -> None:
    # Окно из одного бара вмещает одну сделку: pnl-серия длиной 1.
    one_trade = make_report_with_metrics(trades=1, wins=1, losses=0)
    assert one_trade.metrics.sharpe() is None
    assert one_trade.metrics.profit_factor() is None
    payload = json.loads(one_trade.to_json())
    assert payload["sharpe"] is None
    assert payload["profit_factor"] is None

    # Две сделки с разбросом pnl: обе метрики — конкретные числа.
    two_trades = make_report_with_metrics(trades=2, wins=1, losses=1)
    assert two_trades.metrics.sharpe() is not None
    assert two_trades.metrics.profit_factor() is not None
    payload = json.loads(two_trades.to_json())
    assert payload["sharpe"] is not None
    assert payload["profit_factor"] is not None


def test_zone_boundaries_in_header() -> None:
    first_zone = build_report(
        spec=make_spec(),
        zones_used=(Zone.FIT,),
        costs=CostModel(),
        trials=1,
        intrabar=IntrabarOrder.PESSIMISTIC,
        metrics=Metrics(),
    )
    assert "zones_used: fit" in first_zone.to_text().splitlines()
    assert json.loads(first_zone.to_json())["zones_used"] == ["fit"]

    all_zones = build_report(
        spec=make_spec(),
        zones_used=(Zone.FIT, Zone.HOLDOUT, Zone.LIVE),
        costs=CostModel(),
        trials=1,
        intrabar=IntrabarOrder.PESSIMISTIC,
        metrics=Metrics(),
    )
    assert "zones_used: fit, holdout, live" in all_zones.to_text().splitlines()
    assert json.loads(all_zones.to_json())["zones_used"] == ["fit", "holdout", "live"]


def test_json_key_order_is_fixed() -> None:
    report = make_report_with_metrics(trades=2, wins=1, losses=1)
    payload = json.loads(report.to_json())
    assert list(payload) == [
        "spec_version",
        "zones_used",
        "costs",
        "trials",
        "intrabar",
        "trades",
        "wins",
        "losses",
        "net_pnl",
        "equity",
        "max_drawdown",
        "profit_factor",
        "win_rate",
        "sharpe",
    ]
    assert list(payload["costs"]) == ["taker_fee", "maker_fee", "slippage", "leverage"]
