"""E-7: извлечение точных чисел из csv 3 клеток + побайтный контроль rerun.

Печатает:
 1) по каждой клетке все поля метрик из csv (точные Decimal-строки, без округления);
 2) наличие/состав артефактов (ровно 3 файла) в каждой папке;
 3) sha256 3 файлов holdout_E-a3 vs holdout_E-a3-rerun и вердикт byte-identical.
Без float, без интерпретаций.
"""
import csv
import hashlib
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MEAS = Path(__file__).resolve().parent
# holdout -> meas -> 2026-11-02-seriesE-deviation -> sdd -> .superpowers -> репо
REPO = MEAS.parents[4]

FIELDS = [
    "deviation_version", "symbol", "cell", "start_ts", "end_ts", "ctx_hours",
    "zone_tf_secs", "touches", "tol", "depth", "conf_min", "vol_filter",
    "exit_mode", "capital", "taker_fee", "slippage", "zones", "events",
    "rounds", "n_stops", "n_takes", "n_tb", "n_forced", "price_gross",
    "friction", "net_pnl", "equity", "annualized_pct", "flat_fold",
]
CELLS = ["E-a3", "E-a5", "E-b5"]


def read_metrics(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, l in enumerate(lines) if l.startswith("fork,"))
    rows = list(csv.DictReader(lines[idx:]))
    if len(rows) != 1:
        sys.exit(f"ожидалась 1 строка данных в {path}, найдено {len(rows)}")
    return rows[0]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    print("== Артефакты и точные числа из csv ==")
    for cell in CELLS:
        d = REPO / "data" / "calib" / f"holdout_{cell}"
        files = sorted(p.name for p in d.iterdir() if p.is_file())
        print(f"\n[{cell}] dir={d.relative_to(REPO)} files({len(files)}): {files}")
        if len(files) != 3:
            sys.exit(f"{cell}: ожидалось ровно 3 файла, найдено {len(files)}")
        csvs = [p for p in d.iterdir() if p.suffix == ".csv" and "_journal_" not in p.name]
        if len(csvs) != 1:
            sys.exit(f"{cell}: не найден однозначный metrics-csv")
        row = read_metrics(csvs[0])
        for f in FIELDS:
            print(f"  {f}: {row[f]}")

    print("\n== Побайтный контроль: holdout_E-a3 vs holdout_E-a3-rerun ==")
    d1 = REPO / "data" / "calib" / "holdout_E-a3"
    d2 = REPO / "data" / "calib" / "holdout_E-a3-rerun"
    names = sorted(p.name for p in d1.iterdir() if p.is_file())
    if sorted(p.name for p in d2.iterdir() if p.is_file()) != names:
        sys.exit("составы файлов оригинала и rerun различаются")
    all_eq = True
    print("| file | sha256 original | sha256 rerun | equal |")
    print("|------|-----------------|--------------|:-----:|")
    for name in names:
        h1, h2 = sha256(d1 / name), sha256(d2 / name)
        eq = h1 == h2
        all_eq = all_eq and eq
        print(f"| `{name}` | `{h1}` | `{h2}` | {eq} |")
    print(f"\nBYTE_IDENTICAL: {all_eq}")
    if not all_eq:
        sys.exit(1)


if __name__ == "__main__":
    main()
