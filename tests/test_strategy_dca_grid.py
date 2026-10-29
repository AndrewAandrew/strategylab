"""DCA-лесенка: геометрическое усреднение вниз без выхода и без self-гейта (design §8, A5).

Юнит-тесты дёргают `on_bar` на синтетических барах: якорь — close первого
сигнала (бар 0), уровни — якорь·(1−step)^k (k — число уже поданных лотов),
всего лотов не больше layers+1 (первый плюс докупки вплоть до L). Выходов,
стопов и тейков нет намеренно — анти-стратегия: замер поведения модели на
отрицательной equity делает движок, не стратегия.
"""

from decimal import Decimal

import pytest

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState
from strategylab.strategy.dca_grid import DcaGridStrategy
from strategylab.strategy.examples import STRATEGY_CLASSES, build

ONE = Decimal("1")


def _bar(ts: int, close: str) -> Bar:
    price = Decimal(close)
    return Bar(ts=ts, open=price, high=price, low=price, close=price, volume=ONE)


def _collect(
    strategy: DcaGridStrategy, closes: list[str], equity: str = "10000"
) -> list[Order]:
    """Прогон лесенки: с бара 0 позиция занята (филл заявки нулевого бара)."""
    orders: list[Order] = []
    for index, close in enumerate(closes):
        position = "0" if index == 0 else "1"
        state = StrategyState(
            position=Decimal(position), equity=Decimal(equity), bar_index=index
        )
        orders += strategy.on_bar(_bar(1000 + index, close), state)
    return orders


def test_first_buy_on_bar_zero() -> None:
    """Бар 0 — ровно один MARKET BUY qty=1, не reduce_only, без expiry."""
    orders = _collect(DcaGridStrategy(Decimal("0.1"), Decimal("3")), ["100", "200"])
    assert len(orders) == 1
    entry = orders[0]
    assert entry.side is Side.BUY
    assert entry.kind is OrderKind.MARKET
    assert entry.qty == ONE
    assert entry.limit_price is None and entry.stop_price is None
    assert entry.reduce_only is False
    assert entry.expires_in_bars is None


def test_layers_at_geometric_levels() -> None:
    """Ровно одна докупка на пересечение уровня (1−s)^k; промежуточные бары молчат."""
    closes = ["100", "95", "90", "85", "81", "78", "72.9", "65"]
    orders = _collect(DcaGridStrategy(Decimal("0.1"), Decimal("4")), closes)
    # k=1 → 90 (b2), k=2 → 81 (b4), k=3 → 72.9 (b6), k=4 → 65.61 (b7): всего 1+4 лота.
    assert len(orders) == 5
    assert all(order.side is Side.BUY for order in orders)


def test_stops_at_layer_cap() -> None:
    """bought == layers+1: глубокое продолжение падения не рождает ни одного ордера."""
    closes = ["100", "90", "81", "50", "20", "10", "1"]
    orders = _collect(DcaGridStrategy(Decimal("0.1"), Decimal("2")), closes)
    assert len(orders) == 3  # первый + 2 докупки (L=2), дальше ноль


def test_no_equity_self_gate() -> None:
    """При equity −1000 под уровнем BUY ВЫДАН: гейта уровня счёта нет намеренно."""
    orders = _collect(
        DcaGridStrategy(Decimal("0.1"), Decimal("3")), ["100", "90", "80"], equity="-1000"
    )
    assert len(orders) == 3  # b0, k=1 → 90 (b1), k=2 → 81 (b2)


def test_no_exit_orders_ever() -> None:
    """Ни на растущем, ни на падающем пути SELL не появляется никогда."""
    rising = _collect(
        DcaGridStrategy(Decimal("0.1"), Decimal("3")), ["100", "110", "120", "130"]
    )
    falling = _collect(
        DcaGridStrategy(Decimal("0.1"), Decimal("3")), ["100", "95", "90", "50", "10"]
    )
    assert len(rising) == 1 and len(falling) == 4  # falling упёрлась в cap 3+1
    assert all(order.side is Side.BUY for order in rising + falling)


def test_params_validation() -> None:
    """Границы параметров + int() для layers + регистрация в реестре и build (R4)."""
    for bad_step in (Decimal("0"), Decimal("-0.1"), Decimal("1"), Decimal("1.5")):
        with pytest.raises(ValueError):
            DcaGridStrategy(bad_step, Decimal("3"))
    for bad_layers in (Decimal("0"), Decimal("-1")):
        with pytest.raises(ValueError):
            DcaGridStrategy(Decimal("0.1"), bad_layers)

    # Дробный layers → int() (стиль SmaCross): 3.7 живёт как 3 — всего 4 лота.
    ladder = _collect(
        DcaGridStrategy(Decimal("0.1"), Decimal("3.7")),
        ["100", "90", "81", "72.9", "65.6", "10"],
    )
    assert len(ladder) == 4  # пятый уровень 65.6 ≤ 65.61 при layers=4 не пройдёт cap

    assert DcaGridStrategy.name == "dca_grid"
    assert STRATEGY_CLASSES["dca_grid"] is DcaGridStrategy
    built = build("dca_grid", {"step": Decimal("0.05"), "layers": Decimal("4")})
    assert isinstance(built, DcaGridStrategy)
    with pytest.raises(ValueError):
        DcaGridStrategy.from_params({"step": Decimal("0.1"), "unknown": ONE})
