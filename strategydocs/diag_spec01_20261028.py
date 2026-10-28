# mypy: ignore-errors
"""B: exploratory diagnostics of spec_01 two_sided_momentum failure (fit-zone only).

Re-runs the DEFAULT combination on the exact sweep conditions (BTC/ETH fit window,
capital 10000, PESSIMISTIC, cost defaults) with a trade journal, then decomposes
every closed trade into: cost-free price edge, slippage, exit fee, entry-fee share.
Asserts net P&L matches the published sweep CSV digit-for-digit.

NOT confirmatory evidence of a surviving edge: this is post-hoc exploration of an
already falsified spec, to understand WHY it died (input for the next spec).
"""
from __future__ import annotations

import statistics
import sys
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 28
ROOT = Path(r"C:\Users\egoro\Documents\GitHub\strategylab")
sys.path.insert(0, str(ROOT))

from strategylab.broker.costs import CostModel, IntrabarOrder  # noqa: E402
from strategylab.broker.orders import Fill, Side  # noqa: E402
from strategylab.broker.sim import BrokerSimulator  # noqa: E402
from strategylab.core.bars import Interval  # noqa: E402
from strategylab.core.engine import Engine  # noqa: E402
from strategylab.core.metrics import Metrics  # noqa: E402
from strategylab.core.trades import TradeLog  # noqa: E402
from strategylab.data.schema import connect  # noqa: E402
from strategylab.data.store import CandleStore  # noqa: E402
from strategylab.drivers.replay import ReplayDriver  # noqa: E402
from strategylab.spec.spec import StrategySpec  # noqa: E402
from strategylab.strategy.examples import build  # noqa: E402

START, END = 1761523200, 1790294400
CAPITAL = Decimal("10000")
PARAMS = {"donch_len": Decimal("48"), "atr_mult": Decimal("1.5"), "bias_len": Decimal("48")}
EXPECTED = {
    "BTCUSDT": (905, Decimal("-9921.984938580256861831677647"), "e8d0ec83b0cb"),
    "ETHUSDT": (802, Decimal("-9649.268006566539404816100970"), "a5fdc0bfae15"),
}
MAX_RESIDUAL = [Decimal(0)]


class RichLog(TradeLog):
    """Journal + captured closing fills (qty, fee, side) for cost decomposition."""

    def __init__(self) -> None:
        super().__init__()
        self.closes: list[Fill] = []

    def close(self, fill, entry_price, pnl, position_qty):  # noqa: ANN001, ANN201
        super().close(fill, entry_price, pnl, position_qty)
        self.closes.append(fill)


def run(symbol: str, db: str) -> None:
    costs = CostModel()
    s = costs.slippage
    spec = StrategySpec(
        name="two_sided_momentum", symbol=symbol, interval=Interval.MINUTE,
        start_ts=START, end_ts=END, params=dict(PARAMS), rules_ref="sweep",
    )
    exp_trades, exp_net, exp_ver = EXPECTED[symbol]
    assert spec.version.startswith(exp_ver), f"{symbol}: spec_version {spec.version} != {exp_ver}"

    conn = connect(ROOT / "data" / db)
    try:
        store = CandleStore(conn, symbol)
        feed = ReplayDriver(store, symbol, Interval.MINUTE, START, END)
        broker = BrokerSimulator(costs, IntrabarOrder.PESSIMISTIC)
        log = RichLog()
        metrics = Engine().run(feed, build("two_sided_momentum", dict(PARAMS)), broker,
                               Metrics(equity=CAPITAL), log)
    finally:
        conn.close()

    assert metrics.trades == exp_trades, f"{symbol}: trades {metrics.trades} != {exp_trades}"
    assert metrics.net_pnl == exp_net, f"{symbol}: net {metrics.net_pnl} != {exp_net}"
    print(f"### {symbol}: replay EXACT vs published CSV — trades={metrics.trades}, "
          f"net={metrics.net_pnl}, spec={spec.version[:12]}")

    trades = log.trades
    assert len(trades) == len(log.closes) == exp_trades
    rows = []
    for t, fill in zip(trades, log.closes, strict=True):
        long_pos = fill.side is Side.SELL
        sign = Decimal(1) if long_pos else Decimal(-1)
        # unslip entry/exit toward the base (bar) prices — slipped() is multiplicative
        if long_pos:
            base_e = t.entry_price / (1 + s)
            base_x = t.exit_price / (1 - s)
            slip = fill.qty * (t.entry_price - base_e + base_x - t.exit_price)
        else:
            base_e = t.entry_price / (1 - s)
            base_x = t.exit_price / (1 + s)
            slip = fill.qty * (base_e - t.entry_price + t.exit_price - base_x)
        gross_journal = (t.exit_price - t.entry_price) * fill.qty * sign  # slip inside
        entry_fee = gross_journal - fill.fee - t.pnl                    # exact by identity
        cost_free = (base_x - base_e) * fill.qty * sign                  # pure price edge
        costs_total = slip + fill.fee + entry_fee
        residual = t.pnl - (cost_free - costs_total)
        if abs(residual) > MAX_RESIDUAL[0]:
            MAX_RESIDUAL[0] = abs(residual)
        assert abs(residual) < Decimal("1e-18"), f"identity broken: {t}"
        rows.append({
            "long": long_pos, "reason": t.exit_reason, "qty": fill.qty,
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "hold_h": (t.exit_ts - t.entry_ts) / 3600,
            "entry": t.entry_price, "exit": t.exit_price,
            "cost_free": cost_free, "slip": slip, "fee_exit": fill.fee, "fee_entry": entry_fee,
            "costs": costs_total, "pnl": t.pnl,
            "notional": fill.qty * (base_e + base_x) / 2,
        })

    tot = lambda k: sum(r[k] for r in rows)  # noqa: E731
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] <= 0]
    cf_wins = [r for r in rows if r["cost_free"] > 0]
    pf = sum(r["pnl"] for r in wins) / -sum(r["pnl"] for r in losses) if losses else Decimal("inf")
    pf_cf = (sum(r["cost_free"] for r in cf_wins)
             / -sum(r["cost_free"] for r in rows if r["cost_free"] <= 0))
    flip_count = sum(1 for r in rows if r["cost_free"] > 0 >= r["pnl"])
    flip_sum = sum(r["pnl"] - r["cost_free"] for r in rows if r["cost_free"] > 0 >= r["pnl"])
    print(f"components: cost_free_total={tot('cost_free'):.2f}, slip={tot('slip'):.2f}, "
          f"fees={tot('fee_exit') + tot('fee_entry'):.2f} "
          f"(exit {tot('fee_exit'):.2f} + entry {tot('fee_entry'):.2f}), "
          f"all_costs={tot('costs'):.2f} = net {tot('pnl'):.2f}")
    print(f"PF with costs={pf:.4f} vs PF cost-free={pf_cf:.4f}; "
          f"costs/net magnitude ratio={abs(tot('costs') / tot('pnl')):.3f}")
    print(f"flipped-by-costs trades: {flip_count} ({flip_count / len(rows) * 100:.1f}%), "
          f"they lost {flip_sum:.2f} beyond their cost-free edge")
    for side, label in ((True, "LONG "), (False, "SHORT")):
        sr = [r for r in rows if r["long"] is side]
        sw = [r for r in sr if r["pnl"] > 0]
        sl = [r for r in sr if r["pnl"] <= 0]
        spf = sum(r["pnl"] for r in sw) / -sum(r["pnl"] for r in sl) if sl else None
        print(f"{label}: n={len(sr)}, net={sum(r['pnl'] for r in sr):.2f}, "
              f"cost_free={sum(r['cost_free'] for r in sr):.2f}, "
              f"costs={sum(r['costs'] for r in sr):.2f}, PF={spf:.4f}")
    reasons: dict[str, list] = {}
    for r in rows:
        reasons.setdefault(r["reason"], []).append(r)
    for reason, rr in sorted(reasons.items()):
        rw = [r for r in rr if r["pnl"] > 0]
        rl = [r for r in rr if r["pnl"] <= 0]
        rpf = (sum(r["pnl"] for r in rw) / -sum(r["pnl"] for r in rl)) if rl else None
        print(f"reason={reason:>6}: n={len(rr)}, net={sum(r['pnl'] for r in rr):.2f}, "
              f"cost_free={sum(r['cost_free'] for r in rr):.2f}, "
              f"win%={len(rw) / len(rr) * 100:.1f}, PF={rpf:.4f}, "
              f"avg_hold_h={statistics.mean(r['hold_h'] for r in rr):.1f}")
    holds = sorted(r["hold_h"] for r in rows)
    print(f"hold hours: median={statistics.median(holds):.1f}, "
          f"p25={holds[len(holds) // 4]:.1f}, p75={holds[3 * len(holds) // 4]:.1f}")
    avg_cost = tot("costs") / len(rows)
    avg_notional = tot("notional") / len(rows)
    print(f"avg trade: notional={avg_notional:.2f}, costs={avg_cost:.4f} "
          f"({abs(avg_cost / avg_notional) * 100:.3f}% of notional per round trip); "
          f"avg cost_free |edge|={sum(abs(r['cost_free']) for r in rows) / len(rows):.4f}")
    print(f"entry_price sanity: zeros in journal = {sum(1 for r in rows if r['entry'] == 0)}; "
          f"max decomposition residual = {MAX_RESIDUAL[0]:.3e}")
    print()


run("BTCUSDT", "btcusdt.db")
run("ETHUSDT", "ethusdt.db")
