"""Тесты калибровочного харнесса scripts/calib/harness.py (Task 1, calibration-suite).

Модуль скрипта грузится по пути файла — приём как tests/test_sweep.py::_load_sweep.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from strategylab.broker.costs import CostModel
from strategylab.broker.fork import run_fork
from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar, Interval
from strategylab.data.schema import SCHEMA, connect
from strategylab.data.store import CandleStore
from strategylab.data.zones import Zone, assign_zone
from strategylab.spec.spec import StrategySpec
from strategylab.strategy import examples

SYMBOL = "BTCUSDT"
START_TS = 1717200000
_BASE_PRICE = Decimal("100000")
_DROP = Decimal("5")
_HARNESS = Path(__file__).resolve().parent.parent / "scripts" / "calib" / "harness.py"


def _load_harness() -> ModuleType:
    """Модуль скрипта по пути файла: scripts/ не пакет."""
    spec = importlib.util.spec_from_file_location("calib_harness", _HARNESS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # регистрация до исполнения: dataclass(slots=True) пересобирает класс через sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bars(count: int) -> list[Bar]:
    """Строго монотонные минутные бары (open==high==low==close), всегда глубоко в плюсе."""
    bars: list[Bar] = []
    for index in range(count):
        bar_close = _BASE_PRICE - _DROP * Decimal(index)
        bars.append(
            Bar(
                ts=START_TS + 60 * index,
                open=bar_close,
                high=bar_close,
                low=bar_close,
                close=bar_close,
                volume=Decimal("1"),
            )
        )
    return bars


def _bars_saw(count: int) -> list[Bar]:
    """Зигзаг периода 3 (100000/101000/102000): SMA с коротким окном постоянно кроссит SMA3."""
    pattern = (Decimal("100000"), Decimal("101000"), Decimal("102000"))
    bars: list[Bar] = []
    for index in range(count):
        bar_close = pattern[index % 3]
        bars.append(
            Bar(
                ts=START_TS + 60 * index,
                open=bar_close,
                high=bar_close,
                low=bar_close,
                close=bar_close,
                volume=Decimal("1"),
            )
        )
    return bars


def _make_db(
    path: Path,
    bars: list[Bar],
    end_ts: int,
    zone: Zone | None = Zone.FIT,
) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        if zone is not None:
            assign_zone(conn, SYMBOL, START_TS, end_ts, zone)
        CandleStore(conn, SYMBOL).write(bars)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def harness() -> ModuleType:
    return _load_harness()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """200 строго падающих минутных баров, зона fit на всё окно."""
    path = tmp_path / "btc.db"
    bars = _bars(200)
    _make_db(path, bars, START_TS + 60 * 200)
    return path


@pytest.fixture
def tmp_db_two_bars(tmp_path: Path) -> Path:
    """Ровно 2 падающих бара: одна сделка, окно закрыто сводкой."""
    path = tmp_path / "btc2.db"
    bars = _bars(2)
    _make_db(path, bars, START_TS + 60 * 2)
    return path


@pytest.fixture
def tmp_db_saw(tmp_path: Path) -> Path:
    """200 зигзаг-баров периода 3, зона fit: на sma_cross даёт сделки в каждой ячейке."""
    path = tmp_path / "btcs.db"
    _make_db(path, _bars_saw(200), START_TS + 60 * 200)
    return path


@pytest.fixture
def tmp_db_holdout(tmp_path: Path) -> Path:
    path = tmp_path / "btch.db"
    _make_db(path, _bars(2), START_TS + 60 * 2, zone=Zone.HOLDOUT)
    return path


@pytest.fixture
def tmp_db_unzoned(tmp_path: Path) -> Path:
    path = tmp_path / "btcu.db"
    _make_db(path, _bars(2), START_TS + 60 * 2, zone=None)
    return path


class _LimitStubStrategy:
    """Две далеко-лимитки на баре 0 (неисполнимы) + MARKET на ПОСЛЕДНЕМ баре.

    Снимок после последней заявки пинит V4.2-семантику pending_other, а
    несимметричные счётчики (2 vs 1) убивают перестановку колонок.
    """

    name = "stub_limit"

    @classmethod
    def from_params(cls, params: object) -> _LimitStubStrategy:
        return cls()

    def __init__(self) -> None:
        self._limit_done = False

    def on_bar(self, bar: Bar, state: object) -> list[Order]:
        orders: list[Order] = []
        if not self._limit_done:
            self._limit_done = True
            # две entry-лимитки: счётчик limit_buy должен быть 2, а не симметричен other
            orders.extend(
                (
                    Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("0.0001"), None, False),
                    Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("0.0002"), None, False),
                )
            )
        if bar.ts == START_TS + 60 * 199:
            orders.append(Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False))
        return orders


class _BracketStubStrategy:
    """MARKET BUY на баре 0; на баре 1 — TP LIMIT 105 + STOP_MARKET 95 (обе reduce-only)."""

    name = "stub_bracket"

    @classmethod
    def from_params(cls, params: object) -> _BracketStubStrategy:
        return cls()

    def __init__(self) -> None:
        self._entered = False

    def on_bar(self, bar: Bar, state: object) -> list[Order]:
        if not self._entered:
            self._entered = True
            return [Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False)]
        if bar.ts == START_TS + 60:
            return [
                Order(Side.SELL, OrderKind.LIMIT, Decimal("1"), Decimal("105"), None, True),
                Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("95"), True),
            ]
        return []


def _bars_bracket(wide: bool) -> list[Bar]:
    """Три бара: два плоских по 100, третий с диапазоном (wide: 90-110 трогает TP и STOP)."""
    high = Decimal("110") if wide else Decimal("101")
    low = Decimal("90") if wide else Decimal("99")
    flat = Decimal("100")
    return [
        Bar(START_TS, flat, flat, flat, flat, Decimal("1")),
        Bar(START_TS + 60, flat, flat, flat, flat, Decimal("1")),
        Bar(START_TS + 120, flat, high, low, flat, Decimal("1")),
    ]


def _make_bracket_db(tmp_path: Path, name: str, wide: bool) -> Path:
    path = tmp_path / name
    _make_db(path, _bars_bracket(wide), START_TS + 60 * 3)
    return path


def _patch_bracket(harness: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    real_build = examples.build
    monkeypatch.setitem(harness.STRATEGY_CLASSES, "stub_bracket", _BracketStubStrategy)
    monkeypatch.setattr(
        harness,
        "build",
        lambda name, params: (
            _BracketStubStrategy.from_params(params)
            if name == "stub_bracket"
            else real_build(name, params)
        ),
    )


def test_run_cell_matches_run_fork(harness: ModuleType, tmp_db: Path) -> None:
    end_ts = START_TS + 60 * 200
    outcome = harness.run_cell(
        tmp_db, SYMBOL, "buy_hold", {}, START_TS, end_ts, interval=Interval.MINUTE
    )
    conn = connect(tmp_db)
    try:
        store = CandleStore(conn, SYMBOL)
        spec = StrategySpec(
            name="calib-eq",
            symbol=SYMBOL,
            interval=Interval.MINUTE,
            start_ts=START_TS,
            end_ts=end_ts,
            params={},
            rules_ref="test",
        )
        fork = run_fork(
            store,
            spec,
            lambda: examples.build("buy_hold", {}),
            CostModel(),
            Decimal("10000"),
            record_trades=True,
        )
    finally:
        conn.close()

    pess, opt = outcome.sides
    for report, side in ((fork.pessimistic, pess), (fork.optimistic, opt)):
        assert side.intrabar is report.intrabar
        metrics = report.metrics
        assert side.metrics.trades == metrics.trades
        assert side.metrics.wins == metrics.wins
        assert side.metrics.net_pnl == metrics.net_pnl
        assert side.metrics.equity == metrics.equity
        assert side.metrics.max_drawdown == metrics.max_drawdown
        assert side.trades == tuple(report.trades)
    # sanity: buy&hold на падающем окне — ровно одна сделка и минус
    assert pess.metrics.trades == 1
    assert pess.metrics.net_pnl < 0


def test_outside_fit_raises(
    harness: ModuleType,
    tmp_db_holdout: Path,
    tmp_db_unzoned: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ensure_fit обязан сработать ДО任何 прогонов (design §10): build не зовём никогда
    real_build = examples.build
    calls: list[str] = []
    monkeypatch.setattr(
        harness,
        "build",
        lambda name, params: (calls.append(name), real_build(name, params))[1],
    )
    end_ts = START_TS + 60 * 2
    with pytest.raises(harness.OutsideFitZone):
        harness.run_cell(
            tmp_db_holdout, SYMBOL, "buy_hold", {}, START_TS, end_ts, interval=Interval.MINUTE
        )
    with pytest.raises(harness.OutsideFitZone):
        harness.run_cell(
            tmp_db_unzoned, SYMBOL, "buy_hold", {}, START_TS, end_ts, interval=Interval.MINUTE
        )
    assert calls == []


def test_metric_row_null_markers(harness: ModuleType, tmp_db_two_bars: Path) -> None:
    end_ts = START_TS + 60 * 2
    outcome = harness.run_cell(
        tmp_db_two_bars, SYMBOL, "buy_hold", {}, START_TS, end_ts, interval=Interval.MINUTE
    )
    pess_row, opt_row = harness.cell_rows(outcome, {}, [])
    fields = pess_row.split(",")
    # схема: fork, spec_version, trades, wins, win_rate, net_pnl, equity,
    # max_drawdown, profit_factor, sharpe, pending_limit_buy, pending_other
    assert len(fields) == 12
    assert fields[0] == "pessimistic"
    assert fields[1] == outcome.spec_version
    assert fields[2] == "1"  # trades
    assert fields[3] == "0"  # wins
    assert fields[4] == "0"  # win_rate числится, не null
    assert fields[8] == "0"  # profit_factor при убытке — "0", не null
    assert fields[9] == "null"  # sharpe при одной сделке
    assert fields[10] == "0"  # pending_limit_buy
    assert fields[11] == "0"  # pending_other
    assert opt_row.split(",")[0] == "optimistic"


def test_metric_row_negative_equity(harness: ModuleType, tmp_db_two_bars: Path) -> None:
    end_ts = START_TS + 60 * 2
    # крошечный капитал: убыток одной сделки больше него — equity реально уходит в минус
    outcome = harness.run_cell(
        tmp_db_two_bars,
        SYMBOL,
        "buy_hold",
        {},
        START_TS,
        end_ts,
        capital=Decimal("100"),
        interval=Interval.MINUTE,
    )
    pess_row, _ = harness.cell_rows(outcome, {}, [])
    fields = pess_row.split(",")
    assert fields[5].startswith("-")  # net_pnl без abs/клиппинга
    assert fields[6].startswith("-")  # equity = капитал + net, знак сохранён
    assert fields[6] == format(Decimal("100") + Decimal(fields[5]), "f")  # не клиппинг
    assert Decimal(fields[7]) > 0  # max_drawdown — величина просадки


def test_pending_snapshot_visible(
    harness: ModuleType, tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # build() — ветвления по имени (examples.py:86-98), реестр не читает: патчим атрибут харнесса
    real_build = examples.build
    monkeypatch.setattr(
        harness,
        "build",
        lambda name, params: (
            _LimitStubStrategy.from_params(params)
            if name == "stub_limit"
            else real_build(name, params)
        ),
    )
    end_ts = START_TS + 60 * 200
    outcome = harness.run_cell(
        tmp_db, SYMBOL, "stub_limit", {}, START_TS, end_ts, interval=Interval.MINUTE
    )
    pess, _opt = outcome.sides
    assert len(pess.pending_after) == 3
    assert [order.kind for order in pess.pending_after] == [
        OrderKind.LIMIT, OrderKind.LIMIT, OrderKind.MARKET
    ]
    assert pess.trades == ()
    lines = harness.journal_lines(pess, outcome.spec_version, "")
    assert lines[0] == (
        "spec_version,combination,entry_ts,entry_price,exit_ts,exit_price,"
        "pnl,exit_reason"
    )
    assert len(lines) == 1
    fields = harness.cell_rows(outcome, {}, [])[0].split(",")
    assert fields[2] == "0"  # trades
    assert fields[4] == "null"  # win_rate при нуле сделок (design §10)
    assert fields[8] == "null"  # profit_factor
    assert fields[9] == "null"  # sharpe
    assert fields[10] == "2"  # pending_limit_buy — обе entry-лимитки пережили окно
    # pending_other == 1 доказывает снимок ПОСЛЕ submit последнего бара (V4.2)
    assert fields[11] == "1"


def test_write_csv_utf8(harness: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    harness.write_csv(path, ["fork,spec_version", "ОТКЛОНЕНО x: ValueError: y"])
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw == "fork,spec_version\nОТКЛОНЕНО x: ValueError: y\n".encode()


def test_cli_grid_writes_frozen_artifacts(
    harness: ModuleType, tmp_db_saw: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Замороженный контракт Task 2/3/6/8: имена файлов, колонки, attribution журнала."""
    end_ts = START_TS + 60 * 200
    out = tmp_path / "run"
    harness.main(
        [
            str(tmp_db_saw), SYMBOL, str(START_TS), str(end_ts), "sma_cross",
            "--param", "fast=1,2", "--param", "slow=3", "--interval", "1m", "--out", str(out),
        ]
    )
    stem = f"sma_cross_{SYMBOL}_{START_TS}_{end_ts}"
    assert sorted(p.name for p in out.iterdir()) == sorted(
        f"{stem}{suffix}"
        for suffix in (".csv", "_journal_p.csv", "_journal_o.csv", "_report_p.txt")
    )
    lines = (out / f"{stem}.csv").read_text(encoding="utf-8").splitlines()
    assert "grid: fast=1,2; slow=3" in lines
    assert "отклонено: 0" in lines
    columns = (
        "fork,spec_version,fast,slow,trades,wins,win_rate,net_pnl,equity,"
        "max_drawdown,profit_factor,sharpe,pending_limit_buy,pending_other"
    )
    data = lines[lines.index(columns) + 1 :]
    assert len(data) == 4  # 2 ячейки × 2 вилки
    assert [row.split(",")[0] for row in data] == [
        "pessimistic", "optimistic", "pessimistic", "optimistic"
    ]
    assert [row.split(",")[2:4] for row in data] == [["1", "3"], ["1", "3"], ["2", "3"], ["2", "3"]]
    versions = {row.split(",")[1] for row in data}
    assert len(versions) == 2  # версия спека различает ячейки сетки

    journal = (out / f"{stem}_journal_p.csv").read_text(encoding="utf-8").splitlines()
    assert journal[0] == (
        "spec_version,combination,entry_ts,entry_price,exit_ts,exit_price,pnl,exit_reason"
    )
    pairs = {tuple(row[:2]) for row in csv.reader(journal[1:])}
    # обе ячейки сетки должны оставить в журнале сделки (attribution R2)
    assert {combo for _, combo in pairs} == {"fast=1; slow=3", "fast=2; slow=3"}
    assert {version for version, _ in pairs} == versions

    report = (out / f"{stem}_report_p.txt").read_text(encoding="utf-8")
    assert report.count("# cell: spec_version=") == 2
    assert "комбинаций: 2" in capsys.readouterr().out


def test_cli_rejection_and_double_costs(
    harness: ModuleType,
    tmp_db_saw: Path,
    tmp_db_two_bars: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    end_ts = START_TS + 60 * 200
    out = tmp_path / "rej"
    # fast=99 >= slow=3 → ValueError в build: ячейка ОТКЛОНЕНА, сетка продолжается
    harness.main(
        [
            str(tmp_db_saw), SYMBOL, str(START_TS), str(end_ts), "sma_cross",
            "--param", "fast=1,99", "--param", "slow=3", "--interval", "1m", "--out", str(out),
        ]
    )
    captured = capsys.readouterr()
    assert "ОТКЛОНЕНО fast=99; slow=3: ValueError:" in captured.err
    assert "комбинаций: 2" in captured.out
    assert "отклонено: 1" in captured.out
    lines = (out / f"sma_cross_{SYMBOL}_{START_TS}_{end_ts}.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    assert "отклонено: 1" in lines
    assert len([row for row in lines if row.startswith(("pessimistic,", "optimistic,"))]) == 2

    # все ячейки отклонены → громкий выход, битых артефактов не оставляем
    out2 = tmp_path / "allrej"
    with pytest.raises(SystemExit):
        harness.main(
            [
                str(tmp_db_saw), SYMBOL, str(START_TS), str(end_ts), "sma_cross",
                "--param", "fast=99", "--param", "slow=3",
                "--interval", "1m", "--out", str(out2),
            ]
        )
    assert not out2.exists()

    # --double-costs: суффикс _dc на всех четырёх файлах + удвоенные издержки в шапке
    out3 = tmp_path / "dc"
    end2 = START_TS + 60 * 2
    harness.main(
        [
            str(tmp_db_two_bars), SYMBOL, str(START_TS), str(end2), "buy_hold",
            "--interval", "1m", "--double-costs", "--out", str(out3),
        ]
    )
    stem3 = f"buy_hold_{SYMBOL}_{START_TS}_{end2}_dc"
    assert sorted(p.name for p in out3.iterdir()) == sorted(
        f"{stem3}{suffix}"
        for suffix in (".csv", "_journal_p.csv", "_journal_o.csv", "_report_p.txt")
    )
    csv3 = (out3 / f"{stem3}.csv").read_text(encoding="utf-8")
    assert "costs: taker_fee=0.0012 maker_fee=0.0004 slippage=0.0010 leverage=1" in csv3


def test_cli_fork_provenance(
    harness: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Обе вилки обязаны браться ИМЕННО со своей стороны (D1/D2, свежий-взгляд раунд 2).

    Широкий бар 90-110 при TP=105 и STOP=95: пессимист выходит по стопу,
    оптимист — по тейк-профиту; стороны содержимо различны.
    """
    _patch_bracket(harness, monkeypatch)
    db = _make_bracket_db(tmp_path, "brk.db", wide=True)
    out = tmp_path / "forkprov"
    end_ts = START_TS + 60 * 3
    harness.main(
        [str(db), SYMBOL, str(START_TS), str(end_ts), "stub_bracket",
         "--interval", "1m", "--out", str(out)]
    )
    stem = f"stub_bracket_{SYMBOL}_{START_TS}_{end_ts}"
    jp = (out / f"{stem}_journal_p.csv").read_text(encoding="utf-8").splitlines()
    jo = (out / f"{stem}_journal_o.csv").read_text(encoding="utf-8").splitlines()
    assert len(jp) == 2 and len(jo) == 2  # по одной сделке на вилку
    assert jp[1].endswith(",stop")
    assert jo[1].endswith(",take_profit")
    assert jp[1] != jo[1]
    rows = [
        line
        for line in (out / f"{stem}.csv").read_text(encoding="utf-8").splitlines()
        if line.startswith(("pessimistic,", "optimistic,"))
    ]
    pess_net = rows[0].split(",")[5]
    opt_net = rows[1].split(",")[5]
    assert pess_net == "-5.21450150"
    assert Decimal(opt_net) > 0
    report = (out / f"{stem}_report_p.txt").read_text(encoding="utf-8")
    assert "intrabar: PESSIMISTIC" in report
    assert f"net_pnl: {pess_net}" in report


def test_cli_reduce_only_pending_split(
    harness: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reduce-only LIMIT в финальной очереди — pending_other, НЕ limit_buy (V4.2).

    Узкий бар 99-101 не трогает ни TP 105, ни STOP 95: обе reduce-only заявки
    переживают принудительное закрытие окна (двинок не чистит очередь) и должны
    попасть в pending_other.
    """
    _patch_bracket(harness, monkeypatch)
    db = _make_bracket_db(tmp_path, "nar.db", wide=False)
    out = tmp_path / "ropend"
    end_ts = START_TS + 60 * 3
    harness.main(
        [str(db), SYMBOL, str(START_TS), str(end_ts), "stub_bracket",
         "--interval", "1m", "--out", str(out)]
    )
    stem = f"stub_bracket_{SYMBOL}_{START_TS}_{end_ts}"
    row = next(
        line
        for line in (out / f"{stem}.csv").read_text(encoding="utf-8").splitlines()
        if line.startswith("pessimistic,")
    )
    fields = row.split(",")
    assert fields[10] == "0"  # pending_limit_buy: entry-лимиток нет
    assert fields[11] == "2"  # pending_other: TP-LIMIT + STOP_MARKET, обе reduce-only
    journal = (out / f"{stem}_journal_p.csv").read_text(encoding="utf-8").splitlines()
    assert len(journal) == 2
    assert journal[1].endswith(",market")  # закрыто сводкой окна
