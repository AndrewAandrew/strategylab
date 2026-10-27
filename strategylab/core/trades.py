"""История закрытых сделок прогона: журнал, который движок ведёт по запросу.

Журнал не влияет ни на метрики, ни на поведение стратегии: он записывает то,
что движок и так знает, — время и цену входа, время и цену выхода,
реализованный pnl и причину выхода. Запись выключена по умолчанию: журнал
появляется в движке только тогда, когда его передали.

Правила подсчёта — те же, что и в метриках движка: закрытая часть позиции —
минимум из количества филла и открытой позиции, pnl — по средней цене входа
за вычетом комиссии закрывающего филла. Поэтому сумма pnl журнала совпадает
с net_pnl отчёта.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from strategylab.broker.orders import Fill


class TradeLogError(Exception):
    """Журнал сделок нарушен: закрытие без открытия."""


@dataclass(frozen=True, slots=True)
class Trade:
    """Закрытая сделка: вход, выход и реализованный pnl.

    `exit_reason` — причина выхода из филла брокера: `take_profit`, `stop`
    или `market` (сведение позиции в конце окна).
    """

    entry_ts: int
    entry_price: Decimal
    exit_ts: int
    exit_price: Decimal
    pnl: Decimal
    exit_reason: str


class TradeLog:
    """Журнал закрытых сделок: время входа ведёт он, закрытия — движок.

    Движок знает цену входа (средняя цена позиции) и pnl закрытия, но не знает
    времени входа: вход мог случиться на любом баре до текущего. Время входа
    поэтому ведёт журнал — он видит каждый филл, открывающий позицию.
    """

    def __init__(self) -> None:
        self._trades: list[Trade] = []
        self._entry_ts: int | None = None

    @property
    def trades(self) -> tuple[Trade, ...]:
        """Закрытые сделки в порядке закрытия: кортеж, а не список."""
        return tuple(self._trades)

    def open(self, ts: int) -> None:
        """Позиция открылась в момент `ts` — это время входа текущей сделки."""
        self._entry_ts = ts

    def close(
        self,
        fill: Fill,
        entry_price: Decimal,
        pnl: Decimal,
        position_qty: Decimal,
    ) -> None:
        """Позиция закрылась филлом — сделка записана в журнал.

        `position_qty` — размер позиции после закрытия: пока позиция открыта,
        время входа остаётся прежним, и следующая её закрытая часть станет
        новой сделкой с тем же входом.
        """
        entry_ts = self._entry_ts
        if entry_ts is None:
            raise TradeLogError("сделка закрыта без открытия: время входа неизвестно")
        self._trades.append(
            Trade(
                entry_ts=entry_ts,
                entry_price=entry_price,
                exit_ts=fill.ts,
                exit_price=fill.price,
                pnl=pnl,
                exit_reason=fill.reason,
            )
        )
        if position_qty <= 0:
            self._entry_ts = None
