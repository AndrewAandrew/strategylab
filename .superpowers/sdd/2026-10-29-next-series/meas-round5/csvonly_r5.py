# -*- coding: utf-8 -*-
"""Round 5 aggregation — CSV-ONLY (successor letter, R-next-1). stdlib, NO DB.

Numbers are taken exclusively from data/calib/r5_*/ artifacts (journal_p.csv +
report_p.txt + metrics .csv). The DB is never opened (contract for this letter:
holdout R-next-2, bars untouched).

Per-row combined %-on-notional:
  engine._record_realized: pnl_row = (px-pe)*c*s - f_x*px*c - f_e*pe*c
  => comb = pnl/(c*pe) = ((px-pe)*s - f_x*px - f_e*pe)/pe  — qty-free identity.

Direction s from artifacts only:
  reason == "take_profit"  -> s = sign(px - pe)         (TP always favourable)
  reason == "stop"         -> s = +1 if px < pe else -1 (stop always adverse)
  otherwise ("market")     -> if pnl != 0 and the sign(px-pe) candidate yields
                              the same sign(den) = sign(pnl): determinate;
                              else (loss inside the fee spread |px-pe| < fees)
                              AMBIGUOUS: position sibling (TP1/stop row with
                              same entry_ts+pe) resolves; if none, the two
                              candidates are within 2*|px-pe|/pe < ~0.22 pp and
                              the CONSERVATIVE (lower comb_a) value is used.

Profile A (primary, ПЕРВИЧНЫЙ): entry as taker market — for S pe_A = pe*(1±slip),
fee TAKER; for J the run itself is taker market => comb_A = journal identity.
Profile B (secondary, фактическая арифметика брокера на S-сделках): entry is the
limit fill at mid, maker 0.0002 — journal pnl is exactly profile B.

Bootstrap: 1000 resamples, seed 42, percentile, iid per trade row (round 3/4
convention; identical function to the predecessor's meas_r5.py so the lead can
audit digit-in-digit).
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
FEE_SPREAD = TAKER + MAKER + SLIP + SLIP  # worst-case round trip fraction (0.0012)

REPO = "C:/Users/egoro/Documents/GitHub/strategylab"
CELLS = []
FLOOR_P = D("0.00025")
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"j_p_{sk}_{fk}", "jaguar_ema", sk, fk, "j", "p", FLOOR_P))
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"s_p_{sk}_{fk}", "smc_choch_fvg", sk, fk, "s", "p", FLOOR_P))
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"j_w_{sk}_{fk}", "jaguar_ema", sk, fk, "j", "w", D("0.001")))
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"s_w_{sk}_{fk}", "smc_choch_fvg", sk, fk, "s", "w", D("0.0015")))
for fk in ("f1", "f2", "f3"):
    CELLS.append((f"j_t2_btc_{fk}", "jaguar_ema", "btc", fk, "j", "t2", FLOOR_P))

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
    # multi-row position: TP1-half and runner-half close the SAME quantity c.
    # pnl_row = c * den_row. Test both signs; accept the one with c>0 and
    # digit-consistent across rows. This is decisive even when the bracket
    # fill sits on the wrong side of a slipped entry (floor-minimal R:
    # TP1 = 1.5*R < slip distance, so a "take_profit" can print pnl < 0).
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
    # single-row fallbacks
    r = pos["rows"][0]
    if len(pos["rows"]) == 1 and r["reason"] == "take_profit" and kind == "s":
        # S entry = limit at mid, TP level 4R on the favourable side — decisive
        return (1 if r["px"] > pe else -1), "tp"
    if len(pos["rows"]) == 1 and r["reason"] == "stop":
        # a single "stop" row = whole position closed by a frozen SL bracket
        # (no trail above entry possible before TP1: EMA13 <= closes <= high <
        # TP level). Stop is on the adverse side of the FROZEN entry: decisive.
        s = 1 if r["px"] < pe else -1
        f_x = TAKER
        den = _den(pe, r["px"], s, f_x, f_e)
        if den != 0 and r["pnl"] / den > 0:
            return s, "stop"
    # pnl-sign resolution (fee-aware): candidate den must match sign(pnl)
    f_x = TAKER  # reason is "market" here (or J single-row tp fallback)
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
    """(lo_a, hi_a, lo_b, hi_b) combined over both candidate directions."""
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


def comb_ambig(r: dict, kind: str) -> D:
    return comb_candidates(r, kind)[0]


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
        return "—"
    return f"{float(x):.{nd}f}"


def opt_tp_index(path: str) -> dict:
    """entry_ts -> direction via optimistic-fork TP1 row (decisive if it exists):
    a take_profit in the optimistic fork sits on the favourable side of entry."""
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


def analyse(cid, strategy, sk, fk, kind, family, floor) -> dict:
    start, end = FOLDS[fk]
    stem = f"{strategy}_{SYM[sk]}_{start}_{end}"
    adir = f"{REPO}/data/calib/r5_{cid}"
    rows = parse_journal(f"{adir}/{stem}_journal_p.csv")
    rep = parse_report(f"{adir}/{stem}_report_p.txt")
    opt_idx = opt_tp_index(f"{adir}/{stem}_journal_o.csv")
    # consistency: journal vs report
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
            # optimistic fork reached TP1: for S the TP level is strictly on the
            # favourable side of the mid entry, so its side is decisive
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
    return {"cid": cid, "kind": kind, "family": family, "rows": trades, "report": rep,
            "n_rows": len(rows), "ambig_rows": sum(1 for t in trades if t["s"] is None),
            "ambig_positions": ambig, "dir_how": dir_how, "reasons": reasons,
            "sign_mismatch": sign_mismatch,
            "net_rep": net_rep, "wins_rep": wins_rep}


def agg(tag: str, ids3: list[str], res: dict, kind: str, rules: bool) -> dict:
    p(f"### {tag}")
    all_trades: list[dict] = []
    fold_means: dict[str, D | None] = {}
    for cid in ids3:
        fk = cid.rsplit("_", 1)[1]
        r = res[cid]
        tr = r["rows"]
        if not tr:
            p(f"  {fk}: живой прогон, n=0")
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
        rep = r["report"]
        p(f"  {fk}: n={len(tr)} (AMBIG {amb_n}, upper mean add +{float(amb_sp / len(tr)):.4f}pp) | "
          f"WR_A={wr_a:.2f}% (WR по журналу {wr_j:.2f}) | "
          f"A mean={statistics.mean([float(x) for x in a]):.4f}% "
          f"med={statistics.median([float(x) for x in a]):.4f}%{bstat} | "
          f"net={fmt(D(rep['net_pnl']), 4)}$ | MDD={fmt(D(rep['max_drawdown']), 2)}$ "
          f"({fmt(D(rep['max_drawdown']) / 100, 2)}%) | end eq={fmt(D(rep['equity']), 4)}")
        fold_means[fk] = sum(a, D(0)) / len(a)
        all_trades.extend(tr)
    n = len(all_trades)
    v = {"n": n}
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
        p(f"  B (maker, фактическая арифметика брокера): mean="
          f"{statistics.mean([float(x) for x in b_all]):.4f}% med="
          f"{statistics.median([float(x) for x in b_all]):.4f}% "
          f"WR={(sum(1 for t in all_trades if t['comb_b'] > 0) / n * 100):.2f}%")
    r1 = n >= 30
    r2 = mean_a >= D("0.30")
    r3 = all(x is not None and x > 0 for x in fold_means.values())
    r4 = lo > 0
    r5 = True
    alive = r1 and r2 and r3 and r4 and r5
    p(f"  rules: 1 [{'P' if r1 else 'F'}]({n}); 2 [{'P' if r2 else 'F'}]({fmt(mean_a)}); "
      f"3 [{'P' if r3 else 'F'}](" + ", ".join(f"{k}={fmt(vv)}" for k, vv in fold_means.items()) + "); "
      f"4 [{'P' if r4 else 'F'}]({fmt(lo)}); 5 [P] => {'ALIVE' if alive else 'DEAD'}")
    p()
    v.update({"alive": alive, "mean_a": mean_a, "lo": lo, "hi": hi, "wr": wr,
              "fold_means": fold_means})
    return v


def main() -> None:
    p("ROUND-5 CSV-ONLY AGGREGATION (successor). No DB reads at all.")
    p()
    res: dict[str, dict] = {}
    for cid, strategy, sk, fk, kind, family, floor in CELLS:
        res[cid] = analyse(cid, strategy, sk, fk, kind, family, floor)
    # direction census & reasons census
    p("== ДИРЕКЦИОННЫЙ ЦЕНЗ (только артефакты) ==")
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
    p(f"  exit_reason во всех журналах: {sorted(reasons_all)}")
    p(f"  позиции: разрешены по способу {tot_how}; неоднозначных позиций {tot_amb_pos}, "
      f"строк {tot_amb_rows} (консервативный минимальный comb)")
    p(f"  sign(comb) != sign(pnl) строк: {tot_mismatch} (обязано быть 0)")
    check(tot_mismatch == 0, f"знак-comb vs знак-pnl: {tot_mismatch} расхождений")
    p()
    p("== ПЕРВИЧНЫЕ КЛЕТКИ (floor=0.00025) ==")
    p()
    V = {}
    V["JxBTC"] = agg("J×BTCUSDT primary", ["j_p_btc_f1", "j_p_btc_f2", "j_p_btc_f3"], res, "j", True)
    V["JxETH"] = agg("J×ETHUSDT primary", ["j_p_eth_f1", "j_p_eth_f2", "j_p_eth_f3"], res, "j", True)
    V["SxBTC"] = agg("S×BTCUSDT primary", ["s_p_btc_f1", "s_p_btc_f2", "s_p_btc_f3"], res, "s", True)
    V["SxETH"] = agg("S×ETHUSDT primary", ["s_p_eth_f1", "s_p_eth_f2", "s_p_eth_f3"], res, "s", True)
    p("== ДИАГНОСТИКА (НЕ для спецификации) ==")
    p()
    agg("J-wide BTC (0.001)", ["j_w_btc_f1", "j_w_btc_f2", "j_w_btc_f3"], res, "j", False)
    agg("J-wide ETH (0.001)", ["j_w_eth_f1", "j_w_eth_f2", "j_w_eth_f3"], res, "j", False)
    agg("S-wide BTC (0.0015)", ["s_w_btc_f1", "s_w_btc_f2", "s_w_btc_f3"], res, "s", False)
    agg("S-wide ETH (0.0015)", ["s_w_eth_f1", "s_w_eth_f2", "s_w_eth_f3"], res, "s", False)
    agg("J-tp2 BTC (2.0)", ["j_t2_btc_f1", "j_t2_btc_f2", "j_t2_btc_f3"], res, "j", False)
    p("== WINDOW_CANCELLED (снятые лимитки на конце окна, не skip-счётчик) ==")
    p("  J первичные: " + ", ".join(f"{c}:{res[c]['report']['window_cancelled']}"
                                    for c in ("j_p_btc_f1", "j_p_btc_f2", "j_p_btc_f3",
                                              "j_p_eth_f1", "j_p_eth_f2", "j_p_eth_f3")))
    p("  S первичные: " + ", ".join(f"{c}:{res[c]['report']['window_cancelled']}"
                                    for c in ("s_p_btc_f1", "s_p_btc_f2", "s_p_btc_f3",
                                              "s_p_eth_f1", "s_p_eth_f2", "s_p_eth_f3")))
    p("== CONSISTENCY CHECKS ==")
    fails = [c for c in CHECKS if c.startswith("FAIL")]
    p(f"  всего проверок {len(CHECKS)}, провалено {len(fails)}")
    for c in fails:
        p("  " + c)
    text = "\n".join(out_lines)
    print(text)
    with open("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r5/csvonly_out.txt",
              "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
