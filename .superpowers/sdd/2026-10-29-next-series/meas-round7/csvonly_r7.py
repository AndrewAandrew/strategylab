# -*- coding: utf-8 -*-
"""Round 7 aggregation — CSV-ONLY (brother measurer letter, R-next-1). stdlib, NO DB.

Numbers come exclusively from data/calib/r7_*/ artifacts (journal_p.csv +
report_p.txt). The DB is never opened (contract: holdout R-next-2, bars untouched).

Methodology identical to round 6 letter (2026-11-01-round6-setka-brata.md §1):
  engine._record_realized: pnl_row = (px-pe)*c*s - f_x*px*c - f_e*pe*c
  => comb = pnl/(c*pe) = ((px-pe)*s - f_x*px - f_e*pe)/pe  — qty-free identity.

Direction s from artifacts only (same resolve_dir as r5/r6).
Profile A (primary): entry as taker market (pe_A = pe*(1+-slip), taker fee).
Profile B (secondary, maker): entry limit fill at mid, maker 0.0002 — journal
pnl is exactly profile B for S. J: A == B.

Rules 1-5 = round4 prereg S8 verbatim; rule 6 (dollar, amendment 1) — first in
battle: EVERY fold of a primary cell must end with report net_pnl >= 0.

Bootstrap: 1000 resamples, seed 42, percentile, iid per trade row (r3/4/5/6).
"""
from __future__ import annotations

import random
import statistics
from decimal import Decimal, getcontext

getcontext().prec = 28
D = Decimal

TAKER = D("0.0006")
MAKER = D("0.0002")
SLIP = D("0.0005")

REPO = "C:/Users/egoro/Documents/GitHub/strategylab"
VARIANTS = [
    # (cell prefix, strategy, kind, param label)
    ("j_b1_h", "jaguar_ema", "j", "tp1.5 be1.0 1h"),
    ("j_b25_h", "jaguar_ema", "j", "tp2.5 be1.0 1h"),
    ("s_tp25_15", "smc_choch_fvg", "s", "tp2.5 15m"),
    ("s_tp60_15", "smc_choch_fvg", "s", "tp6.0 15m"),
]
CELLS: list[tuple] = []
for pref, strategy, kind, _lbl in VARIANTS:
    for fk in ("f1", "f2", "f3"):
        for sk in ("btc", "eth"):
            CELLS.append((f"{pref}_{sk}_{fk}", strategy, sk, fk, kind))

SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
FOLDS = {"f1": (1761523200, 1771113600),
         "f2": (1771113600, 1780704000),
         "f3": (1780704000, 1790294400)}

out_lines: list[str] = []
CHECKS: list[str] = []


def p(s: str = "") -> None:
    out_lines.append(str(s))


def check(ok: bool, label: str) -> None:
    CHECKS.append(("OK  " if ok else "FAIL") + " " + label)


def parse_journal(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        header = next(fh).strip().split(",")
        assert header == ["spec_version", "combination", "entry_ts", "entry_price",
                          "exit_ts", "exit_price", "pnl", "exit_reason"], header
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split(",")
            assert len(parts) == 8, parts
            rows.append({"entry_ts": int(parts[2]), "pe": D(parts[3]),
                         "exit_ts": int(parts[4]), "px": D(parts[5]),
                         "pnl": D(parts[6]), "reason": parts[7]})
    return rows


def parse_report(path: str) -> dict:
    metrics: dict[str, str] = {}
    started = False
    wc = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("# cell:"):
                if started:
                    break
                started = True
                continue
            if line.startswith("# window_cancelled:"):
                wc = int(line.split(":", 1)[1])
                continue
            if not started or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                metrics[k.strip()] = v.strip()
    m = {k: (int(v) if k in ("trades", "wins", "losses", "trials") else v)
         for k, v in metrics.items()}
    m["window_cancelled"] = wc
    return m


def group_positions(rows: list[dict]) -> list[dict]:
    positions: list[dict] = []
    for r in rows:
        if positions and positions[-1]["entry_ts"] == r["entry_ts"] \
                and positions[-1]["pe"] == r["pe"]:
            positions[-1]["rows"].append(r)
        else:
            positions.append({"entry_ts": r["entry_ts"], "pe": r["pe"], "rows": [r]})
    return positions


def _den(pe: D, px: D, s: int, f_x: D, f_e: D) -> D:
    return (px - pe) * s - f_x * px - f_e * pe


def resolve_dir(pos: dict, kind: str) -> tuple[int, str]:
    """Direction from artifacts. Returns (s, how). s=0 => ambiguous residual."""
    pe = pos["pe"]
    f_e = TAKER if kind == "j" else MAKER
    if len(pos["rows"]) > 1:
        ok = []
        for s in (1, -1):
            cs: list[D] = []
            good = True
            for r in pos["rows"]:
                f_x = MAKER if r["reason"] == "take_profit" else TAKER
                den = _den(pe, r["px"], s, f_x, f_e)
                if den == 0:
                    good = False
                    break
                c = r["pnl"] / den
                if c <= 0:
                    good = False
                    break
                cs.append(c)
            if good and all(abs(x - cs[0]) <= abs(cs[0]) * D("1e-15") for x in cs):
                ok.append(s)
        if len(ok) == 1:
            return ok[0], "qty"
    r = pos["rows"][0]
    if len(pos["rows"]) == 1 and r["reason"] == "take_profit" and kind == "s":
        return (1 if r["px"] > pe else -1), "tp"
    if len(pos["rows"]) == 1 and r["reason"] == "stop":
        s = 1 if r["px"] < pe else -1
        f_x = TAKER
        den = _den(pe, r["px"], s, f_x, f_e)
        if den != 0 and r["pnl"] / den > 0:
            return s, "stop"
    f_x = TAKER
    px, pnl = r["px"], r["pnl"]
    den_l = _den(pe, px, 1, f_x, f_e)
    den_s = _den(pe, px, -1, f_x, f_e)
    if pnl > 0 and den_l > 0 >= den_s:
        return 1, "pnl"
    if pnl > 0 and den_s > 0 >= den_l:
        return -1, "pnl"
    if pnl < 0 and den_l < 0 <= den_s:
        return 1, "pnl"
    if pnl < 0 and den_s < 0 <= den_l:
        return -1, "pnl"
    return 0, "AMBIG"


def comb_rows(pos: dict, s: int, kind: str) -> list[dict]:
    pe = pos["pe"]
    f_e_b = TAKER if kind == "j" else MAKER
    res = []
    for r in pos["rows"]:
        px = r["px"]
        f_x = MAKER if r["reason"] == "take_profit" else TAKER
        comb_b = ((px - pe) * s - f_x * px - f_e_b * pe) / pe * 100
        if kind == "s":
            pe_a = pe * (1 + SLIP) if s > 0 else pe * (1 - SLIP)
            comb_a = ((px - pe_a) * s - f_x * px - TAKER * pe_a) / pe_a * 100
        else:
            comb_a = comb_b
        res.append({**r, "s": s, "comb_a": comb_a, "comb_b": comb_b})
    return res


def comb_candidates(r: dict, kind: str) -> tuple[D, D, D, D]:
    pe, px = r["pe"], r["px"]
    f_e_b = TAKER if kind == "j" else MAKER
    f_x = MAKER if r["reason"] == "take_profit" else TAKER
    a_vals, b_vals = [], []
    for s in (1, -1):
        pe_a = pe * (1 + SLIP) if s > 0 else pe * (1 - SLIP)
        if kind == "s":
            c_a = ((px - pe_a) * s - f_x * px - TAKER * pe_a) / pe_a * 100
        else:
            c_a = ((px - pe) * s - f_x * px - f_e_b * pe) / pe * 100
        c_b = ((px - pe) * s - f_x * px - f_e_b * pe) / pe * 100
        a_vals.append(c_a)
        b_vals.append(c_b)
    return (min(a_vals), max(a_vals), min(b_vals), max(b_vals))


def bootstrap_ci(vals: list[D], seed: int = 42, n_res: int = 1000):
    rnd = random.Random(seed)
    n = len(vals)
    means = sorted(sum(vals[rnd.randrange(n)] for _ in range(n)) / n
                   for _ in range(n_res))

    def q(pp: float) -> D:
        k = (n_res - 1) * pp / 100.0
        f = int(k)
        c = min(f + 1, n_res - 1)
        return means[f] if f == c else means[f] * D(c - k) + means[c] * D(k - f)
    return q(2.5), q(97.5)


def fmt(x, nd=4):
    if x is None:
        return "-"
    return f"{float(x):.{nd}f}"


def opt_tp_index(path: str) -> dict:
    try:
        rows = parse_journal(path)
    except FileNotFoundError:
        return {}
    idx: dict[int, int] = {}
    for pos in group_positions(rows):
        for r in pos["rows"]:
            if r["reason"] == "take_profit":
                idx[pos["entry_ts"]] = 1 if r["px"] > pos["pe"] else -1
    return idx


def analyse(cid, strategy, sk, fk, kind) -> dict:
    start, end = FOLDS[fk]
    stem = f"{strategy}_{SYM[sk]}_{start}_{end}"
    adir = f"{REPO}/data/calib/r7_{cid}"
    rows = parse_journal(f"{adir}/{stem}_journal_p.csv")
    rep = parse_report(f"{adir}/{stem}_report_p.txt")
    opt_idx = opt_tp_index(f"{adir}/{stem}_journal_o.csv")
    n_rep, net_rep = rep["trades"], D(rep["net_pnl"])
    wins_rep, losses_rep = rep["wins"], rep["losses"]
    pnl_sum = sum((r["pnl"] for r in rows), D(0))
    wins_j = sum(1 for r in rows if r["pnl"] > 0)
    losses_j = sum(1 for r in rows if r["pnl"] < 0)
    zero_j = sum(1 for r in rows if r["pnl"] == 0)
    check(n_rep == len(rows), f"{cid}: report trades {n_rep} == journal rows {len(rows)}")
    check(pnl_sum == net_rep, f"{cid}: sum(pnl) {fmt(pnl_sum,6)} == net_pnl {fmt(net_rep,6)}")
    check(wins_rep == wins_j and losses_rep == losses_j + zero_j,
          f"{cid}: wins {wins_rep}/{wins_j}, losses+zero {losses_rep}/{losses_j + zero_j}")
    eq_rep = D(rep["equity"])
    check(abs(10000 + net_rep - eq_rep) <= D("1e-12"),
          f"{cid}: equity {fmt(eq_rep,6)} == 10000 + net")
    trades: list[dict] = []
    ambig = 0
    dir_how: dict[str, int] = {}
    sign_mismatch = 0
    reasons: set[str] = set()
    for pos in group_positions(rows):
        s, how = resolve_dir(pos, kind)
        if s == 0 and kind == "s" and pos["entry_ts"] in opt_idx:
            s, how = opt_idx[pos["entry_ts"]], "opt-tp"
        if s == 0:
            ambig += 1
            for r in pos["rows"]:
                lo_a, hi_a, lo_b, hi_b = comb_candidates(r, kind)
                trades.append({**r, "s": None, "comb_a": lo_a,
                               "comb_b": lo_b, "how": how,
                               "amb_spread": hi_a - lo_a})
                reasons.add(r["reason"])
            continue
        dir_how[how] = dir_how.get(how, 0) + 1
        for t in comb_rows(pos, s, kind):
            col = t["comb_a"] if kind == "j" else t["comb_b"]
            if (col > 0) != (t["pnl"] > 0):
                sign_mismatch += 1
            reasons.add(t["reason"])
            trades.append(t)
    return {"cid": cid, "kind": kind, "rows": trades, "report": rep,
            "n_rows": len(rows), "ambig_rows": sum(1 for t in trades if t["s"] is None),
            "ambig_positions": ambig, "dir_how": dir_how, "reasons": reasons,
            "sign_mismatch": sign_mismatch,
            "net_rep": net_rep, "wins_rep": wins_rep}


def agg(tag: str, ids3: list[str], res: dict, kind: str) -> dict:
    p(f"### {tag}")
    all_trades: list[dict] = []
    fold_means: dict[str, D | None] = {}
    fold_nets: dict[str, D] = {}
    fold_mdd: dict[str, D] = {}
    fold_eq: dict[str, D] = {}
    for cid in ids3:
        fk = cid.rsplit("_", 1)[1]
        r = res[cid]
        tr = r["rows"]
        rep = r["report"]
        fold_nets[fk] = D(rep["net_pnl"])
        fold_mdd[fk] = D(rep["max_drawdown"])
        fold_eq[fk] = D(rep["equity"])
        if not tr:
            p(f"  {fk}: live run, n=0")
            fold_means[fk] = None
            continue
        a = [t["comb_a"] for t in tr]
        amb_n = sum(1 for t in tr if t["s"] is None)
        amb_sp = sum((t.get("amb_spread", D(0)) for t in tr), D(0))
        wr_a = sum(1 for x in a if x > 0) / len(a) * 100
        wr_j = r["wins_rep"] / r["n_rows"] * 100
        bstat = ""
        if kind == "s":
            b = [float(t["comb_b"]) for t in tr]
            bstat = f" | B mean={statistics.mean(b):.4f}%"
        p(f"  {fk}: n={len(tr)} (AMBIG {amb_n}, upper mean add +{float(amb_sp / len(tr)):.4f}pp) | "
          f"WR_A={wr_a:.2f}% (WR journal {wr_j:.2f}) | "
          f"A mean={statistics.mean([float(x) for x in a]):.4f}% "
          f"med={statistics.median([float(x) for x in a]):.4f}%{bstat} | "
          f"net={fmt(D(rep['net_pnl']), 4)}$ | MDD={fmt(D(rep['max_drawdown']), 2)}$ "
          f"({fmt(D(rep['max_drawdown']) / 100, 2)}%) | end eq={fmt(D(rep['equity']), 4)} "
          f"| window_cancelled={rep['window_cancelled']}")
        fold_means[fk] = sum(a, D(0)) / len(a)
        all_trades.extend(tr)
    n = len(all_trades)
    v = {"n": n, "fold_nets": fold_nets, "fold_mdd": fold_mdd, "fold_eq": fold_eq}
    p(f"  AGG: n={n}")
    if n == 0:
        p("  rules: 1 FAIL(0) => DEAD")
        p()
        return v
    a_all = [t["comb_a"] for t in all_trades]
    mean_a = sum(a_all, D(0)) / n
    wr = sum(1 for x in a_all if x > 0) / n * 100
    amb_all = sum(1 for t in all_trades if t["s"] is None)
    amb_sp_all = sum((t.get("amb_spread", D(0)) for t in all_trades), D(0))
    lo, hi = bootstrap_ci(a_all)
    p(f"  A: mean={float(mean_a):.4f}% med={statistics.median([float(x) for x in a_all]):.4f}% "
      f"WR={wr:.2f}% bootstrap95=[{float(lo):.4f}%, {float(hi):.4f}%] "
      f"(AMBIG rows {amb_all}, mean bound +{float(amb_sp_all / n):.4f}pp)")
    if kind == "s":
        b_all = [t["comb_b"] for t in all_trades]
        p(f"  B (maker): mean={statistics.mean([float(x) for x in b_all]):.4f}% med="
          f"{statistics.median([float(x) for x in b_all]):.4f}% "
          f"WR={(sum(1 for t in all_trades if t['comb_b'] > 0) / n * 100):.2f}%")
    net_all = sum(fold_nets.values(), D(0))
    p(f"  AGG net={fmt(net_all, 4)}$ (sum of 3 fold report net_pnl)")
    r1 = n >= 30
    r2 = mean_a >= D("0.30")
    r3 = all(x is not None and x > 0 for x in fold_means.values())
    r4 = lo > 0
    r5 = True
    r6 = all(x >= 0 for x in fold_nets.values())
    alive = r1 and r2 and r3 and r4 and r5 and r6
    p(f"  rules: 1 [{'P' if r1 else 'F'}]({n}); 2 [{'P' if r2 else 'F'}]({fmt(mean_a)}); "
      f"3 [{'P' if r3 else 'F'}](" + ", ".join(f"{k}={fmt(vv)}" for k, vv in fold_means.items()) + "); "
      f"4 [{'P' if r4 else 'F'}]({fmt(lo)}); 5 [P]; "
      f"6 [{'P' if r6 else 'F'}](" + ", ".join(f"{k}={fmt(vv, 2)}" for k, vv in fold_nets.items()) + ")"
      f" => {'ALIVE' if alive else 'DEAD'}")
    p()
    v.update({"alive": alive, "mean_a": mean_a, "lo": lo, "hi": hi, "wr": wr,
              "fold_means": fold_means, "net_all": net_all,
              "r": (r1, r2, r3, r4, r5, r6)})
    return v


def main() -> None:
    p("ROUND-7 CSV-ONLY AGGREGATION (brother). No DB reads at all.")
    p()
    res: dict[str, dict] = {}
    for cid, strategy, sk, fk, kind in CELLS:
        res[cid] = analyse(cid, strategy, sk, fk, kind)
    p("== DIRECTIONAL CENSUS (artifacts only) ==")
    tot_how: dict[str, int] = {}
    tot_amb_pos = tot_amb_rows = tot_mismatch = 0
    reasons_all: set[str] = set()
    for cid, r in res.items():
        for k, v in r["dir_how"].items():
            tot_how[k] = tot_how.get(k, 0) + v
        tot_amb_pos += r["ambig_positions"]
        tot_amb_rows += r["ambig_rows"]
        tot_mismatch += r["sign_mismatch"]
        reasons_all.update(r["reasons"])
    p(f"  exit_reason in all journals: {sorted(reasons_all)}")
    p(f"  positions resolved by {tot_how}; ambiguous positions {tot_amb_pos}, "
      f"rows {tot_amb_rows} (conservative lower comb)")
    p(f"  sign(comb) != sign(pnl) rows: {tot_mismatch} (must be 0)")
    check(tot_mismatch == 0, f"comb-sign vs pnl-sign: {tot_mismatch} mismatches")
    p()
    p("== PRIMARY CELLS (4 variants x 2 symbols; TF signed) ==")
    p()
    V: dict[str, dict] = {}
    for pref, strategy, kind, lbl in VARIANTS:
        for sk in ("btc", "eth"):
            tag = f"{pref} x {SYM[sk]} ({lbl})"
            ids = [f"{pref}_{sk}_{fk}" for fk in ("f1", "f2", "f3")]
            V[f"{pref}_{sk}"] = agg(tag, ids, res, kind)
    p("== WINDOW_CANCELLED (limit cancels at window end, not skip counter) ==")
    for pref, _s, _k, _l in VARIANTS:
        for sk in ("btc", "eth"):
            ids = [f"{pref}_{sk}_{fk}" for fk in ("f1", "f2", "f3")]
            p(f"  {pref} {sk}: " + ", ".join(f"{res[c]['report']['window_cancelled']}"
                                             for c in ids))
    p("== CONSISTENCY CHECKS ==")
    fails = [c for c in CHECKS if c.startswith("FAIL")]
    p(f"  total checks {len(CHECKS)}, failed {len(fails)}")
    for c in fails:
        p("  " + c)
    p("== RULE 6 (dollar, amendment 1) per cell ==")
    for key, v in V.items():
        if "r" in v:
            p(f"  {key}: " + ", ".join(f"{k}={fmt(x, 2)}" for k, x in v["fold_nets"].items())
              + f" => rule6 {'PASS' if v['r'][5] else 'FAIL'}")
    text = "\n".join(out_lines)
    print(text)
    with open("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r7/csvonly_out.txt",
              "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
