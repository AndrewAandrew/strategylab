# -*- coding: utf-8 -*-
"""H-0 feeder: ежедневная подкормка форвард-окна [1793577600, 2027-01-01) + контроль непрерывности.

Один вход в день: funding BTC/ETH, 1m-бары BTC/ETH (последние ~14 дней), 1m-бары алтов
SOXL/SOL/ZEC (последние ~2 суток), затем проверка bars_1m на дыры >24ч внутри окна и
свежести хвоста. Идемпотентно (INSERT OR IGNORE/REPLACE). Логи в data/feeder/feeder_log.txt.

Коды выхода: 0 = всё хорошо; 1 = упал синк/fetch; 2 = синк ок, но найдена дыра >24ч
или хвост старее 26ч (по замороженному H0-freeze-criteria дыры >24ч недопустимы).

Запуск по плану: daily (Task Scheduler «strategylab-h0-feeder») или руками:
    .venv\\Scripts\\python.exe -X utf8 scripts\\h0_feeder.py
"""
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("C:/Users/egoro/Documents/GitHub/strategylab")
PY = REPO / ".venv" / "Scripts" / "python.exe"
LOG = REPO / "data" / "feeder" / "feeder_log.txt"

WINDOW_START = 1793577600          # 2026-11-02 00:00 UTC — старт окна H-0
WINDOW_END = 1798761600            # 2027-01-01 00:00 UTC = START+60d — закрытие окна (запуск после)
MAX_GAP = 86400                    # дыра >24ч невалидна по freeze-закону
STALE_AFTER = 26 * 3600            # хвостBars должен быть не старше ~суток + запас
START_FUNDS = 1792886400           # 2026-10-25, перекрытие расчёток (как в P1)
START_BARS = 1792972800            # 2026-10-26, ~14 дней запаса
ALTS_BACK = 2 * 86400              # хвост алтов: последние 2 суток


def run(args, label, lines=3):
    p = subprocess.run([str(PY), "-X", "utf8", *args], cwd=REPO,
                       capture_output=True, text=True)
    tail = " | ".join((p.stdout + p.stderr).strip().splitlines()[-lines:])
    return p.returncode, f"{label}: rc={p.returncode} | {tail}"


def check_db(db_path, now):
    """Возвращает список проблем для bars_1m внутри окна (пустой = ок)."""
    problems = []
    c = sqlite3.connect(REPO / "data" / db_path)
    rows = c.execute(
        "select ts from bars_1m where ts >= ? order by ts", (WINDOW_START,)
    ).fetchall()
    if not rows:
        return [f"{db_path}: в окне нет ни одного бара"]
    prev = None
    for (ts,) in rows:
        if prev is not None and ts - prev > MAX_GAP:
            problems.append(
                f"{db_path}: дыра {ts - prev}s между {iso(prev)} и {iso(ts)}")
        prev = ts
    tail_age = now - prev
    if tail_age > STALE_AFTER:
        problems.append(f"{db_path}: хвост старый — {iso(prev)} (возраст {tail_age}s)")
    c.close()
    return problems


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def main():
    now = int(time.time())
    report, failed, broken = [], 0, 0

    rc, line = run(["scripts/funding/fetch_funding.py", "--start", str(START_FUNDS)],
                   "funding BTC/ETH")
    report.append(line)
    failed += rc != 0

    for sym, db in (("BTCUSDT", "btcusdt.db"), ("ETHUSDT", "ethusdt.db")):
        rc, line = run(["-m", "strategylab.cli", "sync", f"data/{db}", sym,
                        str(START_BARS), str(now)], f"bars {sym}")
        report.append(line)
        failed += rc != 0

    for sym, db in (("SOXLUSDT", "soxlusdt.db"), ("SOLUSDT", "solusdt.db"),
                    ("ZECUSDT", "zecusdt.db")):
        rc, line = run(["-m", "strategylab.cli", "sync", f"data/{db}", sym,
                        str(now - ALTS_BACK), str(now)], f"bars {sym}", lines=1)
        report.append(line)
        failed += rc != 0

    # непрерывность проверяем только если синки прошли (иначе ложные дыры)
    if not failed:
        for db in ("ethusdt.db", "btcusdt.db"):
            for prob in check_db(db, now):
                report.append(f"GAP-ALERT {prob}")
                broken += 1

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = "FAIL-SYNC" if failed else ("GAP!" if broken else "OK")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{stamp} {status}\n" + "".join(f"    {r}\n" for r in report))
    print(f"{stamp} H0-FEEDER {status}")
    for r in report:
        print("   ", r)
    sys.exit(1 if failed else (2 if broken else 0))


if __name__ == "__main__":
    main()
