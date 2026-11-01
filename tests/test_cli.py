from decimal import Decimal
from pathlib import Path

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.fork import Fork
from strategylab.cli import (
    DEFAULT_RULES_REF,
    main,
    verdict_line,
)
from strategylab.core.bars import Bar, Interval
from strategylab.core.metrics import Metrics
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.data.zones import InmutableZoneError, Zone, assign_zone
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
    # Капитал 100 000 покрывает entry-notional 1 BTC (~67 645): без него гейт
    # подачи (фикс B/E) отклонил бы oversized-заявку. net_pnl и max_drawdown от
    # капитала не зависят; equity = капитал + net_pnl.
    main(
        [
            "run", str(db), SYMBOL, "MINUTE", str(START_TS), str(END_TS), "buy_hold",
            "--capital", "100000",
        ]
    )
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
            "net_pnl: -127.083013460",
            "equity: 99872.916986540",
            "max_drawdown: 127.083013460",
            "profit_factor: 0",
            "win_rate: 0",
            "sharpe: null",
        ]
        + header_lines(spec.version)
        + [
            "intrabar: OPTIMISTIC",
            "trades: 1",
            "wins: 0",
            "losses: 1",
            "net_pnl: -127.083013460",
            "equity: 99872.916986540",
            "max_drawdown: 127.083013460",
            "profit_factor: 0",
            "win_rate: 0",
            "sharpe: null",
        ]
        + ["ВЕРДИКТ: измерима"]
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
    assert lines[14:18] == header_lines(spec.version)
    assert lines[18] == "intrabar: OPTIMISTIC"
    assert lines[19] == "trades: 0"
    assert lines[28] == "ВЕРДИКТ: измерима"


# run: начальный капитал контракта

# Серия для flat_keltner: пила без направления (ADX держится нулём) и проколка
# вниз в последней минуте 15m-бара — это сигнал на вход.
FLAT_WARMUP = 1529
FLAT_TAIL = 30
FLAT_END_TS = START_TS + 60 * (FLAT_WARMUP + 1 + FLAT_TAIL)
FLAT_PRICE = Decimal("100")
FLAT_SPAN = Decimal("0.5")
FLAT_DIP_LOW = Decimal("96.5")


def _flat_bars() -> list[Bar]:
    """Минутные бары серии: пила без направления, затем проколка вниз."""
    flat = [
        Bar(
            ts=START_TS + 60 * index,
            open=FLAT_PRICE,
            high=FLAT_PRICE + FLAT_SPAN,
            low=FLAT_PRICE - FLAT_SPAN,
            close=FLAT_PRICE,
            volume=Decimal("1"),
        )
        for index in range(FLAT_WARMUP)
    ]
    flat.append(
        Bar(
            ts=START_TS + 60 * FLAT_WARMUP,
            open=FLAT_PRICE,
            high=FLAT_PRICE,
            low=FLAT_DIP_LOW,
            close=FLAT_PRICE,
            volume=Decimal("1"),
        )
    )
    flat += [
        Bar(
            ts=START_TS + 60 * index,
            open=FLAT_PRICE,
            high=FLAT_PRICE + FLAT_SPAN,
            low=FLAT_PRICE - FLAT_SPAN,
            close=FLAT_PRICE,
            volume=Decimal("1"),
        )
        for index in range(FLAT_WARMUP + 1, FLAT_WARMUP + 1 + FLAT_TAIL)
    ]
    return flat


def make_flat_db(path: Path) -> None:
    """База с серией flat_keltner: зона fit на всё окно прогона."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        assign_zone(conn, SYMBOL, START_TS, FLAT_END_TS, Zone.FIT)
        store = CandleStore(conn, SYMBOL)
        store.write(_flat_bars())
        conn.commit()
    finally:
        conn.close()


def _flat_argv(db: Path, *extra: str) -> list[str]:
    """Аргументы CLI для flat_keltner: окно серии и короткие длины индикаторов."""
    return [
        "run",
        str(db),
        SYMBOL,
        "MINUTE",
        str(START_TS),
        str(FLAT_END_TS),
        "flat_keltner",
        "--param",
        "adx_len=3",
        "--param",
        "ema_len=3",
        "--param",
        "atr_len_regime=3",
        "--param",
        "atr_len_exit=2",
        *extra,
    ]


def test_run_trades_when_capital_is_given(tmp_path: Path, capsys) -> None:
    """С капиталом риск-сайзинг открывает позицию: сделка есть, капитал в equity."""
    db = tmp_path / "flat.db"
    make_flat_db(db)

    main(_flat_argv(db, "--capital", "10000"))

    lines = capsys.readouterr().out.splitlines()
    assert lines[5] == "trades: 1"
    assert lines[19] == "trades: 1"
    # Капитал в equity: 10000 старта и −8.8 по сделке (комиссии обеих сторон
    # и увод сведённого филла), просадка — от капитала.
    assert lines[9] == "equity: 9991.200000000"
    assert lines[10] == "max_drawdown: 8.800000000"


def test_run_without_capital_trades_by_default(tmp_path: Path, capsys) -> None:
    """Без флага капитал дефолтный: стратегия с риск-сайзингом торгует."""
    db = tmp_path / "flat.db"
    make_flat_db(db)

    main(_flat_argv(db))

    lines = capsys.readouterr().out.splitlines()
    assert lines[5] == "trades: 1"
    assert lines[19] == "trades: 1"


def test_run_does_not_trade_with_zero_capital(tmp_path: Path, capsys) -> None:
    """Капитал 0 — прогон без капитала: размер сделки нулевой, сделок нет."""
    db = tmp_path / "flat.db"
    make_flat_db(db)

    main(_flat_argv(db, "--capital", "0"))

    lines = capsys.readouterr().out.splitlines()
    assert lines[5] == "trades: 0"
    assert lines[19] == "trades: 0"
    assert lines[9] == "equity: 0"


def test_run_rejects_a_capital_that_is_not_a_decimal(tmp_path: Path) -> None:
    """Капитал не Decimal-строка — ошибка argparse с кодом 2."""
    db = tmp_path / "flat.db"
    make_flat_db(db)

    with pytest.raises(SystemExit) as exit_info:
        main(_flat_argv(db, "--capital", "десять тысяч"))

    assert exit_info.value.code == 2


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
    assert verdict_line(_fork(Decimal("1"), Decimal("2"))) == "ВЕРДИКТ: измерима"


def test_verdict_line_is_not_measurable_when_branches_diverge() -> None:
    assert verdict_line(_fork(Decimal("-1"), Decimal("1"))) == (
        "ВЕРДИКТ: НЕ ИЗМЕРИМА — вилки расходятся по вердикту"
    )


# init: создание схемы базы


def _table_names(db: Path) -> set[str]:
    """Имена таблиц базы: проверка идёт по sqlite_master, а не по коду команды."""
    conn = connect(db)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def test_init_creates_the_schema_on_a_new_database(tmp_path: Path, capsys) -> None:
    db = tmp_path / "new.db"
    main(["init", str(db)])
    out = capsys.readouterr().out

    assert f"схема готова: {db}" in out
    assert {"bars_1m", "zones"} <= _table_names(db)


def test_init_on_an_existing_database_keeps_its_data(tmp_path: Path) -> None:
    """Повторный init — no-op: SCHEMA идемпотентна, данные базы не теряются."""
    db = tmp_path / "twice.db"
    main(["init", str(db)])
    conn = connect(db)
    try:
        assign_zone(conn, SYMBOL, START_TS, END_TS, Zone.FIT)
        conn.commit()
    finally:
        conn.close()

    main(["init", str(db)])

    conn = connect(db)
    try:
        rows = conn.execute("SELECT symbol, start_ts, end_ts, zone FROM zones").fetchall()
    finally:
        conn.close()
    assert [(row[0], row[1], row[2], row[3]) for row in rows] == [
        (SYMBOL, START_TS, END_TS, "fit")
    ]


# zone: объявление диапазона зоны


def test_zone_declares_the_range(tmp_path: Path, capsys) -> None:
    db = tmp_path / "zones.db"
    main(["init", str(db)])
    main(["zone", str(db), SYMBOL, str(START_TS), str(END_TS), "fit"])
    out = capsys.readouterr().out

    assert SYMBOL in out
    assert "fit" in out
    conn = connect(db)
    try:
        row = conn.execute("SELECT symbol, start_ts, end_ts, zone FROM zones").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert (row[0], row[1], row[2], row[3]) == (SYMBOL, START_TS, END_TS, "fit")


def test_zone_refuses_a_range_intersecting_an_existing_one(tmp_path: Path) -> None:
    """Пересечение диапазонов — ошибка домена, а не молчаливая перезапись."""
    db = tmp_path / "zones.db"
    main(["init", str(db)])
    main(["zone", str(db), SYMBOL, str(START_TS), str(END_TS), "fit"])

    with pytest.raises(InmutableZoneError):
        main(["zone", str(db), SYMBOL, str(START_TS + 60), str(END_TS + 60), "holdout"])


def test_zone_refuses_an_empty_range(tmp_path: Path) -> None:
    db = tmp_path / "zones.db"
    main(["init", str(db)])

    with pytest.raises(ValueError):
        main(["zone", str(db), SYMBOL, str(END_TS), str(END_TS), "fit"])


def test_zone_rejects_an_unknown_zone_name(tmp_path: Path) -> None:
    """Имя зоны вне Zone отклоняется argparse с кодом 2, как и прочие аргументы."""
    db = tmp_path / "zones.db"
    main(["init", str(db)])

    with pytest.raises(SystemExit) as exit_info:
        main(["zone", str(db), SYMBOL, str(START_TS), str(END_TS), "train"])

    assert exit_info.value.code == 2
