"""Гейты E2-1…E2-5 по АРТЕФАКТАМ решётки Series E (18 клеток × 3 фолда).

Закон: .superpowers/sdd/2026-11-02-seriesE-deviation/
seriesE-deviation-preregistration-DRAFT.md, §E-2 (гейты) и §E-2-F (замороженная
таблица — параметры клеток берутся ТОЛЬКО оттуда; здесь — только чтение
артефактов data/calib/dev_<cell>_f<N>/).

Источники чисел (Decimal-строки из csv; float — только bootstrap, так и разрешено):
- <stem>.csv            — метаблок + header + строка fork=pessimistic;
- <stem>_journal_p.csv  — cash-flow: круг = пара entry→exit, net круга =
                          сумма cash_delta обоих строк, день = exit_ts;
- <stem>_report_p.txt   — сводка (rounds, stops/takes/tb/forced и пр.).

Гейты (дословно §E-2):
- E2-1: net ≥ 0$ в каждом из трёх фолдов (Decimal);
- E2-2: pooled_ann = (сумма net трёх фолдов / 10000) × (365/333) × 100 ≥ +3.0%/год;
- E2-3: каждый фолд > 0 годовой (annualized_pct из csv, строго > 0);
- E2-4: moving-block bootstrap, блоки 30d, 1000 повторов, seed=42, circular wrap,
        старт позиции uniform 0..322, статистика = mean(daily)×365/10000×100,
        нижняя граница = 2.5-й процентиль (линейная интерполяция по 1000
        отсортированных повторов), PASS если > 0;
- E2-5: rounds ≤ 25 в каждом фолде.

Запуск: $env:PYTHONUTF8="1"; python meas/gates_E2.py  (из корня репо)
Выход: meas/grid_E_gates.json + печать таблицы.
"""

from __future__ import annotations

import csv
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CALIB = REPO / "data" / "calib"
OUT_JSON = Path(__file__).resolve().parent / "grid_E_gates.json"

FOLDS = ("f1", "f2", "f3")
FOLD_WINDOW = {
    "f1": (1761523200, 1771113600),
    "f2": (1771113600, 1780704000),
    "f3": (1780704000, 1790294400),
}
TOTAL_DAYS = 333  # суммарная длина трёх фолдов в днях (111+111+111)
DAY0 = 1761523200
DAY_SEC = 86400
CAPITAL = Decimal("10000")
POOL_BAR = Decimal("3.0")
ROUNDS_CAP = 25

BOOT_REPS = 1000
BOOT_SEED = 42
BOOT_BLOCK = 30
BOOT_START_MAX = 322  # inclusive: uniform 0..322


def cells() -> list[str]:
    return [f"E-{p}{i}" for p in ("a", "b", "c") for i in range(1, 7)]


def read_metrics(cell: str, fold: str) -> dict:
    folder = CALIB / f"dev_{cell}_{fold}"
    csvs = sorted(folder.glob("*.csv"))
    csvs = [p for p in csvs if not p.name.endswith("_journal_p.csv")]
    if len(csvs) != 1:
        raise SystemExit(f"ожидался ровно 1 metrics-csv в {folder}, найдено {len(csvs)}")
    with open(csvs[0], encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "fork")
    header = rows[header_idx]
    values = rows[header_idx + 1]
    if len(values) != len(header):
        raise SystemExit(f"длина строки не совпадает с header в {csvs[0]}")
    return dict(zip(header, values))


def read_round_cashflows(cell: str, fold: str) -> list[tuple[int, Decimal]]:
    """Круги фолда: [(exit_ts, net Decimal)]. Круг = пара entry→exit в journal."""
    folder = CALIB / f"dev_{cell}_{fold}"
    journals = sorted(folder.glob("*_journal_p.csv"))
    if len(journals) != 1:
        raise SystemExit(f"ожидался ровно 1 journal в {folder}, найдено {len(journals)}")
    rounds: list[tuple[int, Decimal]] = []
    acc = Decimal(0)
    open_round = False
    with open(journals[0], encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            cash = Decimal(row["cash_delta"])
            if row["kind"] == "entry":
                if open_round:
                    raise SystemExit(f"{journals[0]}: entry внутри незакрытого круга")
                acc = cash
                open_round = True
            elif row["kind"] == "exit":
                if not open_round:
                    raise SystemExit(f"{journals[0]}: exit без entry")
                rounds.append((int(row["ts"]), acc + cash))
                acc = Decimal(0)
                open_round = False
            else:
                raise SystemExit(f"{journals[0]}: неизвестный kind {row['kind']!r}")
    if open_round:
        raise SystemExit(f"{journals[0]}: незакрытый круг на конце journal")
    return rounds


def daily_series(cell: str) -> list[float]:
    """Дневной ряд net по фолдам f1+f2+f3: каждый круг в день exit_ts,
    дни без выходов = 0; всего 333 дня (DAY0 .. DAY0+333·86400)."""
    daily = [0.0] * TOTAL_DAYS
    for fold in FOLDS:
        for ts, net in read_round_cashflows(cell, fold):
            day = (ts - DAY0) // DAY_SEC
            if not (0 <= day < TOTAL_DAYS):
                raise SystemExit(f"{cell} {fold}: exit_ts {ts} вне ряда 333 дней (day={day})")
            daily[day] += float(net)
    return daily


def bootstrap_lower_95(daily: list[float]) -> tuple[float, float, float]:
    """Moving-block bootstrap (см. docstring): возвращает (нижняя, верхняя, mean)."""
    rng = random.Random(BOOT_SEED)
    n = len(daily)
    stats: list[float] = []
    for _ in range(BOOT_REPS):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(0, BOOT_START_MAX + 1)  # uniform 0..322
            for i in range(BOOT_BLOCK):
                if len(sample) >= n:
                    break
                sample.append(daily[(start + i) % n])  # circular wrap
        mean = sum(sample) / n
        stats.append(mean * 365 / float(CAPITAL) * 100)
    stats.sort()

    def pct(p: float) -> float:
        k = p * (len(stats) - 1)
        lo = int(k)
        frac = k - lo
        hi = min(lo + 1, len(stats) - 1)
        return stats[lo] + frac * (stats[hi] - stats[lo])

    return pct(0.025), pct(0.975), sum(stats) / len(stats)


def pooled_ann(nets: list[Decimal]) -> Decimal:
    """(сумма net трёх фолдов / 10000) × (365/333) × 100."""
    return sum(nets, Decimal(0)) / CAPITAL * Decimal(365) / Decimal(TOTAL_DAYS) * Decimal(100)


def main() -> int:
    out: dict[str, dict] = {}
    for cell in cells():
        folds: dict[str, dict] = {}
        for fold in FOLDS:
            m = read_metrics(cell, fold)
            folds[fold] = {
                "net_pnl": m["net_pnl"],
                "annualized_pct": m["annualized_pct"],
                "rounds": int(m["rounds"]),
                "events": int(m["events"]),
                "flat_fold": int(m["flat_fold"]),
                "n_stops": int(m["n_stops"]),
                "n_takes": int(m["n_takes"]),
                "n_tb": int(m["n_tb"]),
                "n_forced": int(m["n_forced"]),
                "deviation_version": m["deviation_version"],
                "zones": int(m["zones"]),
                "price_gross": m["price_gross"],
                "friction": m["friction"],
            }
        nets = [Decimal(folds[f]["net_pnl"]) for f in FOLDS]
        anns = [Decimal(folds[f]["annualized_pct"]) for f in FOLDS]
        rounds = [folds[f]["rounds"] for f in FOLDS]

        e2_1 = all(n >= 0 for n in nets)
        pool = pooled_ann(nets)
        e2_2 = pool >= POOL_BAR
        e2_3 = all(a > 0 for a in anns)
        daily = daily_series(cell)
        boot_lo, boot_hi, boot_mean = bootstrap_lower_95(daily)
        e2_4 = boot_lo > 0.0
        e2_5 = all(r <= ROUNDS_CAP for r in rounds)

        out[cell] = {
            "folds": folds,
            "pooled_net": str(sum(nets, Decimal(0))),
            "pooled_ann": str(pool),
            "daily_sum_check": str(sum(daily)),
            "bootstrap": {
                "reps": BOOT_REPS, "seed": BOOT_SEED, "block_days": BOOT_BLOCK,
                "start_uniform": "0..322 inclusive (randrange(323))", "wrap": "circular",
                "statistic": "mean(daily)*365/10000*100",
                "lower_2.5pct": boot_lo, "upper_97.5pct": boot_hi, "mean_replicates": boot_mean,
            },
            "gates": {
                "E2-1": e2_1, "E2-2": e2_2, "E2-3": e2_3, "E2-4": e2_4, "E2-5": e2_5,
                "ALL": e2_1 and e2_2 and e2_3 and e2_4 and e2_5,
            },
        }
        print(
            f"{cell}: pooled_net={out[cell]['pooled_net']} pooled_ann={pool:.6f} "
            f"lo95={boot_lo:.4f} gates="
            + ",".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in out[cell]["gates"].items()),
            flush=True,
        )

    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"json: {OUT_JSON}")
    passed = sum(1 for c in out.values() if c["gates"]["ALL"])
    print(f"cells passing E2-1..E2-5: {passed}/18")
    return 0


if __name__ == "__main__":
    sys.exit(main())
