# -*- coding: utf-8 -*-
"""Round 5 charts, phase A: cumulative net curves per cell from journal_p.csv.

View-only visualization (round5-preregistration.md §9.4, к5-7): NOT a measurement
object. Stdlib only. Reads data/calib/r5_*/​*_journal_p.csv (entry_ts, exit_ts, pnl),
aggregates the 3 folds per cell, emits one self-contained HTML page with inline SVG.

Cell naming (round5-grid-commands.md): r5_{j,s}_{p,w,t2}_{btc,eth}_{f1..f3}
Groups: primary = *_p_*; diagnostics = *_w_*, *_t2_*.
"""
import csv
import html
import sys
from pathlib import Path

FOLD_BOUNDS = [(1761523200, 1771113600), (1771113600, 1780704000), (1780704000, 1790294400)]
CELL_LABEL = {"j_p": "J primary (floor 0.025%)", "s_p": "S primary (floor 0.025%)",
              "j_w": "J wide (floor 0.1%)", "s_w": "S wide (floor 0.15%)",
              "j_t2": "J tp=2.0 (BTC)"}
SYMBOL_LABEL = {"btc": "BTCUSDT", "eth": "ETHUSDT"}


def cell_key(dir_name: str):
    parts = dir_name.split("_")  # r5 j p btc f1
    if len(parts) == 5 and parts[0] == "r5":
        return f"{parts[1]}_{parts[2]}", parts[3]
    return None, None


def load_trades(calib_dir: Path):
    """-> {(cell, symbol): [(exit_ts, pnl), ...]} from journal_p.csv of every run."""
    out = {}
    for d in sorted(calib_dir.glob("r5_*")):
        cell, sym = cell_key(d.name)
        if cell is None:
            continue
        for jp in d.glob("*_journal_p.csv"):
            rows = out.setdefault((cell, sym), [])
            with jp.open(encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    try:
                        rows.append((int(r["exit_ts"]), float(r["pnl"])))
                    except (KeyError, ValueError):
                        continue  # malformed tail lines, skip silently (tool is view-only)
    for v in out.values():
        v.sort(key=lambda t: t[0])
    return out


def cum_curve(trades):
    ts = [trades[0][0]] if trades else [0]
    xs, ys, acc = [ts[0]], [0.0], 0.0
    for t, pnl in trades:
        acc += pnl
        xs.append(t)
        ys.append(acc)
    return xs, ys


def svg_panel(trades, title, w=340, h=190):
    ml, mr, mt, mb = 44, 8, 24, 26
    pw, ph = w - ml - mr, h - mt - mb
    xs, ys = cum_curve(trades) if trades else ([0, 1], [0.0, 0.0])
    x0, x1 = min(xs), max(xs) or 1
    y0, y1 = min(ys), max(ys)
    if y0 == y1:
        y0, y1 = y0 - 1.0, y1 + 1.0
    pad = (y1 - y0) * 0.06
    y0, y1 = y0 - pad, y1 + pad

    def sx(t):
        return ml + (t - x0) / (x1 - x0 or 1) * pw

    def sy(v):
        return mt + (1 - (v - y0) / (y1 - y0)) * ph

    el = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
          f'<text x="{ml}" y="14" class="ttl">{html.escape(title)}</text>']
    # fold separators
    for b0, _ in FOLD_BOUNDS[1:]:
        if x0 <= b0 <= x1:
            el.append(f'<line x1="{sx(b0):.1f}" y1="{mt}" x2="{sx(b0):.1f}" y2="{mt+ph}" class="fold"/>')
    # zero line
    if y0 < 0 < y1:
        el.append(f'<line x1="{ml}" y1="{sy(0):.1f}" x2="{ml+pw}" y2="{sy(0):.1f}" class="zero"/>')
    # axes labels (min/max $)
    el.append(f'<text x="4" y="{mt+8}" class="ax">{y1:+,.0f}</text>')
    el.append(f'<text x="4" y="{mt+ph}" class="ax">{y0:+,.0f}</text>')
    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    el.append(f'<polyline points="{pts}" class="curve"/>')
    n = len(trades)
    fin = ys[-1] if ys else 0.0
    el.append(f'<text x="{w-mr}" y="{h-8}" text-anchor="end" class="ax">'
              f'n={n}  net={fin:+,.0f}$</text>')
    el.append("</svg>")
    return "".join(el)


def build_page(calib_dir: Path, out_html: Path, note: str):
    data = load_trades(calib_dir)
    order = ["j_p", "s_p", "j_w", "s_w", "j_t2"]
    groups = []
    for cell in order:
        syms = [s for (c, s) in sorted(data) if c == cell]
        if not syms:
            continue
        panels = []
        for sym in syms:
            trades = data[(cell, sym)]
            title = f"{CELL_LABEL.get(cell, cell)} · {SYMBOL_LABEL.get(sym, sym)}"
            panels.append(svg_panel(trades, title))
        groups.append(f'<h2>{html.escape(CELL_LABEL.get(cell, cell))}</h2>'
                      f'<div class="grid">{"".join(panels)}</div>')
    css = ("body{font-family:Segoe UI,Arial;background:#101418;color:#dde}"
           "h1{font-size:19px}h2{font-size:14px;margin:18px 4px 4px;color:#9cd}"
           ".note{color:#8a94a6;font-size:12px}"
           ".grid{display:flex;flex-wrap:wrap;gap:10px}"
           "svg{background:#181f26;border:1px solid #2a3540;border-radius:6px;width:340px}"
           ".ttl{fill:#cfe3ff;font-size:11px}.ax{fill:#7f8b9b;font-size:9px}"
           ".curve{fill:none;stroke:#5ec27a;stroke-width:1.6}"
           ".fold{stroke:#39434f;stroke-dasharray:3,3}.zero{stroke:#6b7683;stroke-width:.7}")
    doc = (f"<!doctype html><html><head><meta charset='utf-8'>"
           f"<title>Round 5 — кумулятивные net-кривые</title><style>{css}</style></head><body>"
           f"<h1>Раунд 5: кумулятивный net по журналам клеток</h1>"
           f"<p class='note'>{html.escape(note)}</p>{''.join(groups)}"
           f"<p class='note'>Серия = журнал сделок (journal_p), фолд-границы пунктиром. "
           f"View-only (к5-7): объектом выживания не является.</p></body></html>")
    out_html.write_text(doc, encoding="utf-8")
    return out_html


if __name__ == "__main__":
    # usage: python charts_round5.py <calib_dir> <out_html> [note]
    calib = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/calib")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("charts-round5.html")
    note = sys.argv[3] if len(sys.argv) > 3 else "предупреждение: данные не опубликованы замерщиком"
    p = build_page(calib, out, note)
    print("wrote", p)
