from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from strategylab.broker.orders import Fill


@dataclass(slots=True)
class Metrics:
    """Накопитель результата прогона.

    Чистый аккумулятор: книгу позиций не ведёт и pnl из fill не выводит —
    реализованный pnl закрытой сделки передаёт вызывающий (движок).

    `equity` — капитал прогона: стартовый капитал плюс накопленный реализованный
    P&L. Стартовый капитал задаётся конструктором (`Metrics(equity=...)`) — его
    видит стратегия в `StrategyState.equity`, и при нуле стратегия с
    риск-сайзингом не открывает позицию вовсе.
    """

    trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    _peak: Decimal = Decimal("0")
    _pnls: list[Decimal] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        """Пик капитала стартует со стартового equity: просадка меряется от него, а не от нуля."""
        self._peak = self.equity

    def record(self, fill: Fill, pnl: Decimal) -> None:
        self.trades += 1
        if pnl > 0:
            self.wins += 1
        elif pnl < 0:
            self.losses += 1
        self.net_pnl += pnl
        self.equity += pnl
        self._pnls.append(pnl)
        if self.equity > self._peak:
            self._peak = self.equity
        drawdown = self._peak - self.equity
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

    def profit_factor(self) -> Decimal | None:
        """gross_profit / gross_loss; None при отсутствии убыточных сделок."""
        if self.losses == 0:
            return None
        gross_profit = sum((p for p in self._pnls if p > 0), Decimal("0"))
        gross_loss = -sum((p for p in self._pnls if p < 0), Decimal("0"))
        return gross_profit / gross_loss

    def win_rate(self) -> Decimal | None:
        """Доля выигрышных сделок; None при отсутствии сделок."""
        if self.trades == 0:
            return None
        return Decimal(self.wins) / Decimal(self.trades)

    def sharpe(self) -> Decimal | None:
        """mean(pnl) / population stddev(pnl); None при < 2 сделок или нулевом stddev."""
        if self.trades < 2:
            return None
        n = Decimal(self.trades)
        mean = sum(self._pnls, Decimal("0")) / n
        variance = sum(((p - mean) ** 2 for p in self._pnls), Decimal("0")) / n
        stddev = variance.sqrt()
        if stddev == 0:
            return None
        return mean / stddev
