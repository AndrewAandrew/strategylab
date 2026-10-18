from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
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
    assert len(lines) == 11
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
