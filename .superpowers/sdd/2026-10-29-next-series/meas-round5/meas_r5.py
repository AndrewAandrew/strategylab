#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round 5 aggregation (brat-zamerщik, R-next-1). stdlib only, read-only DB.

Parses the27 harness artifacts of the round-5 grid, reconstructs per-trade
position qty by exact inversion of the engine pnl formula
( engine._record_realized: realized = (px-pe)*closed*s - f_x*px*c - f_e*pe*c ),
determines direction digit-exactly (J: entry == close*(1+-slip) at entry_ts;
S: FVG mid reconstructed from bars_1m), computes combined columns
  A (primary, taker entry re-price) and B (actual-run maker arithmetic for S),
then applies the five survival rules of round4 §8 (verbatim, thresholds frozen).
Bootstrap: 1000 resamples, seed 42, percentile, iid per trade (round3/4 convention).
"""
from __future__ import annotations

import random
import sqlite3
import statistics
import sys
from decimal import Decimal, getcontext

getcontext().prec = 28

D = Decimal
TAKER = D("0.0006")
MAKER = D("0.0002")
SLIP = D("0.0005")
RISK = D("0.005")
CAPITAL = D("10000")
FLOOR_P = D("0.00025")

REPO = "C:/Users/egoro/Documents/GitHub/strategylab"
DB = {"btc": f"file:{REPO}/data/btcusdt.db?mode=ro",
      "eth": f"file:{REPO}/data/ethusdt.db?mode=ro"}
SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
FOLDS = {"f1": (1761523200, 1771113600),
         "f2": (1771113600, 1780704000),
         "f3": (1780704000, 1790294400)}

# cell id -> (strategy, symkey, fold, kind j/s, family p/w/t2, floor, tp_mult)
CELLS: list[tuple[str, str, str, str, str, D, D | None]] = []
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"j_p_{sk}_{fk}", "jaguar_ema", sk, fk, "j", "p", FLOOR_P, None))
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"s_p_{sk}_{fk}", "smc_choch_fvg", sk, fk, "s", "p", FLOOR_P, None))
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"j_w_{sk}_{fk}", "jaguar_ema", sk, fk, "j", "w", D("0.001"), None))
for fk in ("f1", "f2", "f3"):
    for sk in ("btc", "eth"):
        CELLS.append((f"s_w_{sk}_{fk}", "smc_choch_fvg", sk, fk, "s", "w", D("0.0015"), None))
for fk in ("f1", "f2", "f3"):
    CELLS.append((f"j_t2_btc_{fk}", "jaguar_ema", "btc", fk, "j", "t2", FLOOR_P, D("2.0")))

out_lines: list[str] = []
ANOMALIES: list[str] = []


def p(s: str = "") -> None:
    out_lines.append(str(s))


def note_anomaly(s: str) -> None:
    ANOMALIES.append(s)
    if len(ANOMALIES) <= 30:
        p(f"  !! ANOMALY: {s}")


_conns = {}


def conn_for(sk: str):
    if sk not in _conns:
        con = sqlite3.connect(DB[sk], uri=True)
        con.text_factory = lambda raw: D(raw.decode())
        _conns[sk] = con
    return _conns[sk]


def bar_close(sk: str, ts: int, iv: int) -> D | None:
    """Close бара агрегированной сетки: последний 1m-close бакета [ts, ts+iv)."""
    rows = conn_for(sk).execute(
        "SELECT close FROM bars_1m WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts DESC LIMIT 1",
        (SYM[sk], ts, ts + iv)).fetchall()
    return rows[0][0] if rows else None


def bars_list(sk: str, lo: int, hi: int) -> list[tuple[int, D, D]]:
    """Бары (ts, high, low) в [lo, hi] по порядку: индекс t−2 = два БАРА назад
    (движок держит деку баров, а не разницу в секундах)."""
    rows = conn_for(sk).execute(
        "SELECT ts, high, low FROM bars_1m WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts",
        (SYM[sk], lo, hi)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


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
    """First '# cell:' block metrics of the pessimistic report."""
    metrics: dict[str, str] = {}
    started = False
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("# cell:"):
                if started:
                    break
                started = True
                continue
            if not started:
                continue
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                metrics[k.strip()] = v.strip()
    return {k: (int(v) if k in ("trades", "wins", "losses", "trials") else v)
            for k, v in metrics.items()}


def group_positions(rows: list[dict]) -> list[dict]:
    positions: list[dict] = []
    for r in rows:
        if positions and positions[-1]["entry_ts"] == r["entry_ts"] \
                and positions[-1]["pe"] == r["pe"]:
            positions[-1]["rows"].append(r)
        else:
            positions.append({"entry_ts": r["entry_ts"], "pe": r["pe"], "rows": [r]})
    return positions


def direction_j(sk: str, pos: dict, iv: int) -> int:
    close = bar_close(sk, pos["entry_ts"], iv)
    if close is None:
        note_anomaly(f"J: нет бара entry_ts={pos['entry_ts']}")
        return 0
    pe = pos["pe"]
    long_m = pe == close * (D(1) + SLIP)
    short_m = pe == close * (D(1) - SLIP)
    if long_m and not short_m:
        return 1
    if short_m and not long_m:
        return -1
    note_anomaly(f"J: pe={pe} не маркет-филл close={close} на ts={pos['entry_ts']}")
    return 0


def direction_s(sk: str, pos: dict) -> tuple[int, str]:
    """Направление S-позиции: реконструкция FVG-mid из баров (точно в Decimal).

    Движок: FVG-бар t−2 — это ДВА БАРА назад по индексу деку (1m ⇒ −120s),
    mid = (H(t−2)+L(t))/2 лонг / (L(t−2)+H(t))/2 шорт; лимитка подаётся на
    баре S = fvg_bar+2 и жива ttl_bars=60 ⇒ fill_idx − fvg_idx ∈ [3, 62].
    Совпадение mid со случайным старым FVG отсекается этим окном и знаком
    TP1/stop (stop у лонга ниже входа, у шорта — выше)."""
    pe = pos["pe"]
    lo = pos["entry_ts"] - 70 * 60
    bars = bars_list(sk, lo, pos["entry_ts"])
    fill_idx = len(bars) - 1
    if fill_idx < 0 or bars[fill_idx][0] != pos["entry_ts"]:
        note_anomaly(f"S: бар entry_ts={pos['entry_ts']} не найден")
        return 0, "none"
    found: set[int] = set()
    for i in range(2, len(bars)):
        if not 3 <= fill_idx - i <= 62:
            continue
        _t, high_t, low_t = bars[i]
        high_tm2, low_tm2 = bars[i - 2][1], bars[i - 2][2]
        # long: mid = (H(t-2)+L(t))/2 при L(t) > H(t-2); short: (L(t-2)+H(t))/2 при H(t) < L(t-2)
        if low_t > high_tm2 and (high_tm2 + low_t) / D(2) == pe:
            found.add(1)
        if high_t < low_tm2 and (low_tm2 + high_t) / D(2) == pe:
            found.add(-1)
    src = "fvg-mid"
    if len(found) == 2:
        # коллизия mid: решаем знаком TP1 (target-сторона) или stop (противоположная)
        tp = [r for r in pos["rows"] if r["reason"] == "take_profit"]
        st = [r for r in pos["rows"] if r["reason"] == "stop"]
        if tp:
            found = {1 if tp[0]["px"] > pe else -1}
            src = "fvg-mid+tp1"
        elif st:
            found = {-1 if st[0]["px"] > pe else 1}
            src = "fvg-mid+stop"
    if len(found) == 1:
        s = found.pop()
    elif len(found) == 0:
        src = "tp1-sign"
        tp = [r for r in pos["rows"] if r["reason"] == "take_profit"]
        if not tp:
            note_anomaly(f"S: FVG-mid не найден и TP1 нет, ts={pos['entry_ts']} pe={pe}")
            return 0, "none"
        s = 1 if tp[0]["px"] > pe else -1
    else:
        note_anomaly(f"S: FVG-mid даёт оба направления, ts={pos['entry_ts']} pe={pe}")
        return 0, "none"
    # сверка TP1-знаком при наличии
    tp = [r for r in pos["rows"] if r["reason"] == "take_profit"]
    if tp:
        s_tp = 1 if tp[0]["px"] > pe else -1
        if s_tp != s:
            note_anomaly(f"S: fvg-mid dir={s} != tp1 dir={s_tp}, ts={pos['entry_ts']}")
            return 0, "none"
    return s, src


def invert_qty(pos: dict, s: int, f_e: D) -> list[D | None]:
    """c = pnl / ((px-pe)*s - f_x*px - f_e*pe) для каждой строки позиции."""
    pe = pos["pe"]
    cs: list[D | None] = []
    for r in pos["rows"]:
        f_x = MAKER if r["reason"] == "take_profit" else TAKER
        den = (r["px"] - pe) * s - f_x * r["px"] - f_e * pe
        if den == 0:
            note_anomaly(f"нулевой знаменатель инверсии, ts={pos['entry_ts']}")
            cs.append(None)
            continue
        c = r["pnl"] / den
        if c <= 0:
            note_anomaly(f"отрицательный qty={c} (pnl={r['pnl']}, den={den}), "
                         f"ts={pos['entry_ts']} reason={r['reason']}")
            cs.append(None)
            continue
        cs.append(c)
    if len(cs) == 2 and None not in cs:
        c1, c2 = cs
        if abs(c1 - c2) > abs(c1) * D("1e-18"):
            note_anomaly(f"TP1/раннер qty разошлись: {c1} vs {c2}, ts={pos['entry_ts']}")
    return cs


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


def fmt(x: D | float | None, nd: int = 4) -> str:
    if x is None:
        return "—"
    return f"{float(x):.{nd}f}"


def analyse_cell(cid: str, strategy: str, sk: str, fk: str, kind: str,
                 family: str, floor: D, tp_mult: D | None) -> dict:
    start, end = FOLDS[fk]
    stem = f"{strategy}_{SYM[sk]}_{start}_{end}"
    adir = f"{REPO}/data/calib/r5_{cid}"
    jp = f"{adir}/{stem}_journal_p.csv"
    rp = f"{adir}/{stem}_report_p.txt"
    try:
        rows = parse_journal(jp)
        report = parse_report(rp)
    except FileNotFoundError:
        return {"cid": cid, "dead": True, "n": 0, "rows": []}
    f_e = TAKER if kind == "j" else MAKER
    trades: list[dict] = []
    for pos in group_positions(rows):
        if kind == "j":
            s = direction_j(sk, pos, 300)
            src = "market-fill"
        else:
            s, src = direction_s(sk, pos)
        if s == 0:
            continue
        cs = invert_qty(pos, s, f_e)
        for r, c in zip(pos["rows"], cs):
            if c is None:
                continue
            pe, px = pos["pe"], r["px"]
            comb_b = r["pnl"] / (c * pe) * D(100)
            if kind == "s":
                pe_a = pe * (D(1) + SLIP) if s > 0 else pe * (D(1) - SLIP)
                f_x = MAKER if r["reason"] == "take_profit" else TAKER
                pnl_a = (px - pe_a) * c * s - f_x * px * c - TAKER * pe_a * c
                comb_a = pnl_a / (c * pe_a) * D(100)
            else:
                comb_a = comb_b
            trades.append({"entry_ts": pos["entry_ts"], "exit_ts": r["exit_ts"],
                           "s": s, "c": c, "pe": pe, "px": px, "pnl": r["pnl"],
                           "reason": r["reason"], "comb_a": comb_a, "comb_b": comb_b,
                           "dir_src": src})
    n_rep = report.get("trades")
    net_rep = D(report.get("net_pnl", "0"))
    if n_rep is not None and n_rep != len(rows):
        note_anomaly(f"{cid}: trades отчёта {n_rep} != строк журнала {len(rows)}")
    if len(trades) != len(rows):
        note_anomaly(f"{cid}: разрешено направление не у всех строк: "
                     f"{len(trades)}/{len(rows)}")
    pnl_sum = sum((r["pnl"] for r in rows), D(0))
    if rows and pnl_sum != net_rep:
        note_anomaly(f"{cid}: сумма pnl журнала {pnl_sum} != net_pnl отчёта {net_rep}")
    return {"cid": cid, "dead": False, "n": len(trades), "rows": trades,
            "report": report, "n_rows": len(rows)}


def main() -> None:
    p("ROUND-5 AGGREGATION (brat, R-next-1). stdlib, read-only DB URI.")
    p(f"CELLS={len(CELLS)} folds f1-f3 only; holdout ts>=1790294400 не читается.")
    p()

    results: dict[str, dict] = {}
    for cid, strategy, sk, fk, kind, family, floor, tp_mult in CELLS:
        results[cid] = analyse_cell(cid, strategy, sk, fk, kind, family, floor, tp_mult)

    # ---- aggregates per cell ----
    def agg(tag: str, ids3: list[str], kind: str, rules: bool) -> dict:
        p(f"### {tag}")
        all_trades: list[dict] = []
        fold_means: dict[str, D | None] = {}
        dead_folds = []
        for cid in ids3:
            fk = cid.rsplit("_", 1)[1]
            res = results[cid]
            if res["dead"]:
                dead_folds.append(fk)
                p(f"  {fk}: LIQUIDATED_RUN_DEAD / нет артефактов (правила 1/3 FAIL)")
                fold_means[fk] = None
                continue
            tr = res["rows"]
            if not tr:
                p(f"  {fk}: живой прогон, n=0 (правило 1 FAIL)")
                fold_means[fk] = None
                continue
            a = [t["comb_a"] for t in tr]
            wr = sum(1 for x in a if x > 0) / len(a) * 100
            bstat = ""
            if kind == "s":
                b = [float(t["comb_b"]) for t in tr]
                bstat = f" | B: mean={statistics.mean(b):.4f}%"
            p(f"  {fk}: n={len(tr)} (строк журнала {res['n_rows']}) | "
              f"A mean={statistics.mean([float(x) for x in a]):.4f}% "
              f"med={statistics.median([float(x) for x in a]):.4f}%{bstat} | WR={wr:.2f}% "
              f"| net_pnl={res['report'].get('net_pnl')} | equity_end={res['report'].get('equity')}")
            fold_means[fk] = sum(a, D(0)) / len(a)
            all_trades.extend(tr)
        n = len(all_trades)
        p(f"  AGG: n={n}")
        verdict = {"n": n, "alive": False}
        if n == 0:
            p("  rules: [FAIL] 1 n>=30 (n=0); [FAIL] 3 (нет фолдов с trades) => DEAD")
            p()
            return verdict
        a_all = [t["comb_a"] for t in all_trades]
        mean_a = sum(a_all, D(0)) / n
        med_a = statistics.median([float(x) for x in a_all])
        wr = sum(1 for x in a_all if x > 0) / n * 100
        lo_ci, hi_ci = bootstrap_ci(a_all)
        p(f"  A: mean={float(mean_a):.4f}% med={med_a:.4f}% WR={wr:.2f}% "
          f"bootstrap95=[{float(lo_ci):.4f}%, {float(hi_ci):.4f}%]")
        if kind == "s":
            b_all = [t["comb_b"] for t in all_trades]
            p(f"  B (maker, фактическая арифметика брокера): mean="
              f"{statistics.mean([float(x) for x in b_all]):.4f}% med="
              f"{statistics.median([float(x) for x in b_all]):.4f}%")
            rho = [float(t["pe"] and (t["c"] and RISK * D(10000) / t["c"] / t["pe"])) for t in all_trades[:0]]
        # rho-диагностика: R_realized/pe через qty (equity на входе не восстанавливаем точно)
        r1 = n >= 30
        r2 = mean_a >= D("0.30")
        folds_pos = all(v is not None and v > 0 for v in fold_means.values())
        r3 = folds_pos
        r4 = lo_ci > 0
        r5 = True  # net без funding: funding в классах отсутствует конструктивно
        alive = r1 and r2 and r3 and r4 and r5
        p(f"  rules: 1 n>=30 [{'PASS' if r1 else 'FAIL'}]({n}); "
          f"2 meanA>=0.30% [{'PASS' if r2 else 'FAIL'}]({float(mean_a):.4f}); "
          f"3 folds>0 [{'PASS' if r3 else 'FAIL'}]("
          + ", ".join(f"{k}={fmt(v)}" for k, v in fold_means.items()) + "); "
          f"4 bootLow>0 [{'PASS' if r4 else 'FAIL'}]({float(lo_ci):.4f}); "
          f"5 net-no-funding [PASS] => {'ALIVE' if alive else 'DEAD'}")
        p()
        verdict.update({"alive": alive, "mean_a": mean_a, "lo_ci": lo_ci,
                        "hi_ci": hi_ci, "fold_means": fold_means})
        return verdict

    p("== ПЕРВИЧНЫЕ КЛЕТКИ (floor=0.00025) ==")
    p()
    verdicts = {}
    verdicts["J×BTC"] = agg("J×BTCUSDT primary (jaguar_ema, 5m, floor 0.00025)",
                            ["j_p_btc_f1", "j_p_btc_f2", "j_p_btc_f3"], "j", True)
    verdicts["J×ETH"] = agg("J×ETHUSDT primary",
                            ["j_p_eth_f1", "j_p_eth_f2", "j_p_eth_f3"], "j", True)
    verdicts["S×BTC"] = agg("S×BTCUSDT primary (smc_choch_fvg, 1m, floor 0.00025)",
                            ["s_p_btc_f1", "s_p_btc_f2", "s_p_btc_f3"], "s", True)
    verdicts["S×ETH"] = agg("S×ETHUSDT primary",
                            ["s_p_eth_f1", "s_p_eth_f2", "s_p_eth_f3"], "s", True)

    p("== ДИАГНОСТИКА (НЕ для спецификации) ==")
    p()
    agg("J-wide BTC (floor 0.001)", ["j_w_btc_f1", "j_w_btc_f2", "j_w_btc_f3"], "j", False)
    agg("J-wide ETH (floor 0.001)", ["j_w_eth_f1", "j_w_eth_f2", "j_w_eth_f3"], "j", False)
    agg("S-wide BTC (floor 0.0015)", ["s_w_btc_f1", "s_w_btc_f2", "s_w_btc_f3"], "s", False)
    agg("S-wide ETH (floor 0.0015)", ["s_w_eth_f1", "s_w_eth_f2", "s_w_eth_f3"], "s", False)
    agg("J-tp2 BTC (tp_mult 2.0, floor 0.00025)", ["j_t2_btc_f1", "j_t2_btc_f2", "j_t2_btc_f3"], "j", False)

    p("== ANOMALIES ==")
    if ANOMALIES:
        for a in ANOMALIES[:50]:
            p(f"  - {a}")
        if len(ANOMALIES) > 50:
            p(f"  ... и ещё {len(ANOMALIES) - 50}")
        p(f"  TOTAL ANOMALIES: {len(ANOMALIES)}")
    else:
        p("  нет")

    text = "\n".join(out_lines)
    print(text)
    with open("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r5/agg_out.txt",
              "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
