from decimal import Decimal
from pathlib import Path

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.fork import Fork
from strategylab.cli import (
    DEFAULT_RULES_REF,
    VERDICT_MEASURABLE,
    VERDICT_NOT_MEASURABLE,
    main,
    verdict_line,
)
from strategylab.core.bars import Bar, Interval
from strategylab.core.metrics import Metrics
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, assign_zone
from strategylab.report.report import Report, build_report
from strategylab.spec.spec import StrategySpec

SYMBOL = "BTCUSDT"
START_TS = 1717200000  # 2024-06-01T00:00:00Z
END_TS = 1717200300  # 2024-06-01T00:05:00Z

# Пять 1m-баров окна спеки §13.1: третий — реальный бар 2024-06-01
# (O=67659.5, H=67735.6, L=67650.0).
REAL_BARS = (
    (1717200000, "67640.0", "67650.0", "67630.0", "67645.0"),
    (1717200060, "67645.0", "67670.0", "67640.0", "67663.2"),
    (1717200120, "67659.5", "67735.6", "67650.0", "67663.2"),
    (1717200180, "67663.2", "67680.0", "67650.0", "67670.0"),
    (1717200240, "67670.0", "67690.0", "67660.0", "67685.0"),
)


def make_db(path: Path) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        assign_zone(conn, SYMBOL, START_TS, END_TS, Zone.FIT)
        store = CandleStore(conn, SYMBOL)
        store.write(
            [
                Bar(
                    ts=ts,
                    open=Decimal(o),
                    high=Decimal(h),
                    low=Decimal(low),
                    close=Decimal(c),
                    volume=Decimal("1.5"),
                )
                for ts, o, h, low, c in REAL_BARS
            ]
        )
        conn.commit()
    finally:
        conn.close()


def make_spec(
    name: str = "buy_hold", params: dict[str, Decimal] | None = None
) -> StrategySpec:
    return StrategySpec(
        name=name,
        symbol=SYMBOL,
        interval=Interval.MINUTE,
        start_ts=START_TS,
        end_ts=END_TS,
        params={} if params is None else params,
        rules_ref=DEFAULT_RULES_REF,
    )


def header_lines(spec_version: str) -> list[str]:
    return [
        f"spec_version: {spec_version}",
        "zones_used: fit",
        "costs: taker_fee=0.0006 maker_fee=0.0002 slippage=0.0005 leverage=1",
        "trials: 1",
    ]


def test_run_prints_both_reports_and_verdict(tmp_path: Path, capsys) -> None:
    db = tmp_path / "bars.db"
    make_db(db)
    main(["run", str(db), SYMBOL, "MINUTE", str(START_TS), str(END_TS), "buy_hold"])
    out = capsys.readouterr().out
    spec = make_spec()
    # buy_hold: вход рынком на втором баре (67663.2 + slippage), сведение на закрытии
    # пятого бара (67685.0); обе вилки исполняют одинаково — расхождения нет.
    expected = (
        header_lines(spec.version)
        + [
            "intrabar: PESSIMISTIC",
            "trades: 1",
            "wins: 0",
            "losses: 1",
            "net_pnl: -52.64260",
            "equity: -52.64260",
            "max_drawdown: 52.64260",
        ]
        + header_lines(spec.version)
        + [
            "intrabar: OPTIMISTIC",
            "trades: 1",
            "wins: 0",
            "losses: 1",
            "net_pnl: -52.64260",
            "equity: -52.64260",
            "max_drawdown: 52.64260",
        ]
        + [VERDICT_MEASURABLE]
    )
    assert out.splitlines() == expected


def test_run_parses_strategy_params_as_decimal_strings(tmp_path: Path, capsys) -> None:
    db = tmp_path / "bars.db"
    make_db(db)
    main(
        [
            "run",
            str(db),
            SYMBOL,
            "MINUTE",
            str(START_TS),
            str(END_TS),
            "sma_cross",
            "--param",
            "fast=2",
            "--param",
            "slow=3",
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    spec = make_spec("sma_cross", {"fast": Decimal("2"), "slow": Decimal("3")})
    assert lines[0:4] == header_lines(spec.version)
    assert lines[4] == "intrabar: PESSIMISTIC"
    assert lines[5] == "trades: 0"
    assert lines[11:15] == header_lines(spec.version)
    assert lines[15] == "intrabar: OPTIMISTIC"
    assert lines[16] == "trades: 0"
    assert lines[22] == VERDICT_MEASURABLE


def _fork(pessimistic_pnl: Decimal, optimistic_pnl: Decimal) -> Fork:
    spec = make_spec()

    def branch(pnl: Decimal, intrabar: IntrabarOrder) -> Report:
        return build_report(
            spec, (Zone.FIT,), CostModel(), 2, intrabar, Metrics(net_pnl=pnl)
        )

    return Fork(
        pessimistic=branch(pessimistic_pnl, IntrabarOrder.PESSIMISTIC),
        optimistic=branch(optimistic_pnl, IntrabarOrder.OPTIMISTIC),
    )


def test_verdict_line_is_measurable_when_branches_agree() -> None:
    assert verdict_line(_fork(Decimal("1"), Decimal("2"))) == VERDICT_MEASURABLE


def test_verdict_line_is_not_measurable_when_branches_diverge() -> None:
    assert verdict_line(_fork(Decimal("-1"), Decimal("1"))) == VERDICT_NOT_MEASURABLE
