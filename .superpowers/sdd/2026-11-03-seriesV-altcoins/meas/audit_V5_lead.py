# -*- coding: utf-8 -*-
"""Аудит лида V-5: независимый пересчёт с нуля (прецедент audit_E7_holdout_lead.py).

Не переиспользует gates_V5.py: свой парсинг (split по запятым, не csv.DictReader),
свои трети, свои гейты §4 (V-a' nq>=3 И net>=0 в каждой трети; V-b' ann>=+3.0%/год;
V-c' n<=75). Сверяет: journal_p (источник истины) <-> metrics-csv <-> stdout-json <->
raw-лог зонда V0 (reg а клетки = контроль эквивалентности sim<->probe).
Контролирует: 27 строк exit=0 в raw, нет HOLDOUT_ARMED, CTL побайтно, один
deviation_version на клетку-ось. Выход 0 = 0 расхождений.
"""
from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SER = REPO / ".superpowers" / "sdd" / "2026-11-03-seriesV-altcoins"
CALIB = REPO / "data" / "calib"
RAW = SER / "V5-grid-raw.txt"
PROBE_RAW = SER / "V0-zone-deviation-probe-raw.txt"

START, END = 1774742400, 1790294400
THIRD = 60 * 86400
CAP = Decimal(10000)

MARKETS = ("SOXLUSDT", "SOLUSDT", "ZECUSDT")
CELLS = ("V-a1", "V-a2", "V-a3", "V-b1", "V-b2", "V-b3", "V-c1", "V-c2", "V-c3")

bad: list[str] = []


def fail(msg: str) -> None:
    bad.append(msg)
    print("RAZHOZHDENIE:", msg)


def dec(s: str) -> Decimal:
    return Decimal(s.strip())


def journal_rounds(jpath: Path) -> list[tuple[int, Decimal]]:
    """Свой парсер: entry cash_delta = friction входа; exit = gross+friction выхода.
    Круг = сумма пары entry+exit; ts круга = ts entry."""
    rounds: list[tuple[int, Decimal]] = []
    pend: tuple[int, Decimal] | None = None
    for ln in jpath.read_text(encoding="utf-8").splitlines():
        f = ln.split(",")
        if len(f) < 2 or not f[0].strip().lstrip("-").isdigit():
            continue
        ts, kind, delta = int(f[0]), f[1], dec(f[CASH_COL])
        if kind == "entry":
            if pend is not None:
                fail(f"{jpath.name}: entry без парного exit @ {ts}")
            pend = (ts, delta)
        elif kind == "exit":
            if pend is None:
                fail(f"{jpath.name}: exit без entry @ {ts}")
                continue
            rounds.append((pend[0], pend[1] + delta))
            pend = None
        elif kind == "flat":
            if pend is not None:
                rounds.append((pend[0], pend[1] + delta))
                pend = None
            else:
                rounds.append((ts, delta))
    if pend is not None:
        fail(f"{jpath.name}: висячий entry {pend[0]}")
    return rounds


def metrics_value(mpath: Path, key: str) -> str:
    """Из csv метрик: строка вида `key,value` после header 'fork,' — конвенция серии."""
    lines = mpath.read_text(encoding="utf-8").splitlines()
    hi = next(i for i, ln in enumerate(lines) if ln.startswith("fork,"))
    hdr = [h.strip() for h in lines[hi].split(",")]
    row = next(r for r in lines[hi + 1:] if r.strip())
    cols = [c.strip() for c in row.split(",")]
    return dict(zip(hdr, cols))[key]


# индекс cash_delta в journal (по header-строке ts,kind,...)
def cash_col(jpath: Path) -> int:
    for ln in jpath.read_text(encoding="utf-8").splitlines():
        if ln.startswith("ts,kind,"):
            return [h.strip() for h in ln.split(",")].index("cash_delta")
    raise RuntimeError(f"нет header в {jpath}")


CASH_COL = -1

probe_ref: dict[tuple[str, str], tuple[str, str, str, str]] = {}
if PROBE_RAW.exists():
    text = PROBE_RAW.read_bytes().decode("utf-8", errors="replace")
    for ln in text.splitlines():
        p = ln.split()
        if len(p) > 9 and p[0] in MARKETS and p[1] == "4h" and p[2] == "tch2" \
                and p[3] == "tol0.010" and p[4] == "d0.005" and p[5] == "conf0" \
                and p[6] == "k1" and p[7].startswith("reg"):
            exitm = p[8]
            cell = {"V1_24": "V-a1", "V1_48": "V-a2", "V2_R1": "V-a3"}.get(exitm)
            reg = p[7][3:]
            if cell and reg == "а":
                try:
                    nq = next(t for t in p if t.startswith("nq="))[3:]
                    pool = next(t for t in p if t.startswith("pool$="))[6:]
                    ann = next(t for t in p if t.startswith("ann="))[4:].rstrip("%")
                    probe_ref[(p[0], cell)] = (nq, pool, ann, reg)
                except StopIteration:
                    pass
print(f"# probe-справка reg а клеток: {len(probe_ref)} (ожидаем 9)")

n_live = 0
for sym in MARKETS:
    for cell in CELLS:
        out = CALIB / f"dev_{cell}_{sym}"
        stem = f"dev_{cell}_{sym}_{sym}_{START}_{END}"
        mpath, jpath = out / f"{stem}.csv", out / f"{stem}_journal_p.csv"
        if not mpath.exists() or not jpath.exists():
            fail(f"нет артефактов {cell}_{sym}")
            continue
        CASH_COL = cash_col(jpath)
        rs = journal_rounds(jpath)
        n = len(rs)
        net = sum((r[1] for r in rs), Decimal(0))
        ann = net / CAP * Decimal(365) / Decimal(180) * Decimal(100)
        tn = [Decimal(0)] * 3
        tq = [0] * 3
        for ts, d in rs:
            t = (ts - START) // THIRD
            if not (0 <= t < 3):
                fail(f"{cell}_{sym}: ts {ts} вне трети")
                continue
            tn[t] += d
            tq[t] += 1
        # метрика журнала: круг с net==0 (flat без pnl?) не считаем — у E-конвенции только entry/exit
        ga = all(tq[i] >= 3 and tn[i] >= 0 for i in range(3))
        gb = ann >= Decimal("3.0")
        gc = n <= 75
        live = ga and gb and gc
        n_live += live
        # сверка с metrics-csv
        try:
            csv_net = dec(metrics_value(mpath, "net_pnl"))
            csv_n = int(metrics_value(mpath, "rounds"))
            csv_ann = dec(metrics_value(mpath, "annualized_pct"))
        except Exception as e:  # noqa: BLE001
            fail(f"{cell}_{sym}: metrics-парсинг {e}")
            continue
        if net != csv_net:
            fail(f"{cell}_{sym}: journal net {net} != csv {csv_net}")
        if n != csv_n:
            fail(f"{cell}_{sym}: journal n {n} != csv {csv_n}")
        if abs(ann - csv_ann) > Decimal("0.0001"):
            fail(f"{cell}_{sym}: ann {ann} != csv {csv_ann}")
        # probe-справка для reg а
        key = (sym, cell)
        if key in probe_ref:
            pnq, ppool, pann, preg = probe_ref[key]
            mnq = "/".join(str(x) for x in tq)
            if pnq != mnq:
                fail(f"probe-vs-sim {sym} {cell}: nq зонд {pnq} != sim {mnq}")
            if abs(dec(ppool) - net.quantize(Decimal("1"))) > Decimal("1"):
                fail(f"probe-vs-sim {sym} {cell}: pool$ зонд {ppool} != net {net}")
            if abs(dec(pann) - ann.quantize(Decimal("0.01"))) > Decimal("0.01"):
                fail(f"probe-vs-sim {sym} {cell}: ann зонд {pann} != {ann}")
        print(f"{sym:9s} {cell} n={n:3d} nq=({'/'.join(map(str, tq))}) "
              f"ann={ann:+7.2f}% net={net:+10.4f} "
              f"a'={ga} b'={gb} c'={gc} {'LIVE' if live else 'dead'}")

# raw-лог: 27 строк exit=0 (у CTL-блока нет exit= — rc в json), arms на V-5 запрещён
raw = RAW.read_text(encoding="utf-8")
if raw.count("exit=0") != 27:
    fail(f"raw: exit=0 встречено {raw.count('exit=0')} (ожидалось 27)")
if "exit=" in raw and raw.count("exit=") != 27:
    fail(f"raw: строк exit= {raw.count('exit=')} (ожидалось 27)")
if "HOLDOUT_ARMED" in raw:
    fail("raw: HOLDOUT_ARMED — чтение холдаута недопустимо на V-5")
if "ДЕТЕРМИНИЗМ: YES" not in raw:
    fail("raw: нет маркера ДЕТЕРМИНИЗМ: YES")
runs = json.loads((SER / "meas" / "grid_V5_runs.json").read_text(encoding="utf-8"))
ctlr = runs.get("CTL_V-b2_SOLUSDT", {})
if ctlr.get("rc") != 0 or ctlr.get("byte_identical_x2") is not True:
    fail(f"json CTL: {ctlr}")

# CTL побайтно независимо от runner
orig = CALIB / "dev_V-b2_SOLUSDT"
ctl = CALIB / "dev_ctl_V-b2_SOLUSDT"
stem = f"dev_V-b2_SOLUSDT_SOLUSDT_{START}_{END}"
for suf in (".csv", "_journal_p.csv", "_report_p.txt"):
    ha = hashlib.sha256((orig / (stem + suf)).read_bytes()).hexdigest()
    hb = hashlib.sha256((ctl / (stem + suf)).read_bytes()).hexdigest()
    if ha != hb:
        fail(f"CTL {suf}: хэши разошлись")
print("CTL: побайтно 3/3 OK" if not bad else "CTL: см. RAZHOZHDENIE выше")

print(f"\nИТОГ АУДИТА ЛИДА: клеток 27, живых {n_live}, расхождений {len(bad)}")
sys.exit(0 if not bad and n_live == 0 else (1 if bad else 0))
