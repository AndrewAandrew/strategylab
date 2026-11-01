import csv, pathlib
from decimal import Decimal
calib = pathlib.Path("data/calib")
bad, tot = [], 0
for d in sorted(calib.glob("r5_*")):
    tot += 1
    rep = next(d.glob("*_report_p.txt")); jp = next(d.glob("*_journal_p.csv"))
    fields = {}
    for line in rep.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" in line and not line.startswith("#"):
            k, _, v = line.partition(":"); fields[k.strip()] = v.strip()
    with jp.open(encoding="utf-8", newline="") as fh:
        pnls = [Decimal(r["pnl"]) for r in csv.DictReader(fh)]
    n, net, wins = len(pnls), sum(pnls, Decimal(0)), sum(1 for p in pnls if p > 0)
    errs = []
    if n != int(fields["trades"]): errs.append("n")
    if abs(net - Decimal(fields["net_pnl"])) > Decimal("0.01"): errs.append("net")
    if wins != int(fields["wins"]): errs.append("wins")
    if abs(net + Decimal("10000") - Decimal(fields["equity"])) > Decimal("0.01"): errs.append("eq=10k+net")
    if errs: bad.append((d.name, errs))
print("runs:", tot, "mismatches:", len(bad), bad[:5])
