# -*- coding: utf-8 -*-
"""Positional fold comparison: my csvonly output vs predecessor's agg_out.
Both files enumerate cells in the same order (Jp BTC, Jp ETH, Sp BTC, Sp ETH,
Jw BTC, Jw ETH, Sw BTC, Sw ETH, J-tp2) x folds f1..f3."""
import re

def fold_lines(path):
    out = []
    cur = None
    for line in open(path, encoding="utf-8"):
        m = re.match(r"### (.+)", line)
        if m:
            cur = m.group(1).strip()
            continue
        m = re.match(r"\s+(f[123]): (.*)", line)
        if m and cur:
            out.append((cur, m.group(1), m.group(2)))
    return out

new = fold_lines("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r5/csvonly_out.txt")
old = fold_lines("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r5/agg_out.txt")
assert len(new) == len(old) == 27, (len(new), len(old))

def n_of(s):
    return int(re.search(r"n=(\d+)", s).group(1))

def mean_of(s):
    return float(re.search(r"A mean=(-?[\d.]+)%", s).group(1))

def bmean_of(s):
    m = re.search(r"B mean=(-?[\d.]+)%", s) or re.search(r"B: mean=(-?[\d.]+)%", s)
    return float(m.group(1)) if m else None

names = ["Jp-BTC", "Jp-ETH", "Sp-BTC", "Sp-ETH", "Jw-BTC", "Jw-ETH", "Sw-BTC", "Sw-ETH", "t2-BTC"]
print(f"{'cell.fold':12} {'n_old':>6} {'n_new':>6} {'A_old':>9} {'A_new':>9} {'gap_pp':>7} {'bound':>7} {'B_old':>8} {'B_new':>8}")
ok = True
for i, ((tc, fc, sn), (to, fo, so)) in enumerate(zip(new, old)):
    assert fc == fo, (fc, fo)
    n_old, n_new = n_of(so), n_of(sn)
    a_old, a_new = mean_of(so), mean_of(sn)
    bound = float(re.search(r"upper mean add \+([\d.]+)pp", sn).group(1))
    b_old, b_new = bmean_of(so), bmean_of(sn)
    if n_old != n_new or a_new > a_old or a_new < a_old - 1.5 * bound - 1e-9:
        ok = False
    gap = a_old - a_new
    bs = "       —        —" if (b_old is None and b_new is None) else f"{b_old!s:>8} {b_new!s:>8}"
    print(f"{names[i // 3]}-{fc:>3} {n_old:>6} {n_new:>6} {a_old:>9} {a_new:>9} {gap:>7.4f} {bound:>7.4f} {bs}")
    if b_old is not None and b_new is not None and b_new > b_old:
        ok = False
print("POSITIONAL ORDER & BOUNDS:", "OK" if ok else "CHECK")
