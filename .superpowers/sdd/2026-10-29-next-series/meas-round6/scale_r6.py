# -*- coding: utf-8 -*-
"""Round 6 design-helper (view-only, NOT a measurement): rho distributions of
signal candidates for J on {15m, 1h} and S on {5m, 15m}, folds f1-f3 only.

Same reconstruction classes as charts_r5_full.py (fund_floor eps, always-flat),
S operational windows (ttl/max_hold) scaled to preserve real time. Prints one
line per (class, interval, symbol): n, rho p25/50/75/p90 in %, share >= 0.10%
and >= 0.20%. Windows beyond end_ts 1790294400 are never read (R-next-2).
"""
from __future__ import annotations

import math
import statistics
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(r"C:/Users/его/../egoro/Documents/GitHub/strategylab")
sys.path.insert(0, str(REPO))

from strategylab.broker.orders import Order  # noqa: E402
from strategylab.core.bars import Bar, Interval  # noqa: E402
from strategylab.data.schema import connect  # noqa: E402
from strategylab.data.store import CandleStore  # noqa: E402
from strategylab.drivers.replay import ReplayDriver  # noqa: E402
from strategylab.strategy.base import StrategyState  # noqa: E402
from strategylab.strategy.jaguar_ema import JaguarEmaStrategy  # noqa: E402
from strategylab.strategy.smc_choch_fvg import SmcChochFvgStrategy  # noqa: E402

FOLDS = [(1761523200, 1771113600), (1771113600, 1780704000), (1780704000, 1790294400)]
EPS = Decimal("1E-12")
RISK = Decimal("0.005")
EQ = Decimal("10000")


class JProbe(JaguarEmaStrategy):
    def __init__(self) -> None:
        super().__init__(risk_frac=RISK, tp_mult=Decimal("1.5"), fund_floor=EPS)
        self.cand: list[float] = []

    def _entry_orders(self, bar: Bar, state: StrategyState, fast, slow) -> list[Order]:
        orders = super()._entry_orders(bar, state, fast, slow)
        if orders:
            risk = state.equity * RISK / orders[0].qty
            self.cand.append(float(risk / bar.close))
            self._entry_pending = False
            self._stop = None
        return []


class SProbe(SmcChochFvgStrategy):
    def __init__(self, ttl: int, hold: int) -> None:
        super().__init__(risk_frac=RISK, fund_floor=EPS,
                         ttl_bars=Decimal(ttl), max_hold=Decimal(hold))
        self.cand: list[float] = []

    def _entry_order(self, bar: Bar, state: StrategyState):
        order = super()._entry_order(bar, state)
        if order is not None:
            risk = state.equity * RISK / order.qty
            self.cand.append(float(risk / bar.close))
            self._entry_pending = False
            self._signal_bar = None
        return None


def run(cls_factory, db: Path, symbol: str, interval: Interval) -> list[float]:
    conn = connect(db)
    store = CandleStore(conn, symbol)
    vals: list[float] = []
    for a, b in FOLDS:
        s = cls_factory()
        driver = ReplayDriver(store, symbol, interval, a, b)
        for idx, bar in enumerate(driver.bars()):
            s.on_bar(bar, StrategyState(position=Decimal(0), equity=EQ, bar_index=idx))
        vals.extend(s.cand)
    conn.close()
    return vals


def line(name: str, vals: list[float]) -> str:
    if not vals:
        return f"{name}\tn=0"
    vs = sorted(vals)
    q = lambda p: vs[min(int(p * len(vs)), len(vs) - 1)] * 100  # noqa: E731
    ge = lambda t: sum(1 for v in vals if v >= t) / len(vals)  # noqa: E731
    return (f"{name}\tn={len(vs)}\tp25={q(.25):.3f}%\tmed={q(.5):.3f}%\t"
            f"p75={q(.75):.3f}%\tp90={q(.9):.3f}%\t>=0.1%={ge(.001):.0%}\t>=0.2%={ge(.002):.0%}")


def main() -> None:
    jobs = [
        ("J", "15m", JProbe, Interval.FIFTEEN_MIN),
        ("J", "1h", JProbe, Interval.HOUR),
    ]
    # S operational windows preserve real time: ttl 60m, max_hold 240m
    s_windows = {"5m": (12, 48, Interval.FIVE_MIN), "15m": (4, 16, Interval.FIFTEEN_MIN)}
    for sym_key, (symbol, db) in (
        ("btc", ("BTCUSDT", REPO / "data/btcusdt.db")),
        ("eth", ("ETHUSDT", REPO / "data/ethusdt.db")),
    ):
        for cls_name, tf, factory, iv in jobs:
            print(line(f"{cls_name} {tf:4s} {sym_key}", run(factory, db, symbol, iv)), flush=True)
        for tf, (ttl, hold, iv) in s_windows.items():
            print(line(f"S {tf:4s} {sym_key}",
                       run(lambda ttl=ttl, hold=hold: SProbe(ttl, hold), db, symbol, iv)),
                  flush=True)


if __name__ == "__main__":
    main()
