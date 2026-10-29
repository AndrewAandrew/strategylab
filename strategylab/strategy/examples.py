from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import Strategy, StrategyState
from strategylab.strategy.dca_grid import DcaGridStrategy
from strategylab.strategy.flat_keltner import FlatKeltnerStrategy
from strategylab.strategy.limit_pullback import LimitPullbackStrategy
from strategylab.strategy.sweep_liquidity import SweepLiquidityStrategy
from strategylab.strategy.trend_pullback import TrendPullbackStrategy


class UnknownStrategy(Exception):
    """Имя стратегии отсутствует в реестре."""


class SmaCrossStrategy:
    """Пересечение двух скользящих средних: BUY на золотом, SELL на кресте смерти."""

    name = "sma_cross"

    def __init__(self, fast: Decimal, slow: Decimal) -> None:
        if slow <= 0:
            raise ValueError(f"slow должен быть > 0, получено {slow}")
        if fast <= 0:
            raise ValueError(f"fast должен быть > 0, получено {fast}")
        if fast >= slow:
            raise ValueError(f"fast должен быть меньше slow: fast={fast}, slow={slow}")
        self._fast_n = int(fast)
        self._slow_n = int(slow)
        self._fast: deque[Decimal] = deque(maxlen=self._slow_n)
        self._slow: deque[Decimal] = deque(maxlen=self._slow_n)
        self._prev_fast: Decimal | None = None
        self._prev_slow: Decimal | None = None

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        self._fast.append(bar.close)
        self._slow.append(bar.close)
        if len(self._slow) < self._slow_n:
            return []
        fast_sma = _sma(self._fast, self._fast_n)
        slow_sma = _sma(self._slow, self._slow_n)
        prev_fast = self._prev_fast
        prev_slow = self._prev_slow
        self._prev_fast = fast_sma
        self._prev_slow = slow_sma
        if prev_fast is None or prev_slow is None:
            return []
        if prev_fast <= prev_slow and fast_sma > slow_sma:
            return [Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False)]
        if prev_fast >= prev_slow and fast_sma < slow_sma:
            return [Order(Side.SELL, OrderKind.MARKET, Decimal("1"), None, None, False)]
        return []


class BuyHoldStrategy:
    """Один MARKET BUY на первом баре. Закрытие позиции — ответственность движка."""

    name = "buy_hold"

    def __init__(self) -> None:
        self._entered = False

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        if self._entered:
            return []
        self._entered = True
        return [Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False)]


# Реестр стратегий по именам: нужен там, где требуется сам класс, а не
# готовый экземпляр. `build` разбирает параметры по-своему (у `sma_cross`
# они обязательны), а инструменты вроде scripts/sweep.py берут из класса
# дефолты конструктора — иначе дефолтный параметр нечем заменить.
STRATEGY_CLASSES: dict[str, type[Strategy]] = {
    "sma_cross": SmaCrossStrategy,
    "buy_hold": BuyHoldStrategy,
    "flat_keltner": FlatKeltnerStrategy,
    "trend_pullback": TrendPullbackStrategy,
    "sweep_liquidity": SweepLiquidityStrategy,
    "limit_pullback": LimitPullbackStrategy,
    "dca_grid": DcaGridStrategy,
}


def build(name: str, params: Mapping[str, Decimal]) -> Strategy:
    """Реестр стратегий: имя и параметры → экземпляр."""
    if name == "sma_cross":
        return SmaCrossStrategy(fast=params["fast"], slow=params["slow"])
    if name == "buy_hold":
        return BuyHoldStrategy()
    if name == "flat_keltner":
        return FlatKeltnerStrategy.from_params(params)
    if name == "trend_pullback":
        return TrendPullbackStrategy.from_params(params)
    if name == "sweep_liquidity":
        return SweepLiquidityStrategy.from_params(params)
    if name == "limit_pullback":
        return LimitPullbackStrategy.from_params(params)
    if name == "dca_grid":
        return DcaGridStrategy.from_params(params)
    raise UnknownStrategy(name)


def _sma(values: deque[Decimal], n: int) -> Decimal:
    window = list(values)[-n:]
    return sum(window, Decimal("0")) / Decimal(len(window))
