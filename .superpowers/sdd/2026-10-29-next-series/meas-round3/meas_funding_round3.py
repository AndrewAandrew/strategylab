#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round 3 step (b): funding/carry pre-registered measurement (brat, R-next-1).

Implements funding-round3-preregistration.md VERBATIM:
  - z_T over 270 trailing settles strictly before T (r_T excluded), theta=2 fixed;
  - position-based non-overlapping trades (only when flat);
  - honest entry = close of hourly bar with bucket ts = T+3600 (close_{T+1h});
    exit = close of bar with bucket ts = T+3600+hold*3600 (close_{T+25h} for hold=24);
    hold between fills = exactly 24h; accrual settles tau in (entry_bucket, exit_bucket];
  - signs: LONG price=(exit/entry-1), accrual=-sum(r); SHORT price=(1-exit/entry), accrual=+sum(r);
  - combined = price+accrual; net = combined - 0.22%;
  - zones: BTC T in [1704067200,1790294400) (f0+f1+f2+f3), ETH T in [1761523200,1790294400);
  - holdout/live (ts >= 1790294400) NEVER read: funding filtered in SQL, bars filtered in SQL.
Read-only sqlite URIs, stdlib only. Output via --out (and stdout).
"""
import bisect
import math
import random
import sqlite3
import statistics
import sys
from argparse import ArgumentParser

DB = r"file:C:/Users/egoro/Documents/GitHub/strategylab/data/{}.db?mode=ro"
HIST_LO, FIT_LO, FIT_HI = 1704067200, 1761523200, 1790294400
FOLDS_BTC = [("f0", HIST_LO, FIT_LO),
             ("f1", FIT_LO, 1771113600),
             ("f2", 1771113600, 1780704000),
             ("f3", 1780704000, FIT_HI)]
FOLDS_ETH = FOLDS_BTC[1:]
SETTLE, WARM, THETA, COST, HOUR = 28800, 270, 2.0, 0.22, 3600

out_lines = []


def p(s=""):
    out_lines.append(str(s))


def load_funding(db):
    con = sqlite3.connect(DB.format(db), uri=True)
    rows = con.execute("SELECT ts, rate FROM funding_8h WHERE ts < ? ORDER BY ts",
                       (FIT_HI,)).fetchall()
    con.close()
    ts = [r[0] for r in rows]
    rates = [float(r[1]) for r in rows]
    assert all(t % SETTLE == 0 for t in ts), "funding grid violated"
    assert all(b - a == SETTLE for a, b in zip(ts, ts[1:])), "funding gap"
    return ts, rates


def load_hourly_closes(db):
    """Full 60-minute hourly buckets -> {bucket_ts: close}. Bars with ts<FIT_HI only."""
    con = sqlite3.connect(DB.format(db), uri=True)
    rows = con.execute("SELECT ts, close FROM bars_1m WHERE ts >= ? AND ts < ? ORDER BY ts",
                       (HIST_LO, FIT_HI)).fetchall()
    con.close()
    best, cnt = {}, {}
    for ts, c in rows:
        k = ts - ts % HOUR
        cnt[k] = cnt.get(k, 0) + 1
        if k not in best or ts > best[k][1]:
            best[k] = (float(c), ts)
    return {k: v[0] for k, v in best.items() if cnt[k] == 60}


def zseries(rates, win):
    z = [None] * len(rates)
    for i in range(win, len(rates)):
        w = rates[i - win:i]
        m = sum(w) / win
        var = sum((x - m) ** 2 for x in w) / (win - 1)
        if var <= 0:
            continue
        z[i] = (rates[i] - m) / math.sqrt(var)
    return z


def pctl(w, p_):
    ws = sorted(w)
    k = (len(ws) - 1) * p_ / 100.0
    f = int(k)
    c = min(f + 1, len(ws) - 1)
    if f == c:
        return ws[f]
    return ws[f] * (c - k) + ws[c] * (k - f)


def run_cell(fts, rates, zmap, closes, direction, theta, hold_h, win,
             zone_lo, zone_hi, mode="z", warm_min_ts=None, entry_off=HOUR,
             momentum=False):
    """Position-based non-overlapping trades. Returns (trades, counters)."""
    trades = []
    cnts = {"degraded": 0, "unpr_entry": 0, "unpr_exit": 0, "z_unavailable": 0}
    closes_sorted = sorted(closes)
    flat_after = -1
    for i, T in enumerate(fts):
        if T < zone_lo:
            continue
        if T >= zone_hi:
            break
        if T <= flat_after:
            continue  # signals during holding ignored
        if i < win or (warm_min_ts is not None and fts[i - win] < warm_min_ts):
            cnts["z_unavailable"] += 1
            continue
        w = rates[i - win:i]
        if mode in ("z", "momentum"):
            z = zmap[win][i]
            if z is None:
                continue
            if mode == "momentum":
                zp = zmap[win][i - 1]
                if zp is None or abs(z) < theta or abs(z) <= abs(zp):
                    continue
            sig = (z <= -theta) if direction == "LONG" else (z >= theta)
        else:  # percentile
            sig = (rates[i] <= pctl(w, 5)) if direction == "LONG" \
                else (rates[i] >= pctl(w, 95))
        if not sig:
            continue
        would_exit_obs = T + (hold_h + 2) * HOUR
        # entry: first available close at bucket >= T + entry_off
        j = bisect.bisect_left(closes_sorted, T + entry_off)
        if j == len(closes_sorted):
            cnts["unpr_entry"] += 1
            flat_after = would_exit_obs
            continue
        eb = closes_sorted[j]
        if eb + HOUR > T + 3 * HOUR:  # degraded: close later than T+3h
            cnts["degraded"] += 1
            flat_after = would_exit_obs
            continue
        # exit: first available close at bucket >= T + entry_off + hold*3600
        k2 = bisect.bisect_left(closes_sorted, T + entry_off + hold_h * HOUR)
        if k2 == len(closes_sorted):
            cnts["unpr_exit"] += 1
            flat_after = would_exit_obs
            continue
        xb = closes_sorted[k2]
        pe, px = closes[eb], closes[xb]
        sign = 1 if direction == "LONG" else -1
        price = (px / pe - 1.0) * 100.0 * sign
        acc = -sign * sum(rates[k] for k, tt in enumerate(fts) if eb < tt <= xb) * 100.0
        comb = price + acc
        trades.append({"T": T, "eb": eb, "xb": xb, "price": price,
                       "acc": acc, "comb": comb, "net": comb - COST})
        flat_after = xb + HOUR  # position closes at exit close observation
    return trades, cnts


def fold_of(folds, T):
    for name, lo, hi in folds:
        if lo <= T < hi:
            return name
    return None


def bootstrap_ci(vals, seed=42, n_res=1000):
    rnd = random.Random(seed)
    n = len(vals)
    means = sorted(sum(vals[rnd.randrange(n)] for _ in range(n)) / n
                   for _ in range(n_res))
    def q(pp):
        k = (n_res - 1) * pp / 100.0
        f = int(k); c = min(f + 1, n_res - 1)
        return means[f] if f == c else means[f] * (c - k) + means[c] * (k - f)
    return q(2.5), q(97.5)


def stats_block(tag, trades, folds, bootstrap=True):
    if not trades:
        p(f"  {tag}: n=0 -- NO TRADES")
        return None
    comb = [t["comb"] for t in trades]
    price = [t["price"] for t in trades]
    acc = [t["acc"] for t in trades]
    net = [t["net"] for t in trades]
    wr_c = sum(1 for x in comb if x > 0) / len(comb) * 100
    wr_p = sum(1 for x in price if x > 0) / len(price) * 100
    p(f"  {tag}: n={len(trades)} | price mean={statistics.mean(price):.4f}% "
      f"med={statistics.median(price):.4f}% | accrual mean={statistics.mean(acc):.4f}% "
      f"| comb mean={statistics.mean(comb):.4f}% med={statistics.median(comb):.4f}% "
      f"| net mean={statistics.mean(net):.4f}% | wr(comb)={wr_c:.2f}% wr(price)={wr_p:.2f}%")
    for fname, lo, hi in folds:
        sub = [t["comb"] for t in trades if lo <= t["T"] < hi]
        subp = [t["price"] for t in trades if lo <= t["T"] < hi]
        if sub:
            p(f"    {fname}: n={len(sub)} comb mean={statistics.mean(sub):.4f}% "
              f"med={statistics.median(sub):.4f}% price mean={statistics.mean(subp):.4f}%")
        else:
            p(f"    {fname}: n=0")
    if bootstrap and len(trades) >= 5:
        lo_ci, hi_ci = bootstrap_ci(comb)
        p(f"    bootstrap 95% CI comb mean (1000, seed=42): [{lo_ci:.4f}%, {hi_ci:.4f}%]")
    return {"n": len(trades), "comb_mean": statistics.mean(comb),
            "price_mean": statistics.mean(price),
            "fold_means": {name: (statistics.mean([t["comb"] for t in trades
                                                   if lo <= t["T"] < hi])
                                  if any(lo <= t["T"] < hi for t in trades) else None)
                           for name, lo, hi in folds},
            "comb": comb}


def survival(tag, s):
    if s is None:
        p(f"  {tag}: VERDICT -- DEAD (rule 1: n<30, no trades)")
        return False
    checks = []
    checks.append(("1 n>=30", s["n"] >= 30))
    checks.append(("2 comb mean>=0.30%", s["comb_mean"] >= 0.30))
    folds_ok = all(v is not None and v > 0 for v in s["fold_means"].values())
    checks.append(("3 all folds>0", folds_ok))
    lo_ci, hi_ci = bootstrap_ci(s["comb"])
    checks.append(("4 boot low>0", lo_ci > 0))
    checks.append(("5 price mean>0", s["price_mean"] > 0))
    alive = all(ok for _, ok in checks)
    p(f"  {tag}: " + "; ".join(f"[{'PASS' if ok else 'FAIL'}] {name}"
                               for name, ok in checks)
      + f" => {'ALIVE' if alive else 'DEAD'}")
    return alive


def main():
    ap = ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    # ---- provenance ----
    fb_ts, fb_r = load_funding("btcusdt")
    fe_ts, fe_r = load_funding("ethusdt")
    closes_b = load_hourly_closes("btcusdt")
    closes_e = load_hourly_closes("ethusdt")
    btc_2024 = sum(1 for k in closes_b if k < FIT_LO)
    btc_fit = sum(1 for k in closes_b if k >= FIT_LO)
    eth_fit = sum(1 for k in closes_e if k >= FIT_LO)
    p("== PROVENANCE ==")
    p(f"funding_8h read (ts < {FIT_HI}): BTC rows={len(fb_ts)} "
      f"[{fb_ts[0]}..{fb_ts[-1]}], ETH rows={len(fe_ts)} [{fe_ts[0]}..{fe_ts[-1]}]")
    p(f"holdout/live (ts >= {FIT_HI}) never queried (SQL WHERE ts < {FIT_HI}).")
    p(f"hourly full buckets (60/60 min): BTC f0-2024={btc_2024}, BTC fit={btc_fit}, "
      f"ETH fit={eth_fit}; BTC gap [1735689600,1761523200) = 0 bars (known).")
    assert len(fb_ts) == 2994 and len(fe_ts) == 2994
    assert btc_2024 == 8784 and btc_fit == 7992 and eth_fit == 7992
    zmap = {WARM: zseries(fb_r, WARM)}
    zmap_e = {WARM: zseries(fe_r, WARM)}
    # sample-vs-population sensitivity (statistic, not a cell)
    def sig_count(ts_, zc, th):
        return sum(1 for z in zc if z is not None and abs(z) >= th)
    p(f"z (win=270, sample std): BTC settles with |z|>=2: {sig_count(fb_ts, zmap[WARM], 2.0)}; "
      f"ETH: {sig_count(fe_ts, zmap_e[WARM], 2.0)}")

    # ---- PRIMARY CELLS ----
    p()
    p("== PRIMARY CELLS (F1-F4, prereg section 5) ==")
    cells = {}
    specs = [
        ("F1 BTC z<=-2 LONG", "btc", fb_ts, fb_r, zmap, "LONG", FOLDS_BTC, HIST_LO, FIT_HI),
        ("F2 BTC z>=+2 SHORT", "btc", fb_ts, fb_r, zmap, "SHORT", FOLDS_BTC, HIST_LO, FIT_HI),
        ("F3 ETH z<=-2 LONG", "eth", fe_ts, fe_r, zmap_e, "LONG", FOLDS_ETH, FIT_LO, FIT_HI),
        ("F4 ETH z>=+2 SHORT", "eth", fe_ts, fe_r, zmap_e, "SHORT", FOLDS_ETH, FIT_LO, FIT_HI),
    ]
    for tag, sym, fts, rates, zm, direction, folds, zlo, zhi in specs:
        closes = closes_b if sym == "btc" else closes_e
        trades, cnts = run_cell(fts, rates, zm, closes, direction, THETA, 24, WARM,
                                zlo, zhi)
        p(f"{tag}: trades={len(trades)} | degraded={cnts['degraded']} "
          f"unpr_entry={cnts['unpr_entry']} unpr_exit={cnts['unpr_exit']} "
          f"z_unavailable={cnts['z_unavailable']}")
        cells[tag] = (stats_block(tag, trades, folds), trades, cnts)
    p()
    p("== SURVIVAL RULES (5 conditions, prereg section 6) ==")
    verdicts = {}
    for tag in cells:
        verdicts[tag] = survival(tag, cells[tag][0])

    # ---- DIAGNOSTICS (prereg section 8, marked NOT for specification) ----
    p()
    p("== DIAGNOSTICS (NOT for specification) ==")
    p("-- D1 hold ladder k in {8,16,32,48}h, theta=2 (F1,F2) --")
    for tag, sym, fts, rates, direction in [
            ("F1", "btc", fb_ts, fb_r, "LONG"), ("F2", "btc", fb_ts, fb_r, "SHORT")]:
        for kh in (8, 16, 32, 48):
            tr, _ = run_cell(fts, rates, zmap, closes_b, direction, THETA, kh, WARM,
                             HIST_LO, FIT_HI)
            if tr:
                c = [t["comb"] for t in tr]
                p(f"  {tag} k={kh}h: n={len(tr)} comb mean={statistics.mean(c):.4f}% "
                  f"price={statistics.mean([t['price'] for t in tr]):.4f}% "
                  f"accr={statistics.mean([t['acc'] for t in tr]):.4f}% "
                  f"net={statistics.mean([t['net'] for t in tr]):.4f}%")
            else:
                p(f"  {tag} k={kh}h: n=0")
    p("-- D2 theta ladder {1.5,2.5,3.0}, k=24h (F1,F2) --")
    for tag, fts, rates, direction in [("F1", fb_ts, fb_r, "LONG"),
                                       ("F2", fb_ts, fb_r, "SHORT")]:
        for th in (1.5, 2.5, 3.0):
            tr, _ = run_cell(fts, rates, zmap, closes_b, direction, th, 24, WARM,
                             HIST_LO, FIT_HI)
            if tr:
                c = [t["comb"] for t in tr]
                p(f"  {tag} theta={th}: n={len(tr)} comb mean={statistics.mean(c):.4f}% "
                  f"med={statistics.median(c):.4f}%")
            else:
                p(f"  {tag} theta={th}: n=0")
    p("-- D3 trailing window {30,60,180}d = {90,180,540} settles, theta=2, k=24h (F1,F2) --")
    for win in (90, 180, 540):
        zmap[win] = zseries(fb_r, win)
    for tag, fts, rates, direction in [("F1", fb_ts, fb_r, "LONG"),
                                       ("F2", fb_ts, fb_r, "SHORT")]:
        for win in (90, 180, 540):
            tr, _ = run_cell(fts, rates, zmap, closes_b, direction, THETA, 24, win,
                             HIST_LO, FIT_HI)
            if tr:
                c = [t["comb"] for t in tr]
                p(f"  {tag} win={win//3}d: n={len(tr)} comb mean={statistics.mean(c):.4f}% "
                  f"med={statistics.median(c):.4f}%")
            else:
                p(f"  {tag} win={win//3}d: n=0")
    p("-- D4 percentile tail (5/95, trailing 90d), k=24h (F1,F2) --")
    for tag, fts, rates, direction in [("F1", fb_ts, fb_r, "LONG"),
                                       ("F2", fb_ts, fb_r, "SHORT")]:
        tr, _ = run_cell(fts, rates, zmap, closes_b, direction, THETA, 24, WARM,
                         HIST_LO, FIT_HI, mode="pct")
        if tr:
            c = [t["comb"] for t in tr]
            p(f"  {tag} pct: n={len(tr)} comb mean={statistics.mean(c):.4f}% "
              f"med={statistics.median(c):.4f}%")
        else:
            p(f"  {tag} pct: n=0")
    p("-- D5 momentum (|z|>=2 and |z| growing vs prev settle), k=24h (F1,F2) --")
    for tag, fts, rates, direction in [("F1", fb_ts, fb_r, "LONG"),
                                       ("F2", fb_ts, fb_r, "SHORT")]:
        tr, _ = run_cell(fts, rates, zmap, closes_b, direction, THETA, 24, WARM,
                         HIST_LO, FIT_HI, mode="momentum")
        if tr:
            c = [t["comb"] for t in tr]
            p(f"  {tag} mom: n={len(tr)} comb mean={statistics.mean(c):.4f}% "
              f"med={statistics.median(c):.4f}%")
        else:
            p(f"  {tag} mom: n=0")
    p("-- D6 volatility regime split (trailing 30d=720h std of hourly returns, "
      "median cut) on F1,F2 primary trades --")
    for tag, sym, trades in [("F1", "btc", cells["F1 BTC z<=-2 LONG"][1]),
                             ("F2", "btc", cells["F2 BTC z>=+2 SHORT"][1])]:
        closes = closes_b if sym == "btc" else closes_e
        ks = sorted(closes)
        rets = [(ks[i], closes[ks[i]] / closes[ks[i - 1]] - 1)
                for i in range(1, len(ks))]
        rks = [r[0] for r in rets]
        vols = {}
        for t in trades:
            j = bisect.bisect_left(rks, t["T"])
            if j < 720:
                vols[t["T"]] = None
            else:
                w = [rets[k][1] for k in range(j - 720, j)]
                vols[t["T"]] = statistics.pstdev(w)
        paired = [(vols[t["T"]], t["comb"]) for t in trades if vols[t["T"]] is not None]
        if len(paired) < 6:
            p(f"  {tag}: vol available for {len(paired)}/{len(trades)} trades -- skip")
            continue
        med = statistics.median([v for v, _ in paired])
        lo_h = [c for v, c in paired if v <= med]
        hi_h = [c for v, c in paired if v > med]
        p(f"  {tag}: med_std={med:.6f} | low-vol n={len(lo_h)} "
          f"comb mean={statistics.mean(lo_h):.4f}% | high-vol n={len(hi_h)} "
          f"comb mean={statistics.mean(hi_h):.4f}%")
    p("-- D7 ETH warm-up truncation sensitivity (window forced inside fit, "
      "contract's stale a-priori) F3,F4 --")
    for tag, direction in [("F3", "LONG"), ("F4", "SHORT")]:
        tr, _ = run_cell(fe_ts, fe_r, zmap_e, closes_e, direction, THETA, 24, WARM,
                         FIT_LO, FIT_HI, warm_min_ts=FIT_LO)
        if tr:
            c = [t["comb"] for t in tr]
            p(f"  {tag} truncated-warmup: n={len(tr)} comb mean={statistics.mean(c):.4f}% "
              f"med={statistics.median(c):.4f}%")
        else:
            p(f"  {tag} truncated-warmup: n=0")
    p("-- D8 entry-index sensitivity: wall-clock reading entry=bucket T "
      "(entry one bar earlier), k=24h (F1,F2) --")
    for tag, fts, rates, direction in [("F1", fb_ts, fb_r, "LONG"),
                                       ("F2", fb_ts, fb_r, "SHORT")]:
        tr, _ = run_cell(fts, rates, zmap, closes_b, direction, THETA, 24, WARM,
                         HIST_LO, FIT_HI, entry_off=0)
        if tr:
            c = [t["comb"] for t in tr]
            p(f"  {tag} entry@bucketT: n={len(tr)} comb mean={statistics.mean(c):.4f}% "
              f"med={statistics.median(c):.4f}%")
        else:
            p(f"  {tag} entry@bucketT: n=0")
    p("-- D-stat top-3 share of combined sum (primary cells) --")
    for tag in cells:
        trades = cells[tag][1]
        if not trades:
            continue
        tot = sum(t["comb"] for t in trades)
        top3 = sum(sorted((t["comb"] for t in trades), reverse=True)[:3])
        p(f"  {tag}: top3 sum={top3:.4f}pp / total={tot:.4f}pp "
          f"= {top3 / tot * 100:.2f}%" if tot != 0 else
          f"  {tag}: top3={top3:.4f}pp total~0")
    p("-- D-stat mean episode length |z|>=2 (settles), win=270, ts<FIT_HI --")
    for sym, fts, zc in [("BTC", fb_ts, zmap[WARM]), ("ETH", fe_ts, zmap_e[WARM])]:
        runs, cur = [], 0
        for z in zc:
            if z is not None and abs(z) >= 2.0:
                cur += 1
            else:
                if cur:
                    runs.append(cur)
                cur = 0
        if cur:
            runs.append(cur)
        p(f"  {sym}: episodes={len(runs)} mean_len={statistics.mean(runs):.3f} "
          f"max_len={max(runs)}")

    p()
    p("== CELL BUDGET ==")
    p("primary=4; diagnostics: D1=8, D2=6, D3=6, D4=2, D5=2, D6=2, D7=2, D8=2 => 30")
    p("TOTAL = 34 cells (cap 40); headroom 6 -- deliberately unused (no grid expansion)")

    text = "\n".join(out_lines)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
