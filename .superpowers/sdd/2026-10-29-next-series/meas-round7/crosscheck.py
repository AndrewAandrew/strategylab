# -*- coding: utf-8 -*-
import re, io
out = io.open("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r7/csvonly_out.txt", encoding="utf-8").read()
letter = io.open("C:/Users/egoro/Documents/GitHub/strategylab/.superpowers/letters/2026-11-02-round7-setka-brata.md", encoding="utf-8").read().replace(chr(0x2212), "-")
# parse AGG blocks: cell tag -> (n, mean, net, lo, hi)
blocks = re.split(r"### ", out)[1:]
bad = []
for b in blocks:
    tag = b.splitlines()[0].strip()
    agg = re.search(r"AGG: n=(\d+)", b).group(1)
    mean = re.search(r"A: mean=(-?[\d.]+)%", b).group(1)
    lo, hi = re.search(r"bootstrap95=\[(-?[\d.]+)%, (-?[\d.]+)%\]", b).groups()
    net = re.search(r"AGG net=(-?[\d.]+)\$", b).group(1)
    # letter numbers
    def has(x): return x in letter
    # mean formatting in letter: 4 decimals with sign
    m4 = ("+" + mean if float(mean) >= 0 else mean)
    if agg not in letter: bad.append((tag, "n", agg))
    if not has(m4): bad.append((tag, "mean", m4))
    if not has(lo) or not has(hi): bad.append((tag, "boot", lo, hi))
    # net in letter with space thousands separator
    v = float(net)
    s = f"{abs(v):,.2f}".replace(",", " ")
    if s not in letter: bad.append((tag, "net", s))
print("cells checked:", len(blocks))
print("BAD:", bad if bad else "none")

