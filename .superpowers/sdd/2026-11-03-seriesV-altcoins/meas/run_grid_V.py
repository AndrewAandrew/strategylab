# -*- coding: utf-8 -*-
"""RunnerSeries V-5 (заморожен V2-SIM-AMENDMENT-FROZEN.md §3): 27 прогонов = 9 клеток x 3 рынка.

Это НЕ tracked-код симулятора — обёртка вызовов scripts/deviation/grid.py (прецедент
run_grid_E.py). Закону §3: окно пула ровно [1774742400, 1790294400), ctx-hours 0,
tol 0.010, depth 0.005, conf 0, vol-filter 1, zone-tf 14400, touches 2, дефолты
капитала/fees CLI; оси — regime {а,в,г} x exit {V1_24, V1_48, V2_R1}; рынки
SOXLUSDT/SOLUSDT/ZECUSDT. Никаких иных параметров.

Детерминизм-контроль: V-b2_SOLUSDT повторяется во второй выход — побайтное сравнение
всех трёх артефактов.

Запуск (из корня репо, venv):
  $env:PYTHONUTF8="1"
  python .superpowers/sdd/2026-11-03-seriesV-altcoins/meas/run_grid_V.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
GRID = REPO / "scripts" / "deviation" / "grid.py"
SER = REPO / ".superpowers" / "sdd" / "2026-11-03-seriesV-altcoins"
RAW = SER / "V5-grid-raw.txt"
OUT_JSON = SER / "meas" / "grid_V5_runs.json"

START, END = 1774742400, 1790294400  # окно пула V-0, ctx=0 (§2 amendment)

MARKETS = (("SOXLUSDT", "data/soxlusdt.db"),
           ("SOLUSDT", "data/solusdt.db"),
           ("ZECUSDT", "data/zecusdt.db"))

CELLS = [  # (cell, regime, exit_mode) — таблица §3 дословно
    ("V-a1", "а", "V1_24"), ("V-a2", "а", "V1_48"), ("V-a3", "а", "V2_R1"),
    ("V-b1", "в", "V1_24"), ("V-b2", "в", "V1_48"), ("V-b3", "в", "V2_R1"),
    ("V-c1", "г", "V1_24"), ("V-c2", "г", "V1_48"), ("V-c3", "г", "V2_R1"),
]

CTL = dict(tol="0.010", depth="0.005", conf="0", vol="1", tf="14400", tch="2", ctx="0")


def cmd_for(cell: str, sym: str, db: str, regime: str, exit_mode: str, out: Path) -> list[str]:
    return [
        str(PYTHON), str(GRID),
        "--db", db, "--symbol", sym,
        "--start", str(START), "--end", str(END),
        "--cell", f"{cell}_{sym}",
        "--tol", CTL["tol"], "--depth", CTL["depth"], "--conf", CTL["conf"],
        "--vol-filter", CTL["vol"], "--regime", regime, "--exit-mode", exit_mode,
        "--zone-tf-secs", CTL["tf"], "--touches", CTL["tch"], "--ctx-hours", CTL["ctx"],
        "--out", str(out),
    ]


def parse_metrics(stdout: str) -> dict:
    kv: dict[str, str] = {}
    for line in stdout.splitlines():
        if ": " in line:
            k, _, v = line.partition(": ")
            kv[k.strip()] = v.strip()
    return kv


def main() -> int:
    t0 = time.time()
    lines: list[str] = [
        f"# V-5 grid runner | закон: V2-SIM-AMENDMENT-FROZEN.md §3 (FROZEN 2026-11-05 до чисел)",
        f"# окно [{START}, {END}) ctx=0 tol={CTL['tol']} d={CTL['depth']} conf={CTL['conf']} "
        f"k={CTL['vol']} tf={CTL['tf']}s tch={CTL['tch']} | REPO HEAD {subprocess.run(['git','rev-parse','HEAD'],cwd=REPO,capture_output=True,text=True).stdout.strip()}",
        f"# запуск {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    runs: dict[str, dict] = {}
    fail = 0
    plan = [(cell, sym, db, reg, ex) for sym, db in MARKETS for cell, reg, ex in CELLS]
    for i, (cell, sym, db, reg, ex) in enumerate(plan, 1):
        out = REPO / "data" / "calib" / f"dev_{cell}_{sym}"
        argv = cmd_for(cell, sym, db, reg, ex, out)
        p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8")
        lines.append(f"[{i:02d}/27] $ " + " ".join(a.replace(str(REPO) + "\\", "") for a in argv[1:]))
        lines.append((p.stdout + p.stderr).rstrip())
        lines.append(f"[{i:02d}/27] exit={p.returncode}")
        if p.returncode != 0:
            fail += 1
        kv = parse_metrics(p.stdout)
        runs[f"{cell}_{sym}"] = {
            "cell": cell, "symbol": sym, "regime": reg, "exit": ex,
            "rc": p.returncode, "version": kv.get("deviation_version", ""),
            "net": kv.get("net_pnl", ""), "ann": kv.get("annualized_pct", ""),
            "rounds": kv.get("rounds", ""), "events": kv.get("events", ""),
            "out": str(out.relative_to(REPO)),
        }
        print(f"[{i:02d}/27] {cell}_{sym} rc={p.returncode} net={kv.get('net_pnl','?')}", flush=True)

    # детерминизм-контроль: V-b2 x SOLUSDT повтор в dev_ctl_V-b2_SOLUSDT
    ctl_cell, ctl_sym = "V-b2", "SOLUSDT"
    ctl_reg, ctl_ex = "в", "V1_48"
    a_dir = REPO / "data" / "calib" / f"dev_{ctl_cell}_{ctl_sym}"
    b_dir = REPO / "data" / "calib" / f"dev_ctl_{ctl_cell}_{ctl_sym}"
    argv = cmd_for(ctl_cell, ctl_sym, "data/solusdt.db", ctl_reg, ctl_ex, b_dir)
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    lines.append(f"[CTL] $ " + " ".join(a.replace(str(REPO) + "\\", "") for a in argv[1:]))
    lines.append((p.stdout + p.stderr).rstrip())
    identical = True
    stem = f"dev_{ctl_cell}_{ctl_sym}_{ctl_sym}_{START}_{END}"  # CLI: dev_<--cell>_<symbol>_; cell уже с sym
    for suffix in (".csv", "_journal_p.csv", "_report_p.txt"):
        fa, fb = a_dir / (stem + suffix), b_dir / (stem + suffix)
        same = fa.read_bytes() == fb.read_bytes()
        identical = identical and same
        lines.append(f"[CTL] побайтно {suffix}: {same}")
    lines.append(f"[CTL] ДЕТЕРМИНИЗМ: {'YES' if identical and p.returncode == 0 else 'NO'}")
    runs["CTL_V-b2_SOLUSDT"] = {"byte_identical_x2": identical, "rc": p.returncode}

    elapsed = time.time() - t0
    lines.append(f"# ИТОГ: 27 прогонов, fail={fail}, детерминизм={identical}, {elapsed:.0f}s")
    RAW.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    OUT_JSON.write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"RAW: {RAW}\nfail={fail} det={identical} elapsed={elapsed:.0f}s")
    return 1 if (fail or not identical) else 0


if __name__ == "__main__":
    sys.exit(main())
