# -*- coding: utf-8 -*-
"""Round 5 — charts «понимания» (view-only, к5-7; НЕ объект выживания/замера).

Секции:
  A. кумулятивный net по журналам (journal_p.csv) — все 5 семейств ячеек;
  B. гистограммы rho ВСЕХ кандидатных сигналов (реконструкция реальными классами
     с fund_floor=1E-12, окна только f1–f3, read-ONLY запросы) с линиями floor;
  C. scatter PnL исполненной сделки vs rho (J: кандидат = bar entry_ts−300).

Реконструкция игнорирует перекрытие позиций (кандидаты считываются так, будто
класс всегда флэт) — на графике это подписано. Запуск:
  python charts_r5_full.py <repo_root> <out_html> <note>
"""
from __future__ import annotations

import csv
import html
import math
import sys
from decimal import Decimal
from pathlib import Path

FOLDS = [(1761523200, 1771113600), (1771113600, 1780704000), (1780704000, 1790294400)]
EPS_FLOOR = Decimal("1E-12")
RISK = Decimal("0.005")
EQ = Decimal("10000")

CELL_LABEL = {"j_p": "J primary (floor 0.025%)", "s_p": "S primary (floor 0.025%)",
              "j_w": "J wide (floor 0.1%)", "s_w": "S wide (floor 0.15%)",
              "j_t2": "J tp=2.0 (BTC)"}
SYM_LABEL = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
FLOOR_LINES = {"j": [0.00025, 0.001], "s": [0.00025, 0.0015]}


# ---------------------------------------------------------------- data loading
def load_trades(calib: Path):
    out: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for d in sorted(calib.glob("r5_*")):
        parts = d.name.split("_")
        if len(parts) != 5 or parts[0] != "r5":
            continue
        cell, sym = f"{parts[1]}_{parts[2]}", parts[3]
        for jp in d.glob("*_journal_p.csv"):
            rows = out.setdefault((cell, sym), [])
            with jp.open(encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    try:
                        rows.append((int(r["entry_ts"]), int(r["exit_ts"]), float(r["pnl"])))
                    except (KeyError, ValueError):
                        continue
    for v in out.values():
        v.sort(key=lambda t: t[1])
    return out


def build_probes(repo_root: Path):
    sys.path.insert(0, str(repo_root))
    from strategylab.broker.orders import Order
    from strategylab.core.bars import Bar, Interval  # noqa: F401
    from strategylab.data.schema import connect
    from strategylab.data.store import CandleStore
    from strategylab.drivers.replay import ReplayDriver
    from strategylab.strategy.base import StrategyState
    from strategylab.strategy.jaguar_ema import JaguarEmaStrategy
    from strategylab.strategy.smc_choch_fvg import SmcChochFvgStrategy

    class JProbe(JaguarEmaStrategy):
        """Все сигналы J без skip: rho = R/close(сигнального бара); не входит."""

        def __init__(self) -> None:
            super().__init__(risk_frac=RISK, tp_mult=Decimal("1.5"), fund_floor=EPS_FLOOR)
            self.cand: list[tuple[int, float]] = []

        def _entry_orders(self, bar: Bar, state: StrategyState, fast, slow) -> list[Order]:
            orders = super()._entry_orders(bar, state, fast, slow)
            if orders:
                risk = state.equity * RISK / orders[0].qty
                self.cand.append((bar.ts, float(risk / bar.close)))
                self._entry_pending = False
                self._stop = None
            return []

    class SProbe(SmcChochFvgStrategy):
        """Все пары S без skip: rho = R/close(бара подачи S); не входит."""

        def __init__(self) -> None:
            super().__init__(risk_frac=RISK, fund_floor=EPS_FLOOR)
            self.cand: list[tuple[int, float]] = []

        def _entry_order(self, bar: Bar, state: StrategyState):
            order = super()._entry_order(bar, state)
            if order is not None:
                risk = state.equity * RISK / order.qty
                self.cand.append((bar.ts, float(risk / bar.close)))
                self._entry_pending = False
                self._signal_bar = None
            return None

    def run(cls, db: Path, symbol: str, interval: Interval):
        conn = connect(db)
        store = CandleStore(conn, symbol)
        cand: list[tuple[int, float]] = []
        for a, b in FOLDS:
            s = cls()
            driver = ReplayDriver(store, symbol, interval, a, b)
            for idx, bar in enumerate(driver.bars()):
                s.on_bar(bar, StrategyState(position=Decimal(0), equity=EQ, bar_index=idx))
            cand.extend(s.cand)
        conn.close()
        return cand

    from strategylab.core.bars import Interval as I

    out = {}
    for sym_key, (db, symbol) in {
        "btc": (repo_root / "data/btcusdt.db", "BTCUSDT"),
        "eth": (repo_root / "data/ethusdt.db", "ETHUSDT"),
    }.items():
        out[("j", sym_key)] = run(JProbe, db, symbol, I.FIVE_MIN)
        out[("s", sym_key)] = run(SProbe, db, symbol, I.MINUTE)
    return out


# ------------------------------------------------------------------ svg parts
def _scale_log(vals, lines, pad_l=0.05, pad_r=0.05):
    pts = [v for v in vals if v > 0] + [l for l in lines if l > 0]
    lo, hi = min(pts), max(pts)
    lo = lo / (hi / lo) ** pad_l if hi > lo else lo * 0.5
    hi = hi * (hi / lo) ** pad_r if hi > lo else hi * 2
    return math.log2(lo), math.log2(hi)


def cum_panel(trades, title, w=340, h=190):
    ml, mr, mt, mb = 46, 8, 24, 26
    pw, ph = w - ml - mr, h - mt - mb
    xs, ys, acc = [trades[0][1]], [0.0], 0.0
    for _, te, p in trades:
        acc += p
        xs.append(te)
        ys.append(acc)
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if y0 == y1:
        y0, y1 = y0 - 1.0, y1 + 1.0
    pad = (y1 - y0) * 0.06
    y0, y1 = y0 - pad, y1 + pad
    sx = lambda t: ml + (t - x0) / ((x1 - x0) or 1) * pw  # noqa: E731
    sy = lambda v: mt + (1 - (v - y0) / (y1 - y0)) * ph  # noqa: E731
    el = [f'<svg viewBox="0 0 {w} {h}"><text x="{ml}" y="14" class="ttl">{html.escape(title)}</text>']
    for b0, _ in FOLDS[1:]:
        if x0 <= b0 <= x1:
            el.append(f'<line x1="{sx(b0):.1f}" y1="{mt}" x2="{sx(b0):.1f}" y2="{mt+ph}" class="fold"/>')
    if y0 < 0 < y1:
        el.append(f'<line x1="{ml}" y1="{sy(0):.1f}" x2="{ml+pw}" y2="{sy(0):.1f}" class="zero"/>')
    el.append(f'<text x="4" y="{mt+8}" class="ax">{y1:+,.0f}</text>'
              f'<text x="4" y="{mt+ph}" class="ax">{y0:+,.0f}</text>')
    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    el.append(f'<polyline points="{pts}" class="curve"/>')
    el.append(f'<text x="{w-mr}" y="{h-8}" text-anchor="end" class="ax">'
              f'n={len(trades)} net={ys[-1]:+,.0f}$</text></svg>')
    return "".join(el)


def hist_panel(vals, floors, title, w=340, h=190):
    ml, mr, mt, mb = 34, 8, 24, 30
    pw, ph = w - ml - mr, h - mt - mb
    if not vals:
        return (f'<svg viewBox="0 0 {w} {h}"><text x="{ml}" y="14" class="ttl">'
                f'{html.escape(title)}</text><text x="{ml}" y="{mt+ph//2}" class="ax">'
                f'нет кандидатов</text></svg>')
    lo, hi = _scale_log(vals, floors)
    nb = 32
    bins = [0] * nb
    for v in vals:
        i = int((math.log2(v) - lo) / ((hi - lo) / nb))
        bins[min(max(i, 0), nb - 1)] += 1
    mx = max(bins) or 1
    x_of = lambda l: ml + (math.log2(l) - lo) / (hi - lo) * pw  # noqa: E731
    el = [f'<svg viewBox="0 0 {w} {h}"><text x="{ml}" y="14" class="ttl">{html.escape(title)}</text>']
    for i, c in enumerate(bins):
        if not c:
            continue
        bx = ml + i / nb * pw
        bh = c / mx * ph
        el.append(f'<rect x="{bx:.1f}" y="{mt+ph-bh:.1f}" width="{pw/nb-0.6:.1f}" '
                  f'height="{bh:.1f}" class="bar"/>')
    for f in floors:
        if 2 ** lo <= f <= 2 ** hi:
            fx = x_of(f)
            el.append(f'<line x1="{fx:.1f}" y1="{mt}" x2="{fx:.1f}" y2="{mt+ph}" class="floor"/>')
            below = sum(1 for v in vals if v < f)
            el.append(f'<text x="{fx+3:.1f}" y="{mt+12}" class="flr">{f*100:g}%'
                      f' ⟵{below}/{len(vals)}</text>')
    el.append(f'<text x="{ml}" y="{h-6}" class="ax">{2**lo*100:.3g}%</text>'
              f'<text x="{ml+pw}" y="{h-6}" text-anchor="end" class="ax">{2**hi*100:.3g}%</text>'
              f'<text x="{ml}" y="{mt+ph+12}" class="ax">rho=R/close, всего {len(vals)}</text>'
              f'<text x="{ml+pw}" y="{mt+12}" text-anchor="end" class="ax">max {mx}</text></svg>')
    return "".join(el)


def scatter_panel(pts, floors, title, w=340, h=190):
    ml, mr, mt, mb = 46, 8, 24, 30
    pw, ph = w - ml - mr, h - mt - mb
    if not pts:
        return (f'<svg viewBox="0 0 {w} {h}"><text x="{ml}" y="14" class="ttl">'
                f'{html.escape(title)}</text></svg>')
    rhos = [p[0] for p in pts]
    lo, hi = _scale_log(rhos, floors)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    if y0 == y1:
        y0, y1 = y0 - 1, y1 + 1
    pad = (y1 - y0) * 0.08
    y0, y1 = y0 - pad, y1 + pad
    x_of = lambda r: ml + (math.log2(r) - lo) / (hi - lo) * pw  # noqa: E731
    y_of = lambda v: mt + (1 - (v - y0) / (y1 - y0)) * ph  # noqa: E731
    el = [f'<svg viewBox="0 0 {w} {h}"><text x="{ml}" y="14" class="ttl">{html.escape(title)}</text>',
          f'<line x1="{ml}" y1="{y_of(0):.1f}" x2="{ml+pw}" y2="{y_of(0):.1f}" class="zero"/>']
    for f in floors:
        if 2 ** lo <= f <= 2 ** hi:
            fx = x_of(f)
            el.append(f'<line x1="{fx:.1f}" y1="{mt}" x2="{fx:.1f}" y2="{mt+ph}" class="floor"/>')
    for rho, pnl in pts:
        c = "#5ec27a" if pnl >= 0 else "#d26d6d"
        el.append(f'<circle cx="{x_of(rho):.1f}" cy="{y_of(pnl):.1f}" r="2.4" fill="{c}" opacity="0.7"/>')
    el.append(f'<text x="4" y="{mt+8}" class="ax">{y1:+,.0f}</text>'
              f'<text x="4" y="{mt+ph}" class="ax">{y0:+,.0f}</text>'
              f'<text x="{ml+pw}" y="{h-6}" text-anchor="end" class="ax">{2**hi*100:.3g}%</text>'
              f'<text x="{ml}" y="{h-6}" class="ax">{2**lo*100:.3g}%</text>'
              f'<text x="{ml}" y="{mt+14}" class="ax">pnl$ vs rho, n={len(pts)}</text></svg>')
    return "".join(el)


# ------------------------------------------------------------------------ main
def main() -> None:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("charts-round5-full.html")
    note = sys.argv[3] if len(sys.argv) > 3 else ""
    calib = repo / "data" / "calib"
    trades = load_trades(calib)
    cands = build_probes(repo)

    groups: list[str] = []

    def section(title: str, cell: str, keys: list[str]):
        panels = []
        for sym in keys:
            t = trades.get((cell, sym), [])
            if t:
                panels.append(cum_panel(t, f"{CELL_LABEL[cell]} · {SYM_LABEL[sym]}"))
        if panels:
            groups.append(f'<h2>{html.escape(title)}</h2><div class="grid">{"".join(panels)}</div>')

    section("Первичные · J", "j_p", ["btc", "eth"])
    section("Первичные · S", "s_p", ["btc", "eth"])
    section("Диагностика · J-wide (0.1%)", "j_w", ["btc", "eth"])
    section("Диагностика · S-wide (0.15%)", "s_w", ["btc", "eth"])
    section("Диагностика · J tp=2.0", "j_t2", ["btc"])

    hp = []
    for sym in ("btc", "eth"):
        vals = [r for _, r in cands[("j", sym)]]
        hp.append(hist_panel(vals, FLOOR_LINES["j"], f"J все кандидаты · {SYM_LABEL[sym]}"))
    for sym in ("btc", "eth"):
        vals = [r for _, r in cands[("s", sym)]]
        hp.append(hist_panel(vals, FLOOR_LINES["s"], f"S все пары · {SYM_LABEL[sym]}"))
    groups.append('<h2>Что делает floor:rho всех кандидатов (реконструкция, позиции игнорируются)</h2>'
                  f'<div class="grid">{"".join(hp)}</div>')

    sp = []
    for sym in ("btc", "eth"):
        by_ts = {ts: rho for ts, rho in cands[("j", sym)]}
        pts = []
        for entry_ts, _, pnl in trades.get(("j_p", sym), []):
            rho = by_ts.get(entry_ts - 300)
            if rho is not None:
                pts.append((rho, pnl))
        sp.append(scatter_panel(pts, FLOOR_LINES["j"], f"J сделки: PnL vs rho · {SYM_LABEL[sym]}"))
    groups.append("<h2>J: судьба сделок по стоп-дистанции (первичные журналы)</h2>"
                  f'<div class="grid">{"".join(sp)}</div>')

    css = ("body{font-family:Segoe UI,Arial;background:#101418;color:#dde}"
           "h1{font-size:19px}h2{font-size:14px;margin:18px 4px 4px;color:#9cd}"
           ".note{color:#8a9a6a;font-size:12px;max-width:900px}"
           ".grid{display:flex;flex-wrap:wrap;gap:10px}"
           "svg{background:#181f26;border:1px solid #2a3540;border-radius:6px;width:340px}"
           ".ttl{fill:#cfe3ff;font-size:11px}.ax{fill:#7f8b9b;font-size:9px}"
           ".curve{fill:none;stroke:#5ec27a;stroke-width:1.6}"
           ".fold{stroke:#39434f;stroke-dasharray:3,3}.zero{stroke:#6b7683;stroke-width:.7}"
           ".bar{fill:#4a7fae}.floor{stroke:#e0a03c;stroke-width:1.4;stroke-dasharray:4,2}"
           ".flr{fill:#e0a03c;font-size:9px}")
    doc = (f"<!doctype html><html><head><meta charset='utf-8'>"
           f"<title>Раунд 5 — графики понимания</title><style>{css}</style></head><body>"
           f"<h1>Раунд 5 (fundability-skip): графики понимания</h1>"
           f"<p class='note'>{html.escape(note)}</p>"
           + "".join(groups) +
           "<p class='note'>Вью-онли (к5-7): объектом выживания не является. Кумулятивные "
           "кривые — journal_p прогонов брата; кандидаты — реконструкция теми же классами с "
           "eps-floor, окна f1–f3, без перекрытия позиций; J-спаривание сделок: сигнал = "
           "entry_ts−5м. m-2: у S первичный floor 0.025% структурно недостижим — смотрим "
           "S-wide.</p></body></html>")
    out.write_text(doc, encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
