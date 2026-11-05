# -*- coding: utf-8 -*-
"""V-7 one-shot arms-драйвер (закон: V2-SIM-AMENDMENT-FROZEN.md §5, FROZEN 2026-11-05).

ВОРУЖАЕТСЯ ТОЛЬКО если gates_V5.py дал >=1 живую клетку на SOLUSDT. Иначе не запускать.

Делает ровно: по одному вооружённому запуску scripts/deviation/grid.py на каждую
живую SOL-клетку (артефакты dev_v7_<cell>_SOLUSDT), окно [1790294400, 1791849600),
ctx-hours 960 (перенос frozen-зон, прецедент E-7), --holdout-ok + --holdout-ceiling
1791849600; плюс ОДИН детерминизм-контроль (повтор первой живой клетки в dev_v7ctl_*).
Каждый запуск — через run_once-лог V7_<cell>.log (сырые байты stdout). Логи проверяются
на «HOLDOUT_ARMED ровно 1, повторных прогонов нет».

Запуск (из корня репо):
  $env:PYTHONUTF8="1"
  python .superpowers/sdd/2026-11-03-seriesV-altcoins/meas/holdout/run_v7.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
SER = REPO / ".superpowers" / "sdd" / "2026-11-03-seriesV-altcoins"
sys.path.insert(0, str(SER / "meas"))
from gates_V5 import cell_verdict  # noqa: E402  — та же Decimal-рука, что и гейты

PYTHON = sys.executable
GRID = str(Path("scripts") / "deviation" / "grid.py")
START, END, CTX = 1790294400, 1791849600, 960
CTL = dict(tol="0.010", depth="0.005", conf="0", vol="1", tf="14400", tch="2")
CELLS = {  # cell -> (regime, exit) — §3
    "V-a1": ("а", "V1_24"), "V-a2": ("а", "V1_48"), "V-a3": ("а", "V2_R1"),
    "V-b1": ("в", "V1_24"), "V-b2": ("в", "V1_48"), "V-b3": ("в", "V2_R1"),
    "V-c1": ("г", "V1_24"), "V-c2": ("г", "V1_48"), "V-c3": ("г", "V2_R1"),
}


def grid_cmd(cell: str, reg: str, ex: str, out: str) -> list[str]:
    return [GRID, "--db", "data/solusdt.db", "--symbol", "SOLUSDT",
            "--start", str(START), "--end", str(END),
            "--cell", f"v7_{cell}", "--tol", CTL["tol"], "--depth", CTL["depth"],
            "--conf", CTL["conf"], "--vol-filter", CTL["vol"], "--regime", reg,
            "--exit-mode", ex, "--zone-tf-secs", CTL["tf"], "--touches", CTL["tch"],
            "--ctx-hours", str(CTX), "--holdout-ok", "--holdout-ceiling", str(END),
            "--out", out]


def main() -> int:
    live = []
    for cell in CELLS:
        r = cell_verdict(cell, "SOLUSDT")
        if r and r["live"]:
            live.append(cell)
    if not live:
        print("V-7 НЕ вооружается: живых SOL-клеток по §4 нет (см. gates_V5).")
        return 3
    logs_dir = SER / "meas" / "holdout"
    logs_dir.mkdir(parents=True, exist_ok=True)
    armed = [p for p in logs_dir.glob("V7_*.log")]
    if armed:
        print(f"STOP: логи вооружённых прогонов уже существуют ({len(armed)}) — one-shot "
              "израсходован или прерван; сверься с лидом, не повторяй.")
        return 4
    print(f"# V-7 arms: живые SOL-клетки {live}; ровно {len(live)} прогонов + 1 контроль")
    results = []
    for i, cell in enumerate(live):
        reg, ex = CELLS[cell]
        log = logs_dir / f"V7_{cell}.log"
        cmd = grid_cmd(cell, reg, ex, f"data/calib/dev_v7_{cell}_SOLUSDT")
        t0 = time.perf_counter()
        p = subprocess.run([PYTHON, "-X", "utf8", *cmd], cwd=REPO, capture_output=True)
        secs = time.perf_counter() - t0
        header = (f"$ .venv/Scripts/python.exe -X utf8 " + " ".join(cmd) + "\n"
                  f"cwd: {REPO}\nexit={p.returncode} seconds={secs:.3f}\n"
                  "--- stdout (raw) ---\n").encode("utf-8")
        body = p.stdout + (b"\n--- stderr (raw) ---\n" + p.stderr if p.stderr else b"")
        log.write_bytes(header + body)
        armed_count = body.count(b"HOLDOUT_ARMED")
        print(f"[{i + 1}/{len(live)}] {cell}: exit={p.returncode} HOLDOUT_ARMED={armed_count}")
        results.append((cell, p.returncode, armed_count))
        if p.returncode != 0 or armed_count != 1:
            print("STOP: прогон нечист — further arms halted, зови лида")
            return 5
    ctl_cell = live[0]
    reg, ex = CELLS[ctl_cell]
    log = logs_dir / f"V7_CTL_{ctl_cell}.log"
    cmd = grid_cmd(ctl_cell, reg, ex, f"data/calib/dev_v7ctl_{ctl_cell}_SOLUSDT")
    cmd[cmd.index("--cell") + 1] = f"v7ctl_{ctl_cell}"
    p = subprocess.run([PYTHON, "-X", "utf8", *cmd], cwd=REPO, capture_output=True)
    header = (f"$ CTL .venv/Scripts/python.exe -X utf8 " + " ".join(cmd) + "\n"
              f"exit={p.returncode}\n--- stdout (raw) ---\n").encode("utf-8")
    log.write_bytes(header + p.stdout + (b"\n--- stderr (raw) ---\n" + p.stderr if p.stderr else b""))
    a = REPO / "data/calib" / f"dev_v7_{ctl_cell}_SOLUSDT" / f"dev_v7_{ctl_cell}_SOLUSDT_SOLUSDT_{START}_{END}"
    b = REPO / "data/calib" / f"dev_v7ctl_{ctl_cell}_SOLUSDT" / f"dev_v7ctl_{ctl_cell}_SOLUSDT_SOLUSDT_{START}_{END}"
    same = all(
        (a.parent / (a.name + s)).read_bytes() == (b.parent / (b.name + s)).read_bytes()
        for s in (".csv", "_journal_p.csv", "_report_p.txt"))
    print(f"[CTL] повтор {ctl_cell}: побайтно={same}")
    if not same:
        print("STOP: НЕДЕТЕРМИНИРОВАННО — артефакты arms-прогонов заморожены, разбирайся с лидом")
        return 6
    print("V-7 arms-прогнаны. Числа публикует брат (R-next-1); вердикт H1/H2 — лид.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
