from decimal import Decimal

from strategylab.broker.orders import Fill, Side
from strategylab.core.metrics import Metrics


def make_fill() -> Fill:
    return Fill(
        ts=1_717_200_000,
        side=Side.BUY,
        price=Decimal("50000"),
        qty=Decimal("1"),
        fee=Decimal("0"),
        reason="close",
    )


def record(m: Metrics, pnl: str) -> None:
    m.record(make_fill(), pnl=Decimal(pnl))


def test_empty_metrics_has_zero_fields_and_none_ratios() -> None:
    m = Metrics()
    assert m.trades == 0
    assert m.wins == 0
    assert m.losses == 0
    assert m.net_pnl == Decimal("0")
    assert m.equity == Decimal("0")
    assert m.max_drawdown == Decimal("0")
    assert m.profit_factor() is None
    assert m.win_rate() is None
    assert m.sharpe() is None


def test_profit_factor_is_none_without_losses() -> None:
    m = Metrics()
    record(m, "10")
    record(m, "5")
    assert m.profit_factor() is None


def test_profit_factor_exact_with_mixed_trades() -> None:
    m = Metrics()
    record(m, "10")
    record(m, "5")
    record(m, "-5")
    assert m.profit_factor() == Decimal("3")


def test_profit_factor_zero_with_only_losses() -> None:
    m = Metrics()
    record(m, "-10")
    record(m, "-5")
    assert m.profit_factor() == Decimal("0")


def test_win_rate_is_none_without_trades() -> None:
    assert Metrics().win_rate() is None


def test_win_rate_exact_with_mixed_trades() -> None:
    m = Metrics()
    record(m, "10")
    record(m, "-5")
    record(m, "-5")
    assert m.win_rate() == Decimal("1") / Decimal("3")


def test_win_rate_one_with_only_wins() -> None:
    m = Metrics()
    record(m, "10")
    record(m, "5")
    assert m.win_rate() == Decimal("1")


def test_sharpe_is_none_with_fewer_than_two_trades() -> None:
    m = Metrics()
    assert m.sharpe() is None
    record(m, "10")
    assert m.sharpe() is None


def test_sharpe_is_none_when_stddev_is_zero() -> None:
    m = Metrics()
    record(m, "5")
    record(m, "5")
    assert m.sharpe() is None


def test_sharpe_exact_with_two_trades() -> None:
    m = Metrics()
    record(m, "1")
    record(m, "3")
    assert m.sharpe() == Decimal("2")


def test_zero_pnl_counts_as_neither_win_nor_loss() -> None:
    m = Metrics()
    record(m, "0")
    assert m.trades == 1
    assert m.wins == 0
    assert m.losses == 0
    assert m.profit_factor() is None


def test_max_drawdown_is_measured_from_equity_peak() -> None:
    m = Metrics()
    record(m, "10")
    record(m, "5")
    record(m, "-20")
    assert m.equity == Decimal("-5")
    assert m.max_drawdown == Decimal("20")


def test_max_drawdown_zero_when_equity_only_rises() -> None:
    m = Metrics()
    record(m, "10")
    record(m, "5")
    assert m.max_drawdown == Decimal("0")


def test_net_pnl_and_equity_track_running_sum() -> None:
    m = Metrics()
    record(m, "10")
    assert m.net_pnl == Decimal("10")
    assert m.equity == Decimal("10")
    record(m, "-4")
    assert m.net_pnl == Decimal("6")
    assert m.equity == Decimal("6")
