"""E-7: механический подсчёт H1 и H2 по csv трёх holdout-клеток. Decimal, без float.

H1: сколько из 3 клеток имеет net_pnl >= 0 (точный подсчёт).
H2: средняя годовая = (Σ net_pnl) / 3 / 10000 × 365/38 × 100, сравнение >= 3.0.
Только арифметика критериев, без интерпретаций GO/NO-GO (вердикт — за лидом).
"""
import csv
import sys
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 50
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MEAS = Path(__file__).resolve().parent
REPO = MEAS.parents[4]
CELLS = ["E-a3", "E-a5", "E-b5"]
THRESHOLD = Decimal("3.0")
CAPITAL = Decimal("10000")
DAYS = Decimal("38")


def read_net(cell: str) -> Decimal:
    d = REPO / "data" / "calib" / f"holdout_{cell}"
    csvs = [p for p in d.iterdir()
            if p.suffix == ".csv" and "_journal_" not in p.name]
    assert len(csvs) == 1, (cell, csvs)
    lines = csvs[0].read_text(encoding="utf-8").splitlines()
    idx = next(i for i, l in enumerate(lines) if l.startswith("fork,"))
    rows = list(csv.DictReader(lines[idx:]))
    assert len(rows) == 1, (cell, len(rows))
    return Decimal(rows[0]["net_pnl"])


def main() -> None:
    nets = {c: read_net(c) for c in CELLS}
    print("== net_pnl (точные Decimal из csv) ==")
    for c in CELLS:
        print(f"{c}: {nets[c]}")

    print("\n== H1: net >= 0 ==")
    ge = [c for c in CELLS if nets[c] >= 0]
    for c in CELLS:
        print(f"{c}: net >= 0 -> {nets[c] >= 0}")
    print(f"H1 count: {len(ge)} из 3 (клетки с net>=0: {ge if ge else 'нет'}); "
          f"планка H1: минимум 2 из 3")

    print("\n== H2: средняя годовая % ==")
    total = sum(nets.values(), Decimal(0))
    mean = total / Decimal(3)
    frac = mean / CAPITAL
    ann = frac * Decimal(365) / DAYS * Decimal(100)
    print(f"Σ net_pnl            = {total}")
    print(f"(Σ / 3)              = {mean}")
    print(f"((Σ / 3) / 10000)    = {frac}")
    print(f"× 365/38 × 100       = {ann}")
    print(f"H2: {ann} >= {THRESHOLD} -> {ann >= THRESHOLD}")


if __name__ == "__main__":
    main()
