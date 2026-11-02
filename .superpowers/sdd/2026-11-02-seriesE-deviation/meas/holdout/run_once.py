"""Однократный вооружённый прогон E-7 (одна клетка на запуск).

Usage: run_once.py <log_path> -- <argv...>
Исполняет ровно ОДИН прогон scripts/deviation/grid.py с argv, данным после "--",
cwd = текущая директория (репо). Пишет в <log_path> полный сырой stdout (bytes,
без перекодировки) плюс служебный заголовок в формате прецедента E5-raw.
Возвращает exit-код прогона; ничего в параметрах не меняет (E-7 one-shot).
"""
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    sep = sys.argv.index("--")
    log_path = Path(sys.argv[1])
    cmd = list(sys.argv[sep + 1:])
    if not cmd:
        print("runner: пустой argv прогона", file=sys.stderr)
        return 64
    repo = Path.cwd()
    if not (repo / "scripts" / "deviation" / "grid.py").is_file():
        print(f"runner: {repo} не похож на репо (нет scripts/deviation/grid.py)",
              file=sys.stderr)
        return 64
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    argv = [sys.executable] + cmd
    t0 = time.perf_counter()
    proc = subprocess.run(argv, cwd=repo, env=env, capture_output=True)
    secs = time.perf_counter() - t0

    header = (
        "$ .venv/Scripts/python.exe " + " ".join(cmd) + "\n"
        + f"cwd: {repo}\n"
        + f"exit={proc.returncode} seconds={secs:.3f}\n"
        + "--- stdout (raw) ---\n"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as stream:
        stream.write(header.encode("utf-8"))
        stream.write(proc.stdout)
        if proc.stderr:
            stream.write(b"\n--- stderr (raw) ---\n")
            stream.write(proc.stderr)

    out = sys.stdout
    out.write(header)
    out.write(proc.stdout.decode("utf-8", "replace"))
    if proc.stderr:
        out.write("\n--- stderr ---\n")
        out.write(proc.stderr.decode("utf-8", "replace"))
    out.write(f"[runner] log={log_path} exit={proc.returncode}\n")
    out.flush()
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
