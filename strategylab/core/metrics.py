from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from strategylab.broker.orders import Fill, Side


def profit_factor_of(pnls: Sequence[Decimal], losses: int) -> Decimal | None:
    """gross_profit / gross_loss; None при отсутствии убыточных сделок."""
    if losses == 0:
        return None
    gross_profit = sum((p for p in pnls if p > 0), Decimal("0"))
    gross_loss = -sum((p for p in pnls if p < 0), Decimal("0"))
    return gross_profit / gross_loss


def win_rate_of(wins: int, trades: int) -> Decimal | None:
    """Доля выигрышных сделок; None при отсутствии сделок."""
    if trades == 0:
        return None
    return Decimal(wins) / Decimal(trades)


@dataclass(slots=True)
class SideMetrics:
    """Накопитель закрытых сделок одной стороны позиции.

    Своих формул отношений не держит: profit_factor и win_rate считает теми же
    функциями модуля, что и Metrics, — третья копия разъехалась бы при первой правке.
    """

    trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: Decimal = Decimal("0")
    _pnls: list[Decimal] = field(default_factory=list, repr=False)

    def record(self, pnl: Decimal) -> None:
        self.trades += 1
        if pnl > 0:
            self.wins += 1
        elif pnl < 0:
            self.losses += 1
        self.net_pnl += pnl
        self._pnls.append(pnl)

    def profit_factor(self) -> Decimal | None:
        return profit_factor_of(self._pnls, self.losses)

    def win_rate(self) -> Decimal | None:
        return win_rate_of(self.wins, self.trades)


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
    long: SideMetrics = field(default_factory=SideMetrics)
    short: SideMetrics = field(default_factory=SideMetrics)
    _peak: Decimal = Decimal("0")
    _pnls: list[Decimal] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        """Пик капитала стартует со стартового equity: просадка меряется от него, а не от нуля."""
        self._peak = self.equity

    def record(self, fill: Fill, pnl: Decimal, side: Side | None = None) -> None:
        """Учесть закрытую сделку.

        `side` — сторона закрываемой позиции (не филла: у филла она противоположная).
        Если задана, сделка дополнительно попадает в соответствующий `SideMetrics`.
        """
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
        if side is not None:
            bucket = self.long if side is Side.BUY else self.short
            bucket.record(pnl)

    def profit_factor(self) -> Decimal | None:
        """gross_profit / gross_loss; None при отсутствии убыточных сделок."""
        return profit_factor_of(self._pnls, self.losses)

    def win_rate(self) -> Decimal | None:
        """Доля выигрышных сделок; None при отсутствии сделок."""
        return win_rate_of(self.wins, self.trades)

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
