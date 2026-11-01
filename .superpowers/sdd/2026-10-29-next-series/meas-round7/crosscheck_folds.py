# -*- coding: utf-8 -*-
import re, io
out = io.open("C:/Users/egoro/AppData/Local/Temp/opencode/meas-r7/csvonly_out.txt", encoding="utf-8").read()
letter = io.open("C:/Users/egoro/Documents/GitHub/strategylab/.superpowers/letters/2026-11-02-round7-setka-brata.md",
                 encoding="utf-8").read().replace(chr(0x2212), "-")


def sp(x):
    return f"{abs(float(x)):,.2f}".replace(",", " ")


bad = []
nfold = 0
nb = 0
blocks = re.split(r"### ", out)[1:]
for b in blocks:
    tag = b.splitlines()[0].strip()
    pat = (r"(f\d): n=(\d+) \(AMBIG[^|]*\| WR_A=([\d.]+)% \(WR journal ([\d.]+)\) \| "
           r"A mean=(-?[\d.]+)%[^|]*(?:\| B mean=(-?[\d.]+)%)?[^|]*\| "
           r"net=(-?[\d.]+)\$ \| MDD=(-?[\d.]+)\$ \(([\d.]+)%\) \| end eq=(-?[\d.]+)")
    for m in re.finditer(pat, b):
        nfold += 1
        fk, n, wr, wrj, mean, bmean, net, mdd, mddp, eq = m.groups()
        if bmean:
            nb += 1
            if f"{float(bmean):.4f}".replace("-", "") and sp(net):
                pass
            v = f"{float(bmean):+.4f}"
            if v not in letter and f"{float(bmean):.4f}" not in letter:
                bad.append((tag, fk, "B", v))
        for label, val in (("n", n), ("wr", f"{float(wr):.2f}%"),
                           ("mean", f"{float(mean):.4f}"),
                           ("net", sp(net)), ("mdd", sp(mdd)), ("eq", sp(eq))):
            if val not in letter:
                bad.append((tag, fk, label, val))
print("fold rows checked:", nfold, "with B:", nb)
print("BAD:", bad if bad else "none")
