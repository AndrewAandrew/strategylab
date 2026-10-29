"""DCA-лесенка: усреднение вниз по геометрическим уровням без выходов (design §8, A5).

Спека: docs/superpowers/specs/2026-10-28-lab-calibration-suite-design.md, §8.

Логика (заморожена): бар 0 подаёт первый MARKET BUY, якорь — его close; далее
на каждом баре при занятой позиции, `bought < layers + 1` и `close <=
якорь·(1−step)^bought` подаётся докупка фиксированным лотом 1 (не более L
докупов; всего лотов layers+1 — pre-flight T7↔T8). Стопов, тейков, переворотов
нет; позицию закрывает движок концом окна. Гейта `equity <= 0` намеренно нет:
анти-стратегия, предметом замера является поведение модели на отрицательной
equity (гейта уровня счёта в ядре нет — finding №0 из spec_02).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState

_ONE = Decimal("1")
_QTY = Decimal("1")


class DcaGridStrategy:
    """Геометрическая лесенка вниз: якорь close первого сигнала, шаг s, L докупов."""

    name = "dca_grid"

    PARAM_NAMES = ("step", "layers")

    def __init__(self, step: Decimal, layers: Decimal) -> None:
        if not Decimal("0") < step < _ONE:
            raise ValueError(f"step должен быть в диапазоне 0 < step < 1, получено {step}")
        if layers < 1:
            raise ValueError(f"layers должен быть >= 1, получено {layers}")
        self._step = step
        self._layers = int(layers)
        self._anchor: Decimal | None = None
        self._bought = 0

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> DcaGridStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры dca_grid: {unknown}")
        return cls(**{name: params[name] for name in cls.PARAM_NAMES if name in params})

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        if self._anchor is None:
            # Первый сигнал (бар 0): якорь — текущий close, подан первый лот.
            self._anchor = bar.close
            self._bought = 1
            return [Order(Side.BUY, OrderKind.MARKET, _QTY, None, None, False)]
        level = self._anchor * (_ONE - self._step) ** self._bought
        if state.position != 0 and self._bought < self._layers + 1 and bar.close <= level:
            self._bought += 1
            return [Order(Side.BUY, OrderKind.MARKET, _QTY, None, None, False)]
        return []
