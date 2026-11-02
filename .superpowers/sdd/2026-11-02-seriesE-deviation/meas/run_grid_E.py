"""Одноразовый runner Series E замера (R-next-1): 54 запуска = 18 клеток x 3 фолда.

Это НЕ tracked-код симулятора — только обёртка вызовов scripts/deviation/grid.py.
Читает закон: .superpowers/sdd/2026-11-02-seriesE-deviation/
seriesE-deviation-preregistration-DRAFT.md, секции E-2/E2 и E-2-F (параметры
клеток ТОЛЬКО из таблицы E-2-F).

Сделано:
- cmd-таблица 54 строк (клетка x фолд) в порядке E-a1..a6, b1..b6, c1..c6 x f1..f3;
- последовательный прогон (sqlite read-only, без параллелизма — по умолчанию);
- stdout каждого запуска + exit code -> сырой лог E5-seriesE-grid-raw.txt;
- сводный JSON meas/grid_E_runs.json для гейтов E2;
- детерминизм-контроль: E-b3 x f2 повторно в data/calib/dev_E-b3_f2_RERUN,
  побайтное сравнение всех 3 файлов с оригиналом -> True/False.

Запуск (из корня репо, venv):
  $env:PYTHONUTF8="1"
  python .superpowers/sdd/2026-11-02-seriesE-deviation/meas/run_grid_E.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PYTHON = Path.home() / "Documents" / "GitHub" / "strategylab" / ".venv" / "Scripts" / "python.exe"
GRID = REPO / "scripts" / "deviation" / "grid.py"
RAW = REPO / ".superpowers" / "sdd" / "2026-11-02-seriesE-deviation" / "E5-seriesE-grid-raw.txt"
OUT_JSON = REPO / ".superpowers" / "sdd" / "2026-11-02-seriesE-deviation" / "meas" / "grid_E_runs.json"

CTX_HOURS = 960  # все клетки

# Фолды (окна строго эти; events только внутри фолда; контекст 960ч до).
FOLDS: dict[str, tuple[int, int]] = {
    "f1": (1761523200, 1771113600),
    "f2": (1771113600, 1780704000),
    "f3": (1780704000, 1790294400),
}

# Базы фиксированы (задание): B1 = a-клетки, B2 = b-клетки, B3 = c-клетки.
BASES: dict[str, tuple[str, str, str, str, int]] = {
    # префикс: (db, symbol, tol, depth, vol_filter)
    "a": ("data/ethusdt.db", "ETHUSDT", "0.020", "0.003", 2),
    "b": ("data/ethusdt.db", "ETHUSDT", "0.020", "0.005", 1),
    "c": ("data/btcusdt.db", "BTCUSDT", "0.010", "0.005", 1),
}

# Таблица E-2-F: внутри базы индекс 1..6 = (conf, exit).
CONF_EXIT: dict[int, tuple[int, str]] = {
    1: (0, "V1_48"),
    2: (0, "V2_R2"),
    3: (30, "V1_48"),
    4: (30, "V2_R2"),
    5: (60, "V1_48"),
    6: (60, "V2_R2"),
}


def cells() -> list[str]:
    return [f"E-{p}{i}" for p in ("a", "b", "c") for i in range(1, 7)]


def cmd_for(cell: str, fold: str) -> list[str]:
    prefix, idx = cell[2], int(cell[3])
    db, symbol, tol, depth, k = BASES[prefix]
    conf, exit_mode = CONF_EXIT[idx]
    start, end = FOLDS[fold]
    return [
        str(PYTHON),
        str(GRID),
        "--db", db,
        "--symbol", symbol,
        "--start", str(start),
        "--end", str(end),
        "--cell", cell,
        "--tol", tol,
        "--depth", depth,
        "--conf", str(conf),
        "--vol-filter", str(k),
        "--exit-mode", exit_mode,
        "--ctx-hours", str(CTX_HOURS),
        "--out", f"data/calib/dev_{cell}_{fold}",
    ]


def run_one(cmd: list[str]) -> dict:
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONUTF8"] = "1"
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8")
    return {
        "cmd": " ".join(cmd),
        "exit": proc.returncode,
        "seconds": round(time.time() - t0, 3),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def fmt_block(title: str, res: dict) -> str:
    lines = [f"===== {title} =====", f"$ {res['cmd']}", f"exit={res['exit']} seconds={res['seconds']}"]
    if res["stdout"]:
        lines.append("--- stdout ---")
        lines.append(res["stdout"].rstrip("\n"))
    if res["stderr"]:
        lines.append("--- stderr ---")
        lines.append(res["stderr"].rstrip("\n"))
    return "\n".join(lines) + "\n"


def byte_compare(orig_dir: Path, rerun_dir: Path) -> dict:
    """Побайтное сравнение 3 файлов артефакта оригинала и rerun."""
    out: dict[str, bool] = {}
    orig_files = sorted(orig_dir.glob("*.csv")) + sorted(orig_dir.glob("*_report_p.txt"))
    # stem: dev_E-b3_ETHUSDT_<start>_<end> — тот же stem, та же папка-цель.
    for orig in orig_files:
        target = rerun_dir / orig.name
        if not target.exists():
            out[orig.name] = False
            continue
        out[orig.name] = orig.read_bytes() == target.read_bytes()
    return out


def main() -> int:
    RAW.parent.mkdir(parents=True, exist_ok=True)
    raw_parts: list[str] = []
    runs: list[dict] = []
    incidents: list[str] = []

    for cell in cells():
        for fold in ("f1", "f2", "f3"):
            cmd = cmd_for(cell, fold)
            res = run_one(cmd)
            entry = {"cell": cell, "fold": fold, **res}
            runs.append(entry)
            raw_parts.append(fmt_block(f"{cell} {fold}", res))
            if res["exit"] != 0:
                incidents.append(f"{cell} {fold}: exit={res['exit']}")
            print(f"{cell} {fold}: exit={res['exit']} {res['seconds']}s", flush=True)

    # Детерминизм-контроль: E-b3 x f2 rerun в отдельную папку.
    cmd_r = cmd_for("E-b3", "f2")
    cmd_r[-1] = "data/calib/dev_E-b3_f2_RERUN"
    res_r = run_one(cmd_r)
    raw_parts.append(fmt_block("E-b3 f2 RERUN (детерминизм-контроль)", res_r))
    if res_r["exit"] != 0:
        incidents.append(f"E-b3 f2 RERUN: exit={res_r['exit']}")

    orig_dir = REPO / "data" / "calib" / "dev_E-b3_f2"
    rerun_dir = REPO / "data" / "calib" / "dev_E-b3_f2_RERUN"
    cmp = byte_compare(orig_dir, rerun_dir)
    all_equal = bool(cmp) and all(cmp.values())
    raw_parts.append("===== детерминизм E-b3 f2: побайтное сравнение 3 файлов =====")
    for name, ok in cmp.items():
        raw_parts.append(f"  {name}: {ok}")
    raw_parts.append(f"ALL_EQUAL: {all_equal}\n")

    RAW.write_text("".join(raw_parts), encoding="utf-8", newline="\n")
    OUT_JSON.write_text(
        json.dumps(
            {"runs": runs, "rerun": res_r, "determinism": cmp, "determinism_all_equal": all_equal,
             "incidents": incidents},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"raw: {RAW}")
    print(f"json: {OUT_JSON}")
    print(f"determinism_all_equal: {all_equal}")
    print(f"incidents: {incidents or 'нет'}")
    return 1 if incidents else 0


if __name__ == "__main__":
    sys.exit(main())
