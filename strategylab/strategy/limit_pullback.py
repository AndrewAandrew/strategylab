"""Лимит-пуллбек: волны maker-лимиток с TIF-сроком (design §7, спека spec_04).

Спека: docs/superpowers/specs/2026-10-28-lab-calibration-suite-design.md, §7.

Логика (заморожена): на баре, где флэт и `bar_index ≡ 0 (mod ttl)`, выставляется
одна LIMIT BUY по `close·(1−depth)` с `expires_in_bars = ttl`; волны несовместны
— при открытой позиции новых BUY нет. Исполненная позиция живёт `hold` баров без
стопов и тейков и закрывается одним MARKET SELL reduce_only (taker+slip).

Граница волны и инвариант владельца (A3): старая лимитка исполнима на самом баре
границы (новая подаётся после обработки бара движком), на входе следующего
`apply_bar` старая молча списывается ядром (фича feat/limit-order-expiry). В
очереди брокера транзентно живут две лимитки, но исполнима на каждый бар ровно
одна — двойного entry-филла не существует.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState

_ONE = Decimal("1")
_QTY = Decimal("1")


class LimitPullbackStrategy:
    """Волны пуллбек-лимиток: покупаем откат на depth, продаём через hold баров."""

    name = "limit_pullback"

    PARAM_NAMES = ("depth", "ttl_bars", "hold_bars")

    def __init__(self, depth: Decimal, ttl_bars: Decimal, hold_bars: Decimal) -> None:
        if not Decimal("0") < depth < _ONE:
            raise ValueError(f"depth должен быть в диапазоне 0 < depth < 1, получено {depth}")
        if ttl_bars < 1:
            raise ValueError(f"ttl_bars должен быть >= 1, получено {ttl_bars}")
        if hold_bars < 1:
            raise ValueError(f"hold_bars должен быть >= 1, получено {hold_bars}")
        self._depth = depth
        self._ttl = int(ttl_bars)
        self._hold = int(hold_bars)
        self._entry_bar: int | None = None
        self._exit_submitted = False

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> LimitPullbackStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры limit_pullback: {unknown}")
        return cls(**{name: params[name] for name in cls.PARAM_NAMES if name in params})

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        if state.position == 0:
            # Флэт: флаг выхода и отсчёт входа сброшены, волна по модулю ttl.
            self._entry_bar = None
            self._exit_submitted = False
            if state.bar_index % self._ttl == 0:
                limit_price = bar.close * (_ONE - self._depth)
                return [
                    Order(Side.BUY, OrderKind.LIMIT, _QTY, limit_price, None, False, self._ttl)
                ]
            return []
        if self._entry_bar is None:
            # Бар перехода позиции из нуля: стартовая точка отсчёта hold.
            self._entry_bar = state.bar_index
        if not self._exit_submitted and state.bar_index - self._entry_bar >= self._hold:
            self._exit_submitted = True
            return [Order(Side.SELL, OrderKind.MARKET, _QTY, None, None, True)]
        return []
