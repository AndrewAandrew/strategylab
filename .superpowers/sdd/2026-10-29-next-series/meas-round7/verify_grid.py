# -*- coding: utf-8 -*-
"""Сверка 24 команд round7-grid-commands.md со спекой round7-preregistration.md §1/§4/§5.

Read-only: читает оба md, ничего не пишет в репо. Вывод ASCII-безопасен.
"""
import re
import sys

ROOT = r"C:/Users/egoro/Documents/GitHub/strategylab"
GRID = ROOT + "/.superpowers/sdd/2026-10-29-next-series/round7-grid-commands.md"

FOLDS = {
    "f1": ("1761523200", "1771113600"),
    "f2": ("1771113600", "1780704000"),
    "f3": ("1780704000", "1790294400"),
}
CELLS = {
    # cell: (strategy, interval, params, expected order index block)
    "j_b1_h": ("jaguar_ema", "hour", {"risk_frac": "0.005", "fund_floor": "0.00025",
                                      "tp_mult": "1.5", "be_mult": "1.0"}),
    "j_b25_h": ("jaguar_ema", "hour", {"risk_frac": "0.005", "fund_floor": "0.00025",
                                       "tp_mult": "2.5", "be_mult": "1.0"}),
    "s_tp25_15": ("smc_choch_fvg", "15m", {"risk_frac": "0.005", "fund_floor": "0.00025",
                                           "ttl_bars": "4", "max_hold": "16", "tp_mult": "2.5"}),
    "s_tp60_15": ("smc_choch_fvg", "15m", {"risk_frac": "0.005", "fund_floor": "0.00025",
                                           "ttl_bars": "4", "max_hold": "16", "tp_mult": "6.0"}),
}
ORDER = ["j_b1_h", "j_b25_h", "s_tp25_15", "s_tp60_15"]
DB = {"BTCUSDT": "data/btcusdt.db", "ETHUSDT": "data/ethusdt.db"}

text = open(GRID, encoding="utf-8").read()
blocks = re.findall(r"```\n(.*?)```", text, re.S)
cmds = []
for b in blocks:
    for line in b.splitlines():
        line = line.strip()
        if line.startswith("& $PY"):
            cmds.append(line)

errors = []
if len(cmds) != 24:
    errors.append("command count = %d (want 24)" % len(cmds))

seen_order = []
for i, cmd in enumerate(cmds, 1):
    m = re.match(
        r"& \$PY \$H (\S+) (\S+) (\d+) (\d+) (\S+) --interval (\S+) --leverage (\S+) "
        r"--capital (\S+)((?: --param \S+)+) --out (\S+)$", cmd)
    if not m:
        errors.append("cmd %d: parse failure: %s" % (i, cmd))
        continue
    db, sym, st, en, strat, interval, lev, cap, params_str, out = m.groups()
    params = dict(p.split("=", 1) for p in params_str.split(" --param ")[1:])
    outm = re.match(r"data/calib/r7_(\w+?)_(btc|eth)_(f[123])$", out)
    if not outm:
        errors.append("cmd %d: out prefix not r7_<cell>_<btc|eth>_fN: %s" % (i, out))
        continue
    cell, sym_sfx, fold = outm.groups()
    if cell not in CELLS:
        errors.append("cmd %d: unknown cell %s" % (i, cell))
        continue
    if cell not in seen_order:
        seen_order.append(cell)
    exp_strat, exp_interval, exp_params = CELLS[cell]
    if strat != exp_strat:
        errors.append("cmd %d: strategy %s != %s" % (i, strat, exp_strat))
    if interval != exp_interval:
        errors.append("cmd %d: interval %s != %s (cell %s)" % (i, interval, exp_interval, cell))
    if (st, en) != FOLDS[fold]:
        errors.append("cmd %d: fold %s bounds %s..%s != %s" % (i, fold, st, en, FOLDS[fold]))
    if lev != "20":
        errors.append("cmd %d: leverage %s != 20" % (i, lev))
    if cap != "10000":
        errors.append("cmd %d: capital %s != 10000" % (i, cap))
    if params != exp_params:
        errors.append("cmd %d: params %s != %s (cell %s)" % (i, params, exp_params, cell))
    want_sym = {"btc": "BTCUSDT", "eth": "ETHUSDT"}[sym_sfx]
    if sym != want_sym:
        errors.append("cmd %d: symbol %s != %s" % (i, sym, want_sym))
    if db != DB[sym]:
        errors.append("cmd %d: db %s != %s" % (i, db, DB[sym]))
    # no holdout leakage
    if int(en) > 1790294400:
        errors.append("cmd %d: end_ts %s beyond f3 end" % (i, en))

if seen_order != ORDER:
    errors.append("cell order %s != %s" % (seen_order, ORDER))

# каждый cell x symbol x fold ровно один раз
combos = set()
for cmd in cmds:
    outm = re.search(r"--out data/calib/r7_(\w+?)_(btc|eth)_(f[123])$", cmd)
    if outm:
        combos.add(outm.groups())
want = {(c, s, f) for c in CELLS for s in ("btc", "eth") for f in ("f1", "f2", "f3")}
if combos != want:
    errors.append("combo set mismatch: missing=%s extra=%s"
                  % (sorted(want - combos), sorted(combos - want)))

print("commands found: %d" % len(cmds))
print("cell order: %s" % " -> ".join(seen_order))
print("unique cell/sym/fold combos: %d/24" % len(combos))
if errors:
    print("VERDICT: BLOCK")
    for e in errors:
        print("  ERR: " + e)
    sys.exit(1)
print("VERDICT: MATCH (spec vs grid, word-for-word)")
