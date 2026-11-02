# -*- coding: utf-8 -*-
"""Независимый аудит лида E-7 holdout (Series E). Пересчёт с нуля по артефактам.

Черновик (адаптировать под фактические имена файлов замерщика после публикации).

Сверки:
  A) закон: парс таблицы E-2-F (3 клетки-выживших) + окно/критерии E-7 из DRAFT;
     meta каждого holdout-csv == закон 1-в-1 (tol/depth/conf/k/exit/symbol/
     start/end/ctx/дефолты); start==1790294400, end==1793577600 ровно;
  B) инварианты артефактов: net == price_gross - friction; equity == capital + net;
     rounds == stops+takes+tb+forced; ann == net/10000*365/38*100 тем же порядком
     Decimal; journal: 2*rounds строк, сумма cash_delta == net, entry/exit черед.;
  C) stdout-логи замерщика == csv (net/ann/rounds/events/version точными строками);
     в логах есть HOLDOUT_ARMED, нет HOLDOUT_GUARD;
  D) H1/H2 — СВОЕЙ реализацией Decimal-арифметики против его h1h2.py/RESULTS;
  E) rerun-контроль: sha256 3 файлов оригинала vs holdout_E-a3-rerun побайтно сам.

Выход: печать + SUMMARY; ненулевой exit при любом расхождении.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path.home() / "Documents" / "GitHub" / "strategylab"
SDD = REPO / ".superpowers" / "sdd" / "2026-11-02-seriesE-deviation"
PREREG = SDD / "seriesE-deviation-preregistration-DRAFT.md"
RESULTS = SDD / "E7-seriesE-holdout-RESULTS.md"  # публикация замерщика (R-next-1)
MEAS = SDD / "meas" / "holdout"
CALIB = REPO / "data" / "calib"

W_START = 1790294400
W_END = 1793577600
CAPITAL = Decimal("10000")
DAYS = Decimal(38)
SURVIVORS = ["E-a3", "E-a5", "E-b5"]
FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"MISMATCH: {msg}")


# ---------- A) закон: E-2-F для выживших + параметры ----------
law: dict[str, dict[str, object]] = {}
row_re = re.compile(
    r"\| (E-[abc]\d) \| (\w+) 4h tch2 tol([\d.]+) d([\d.]+) k(\d) reg-а \| (\d+) \| (V1_48|V2_R2) \|"
)
for line in PREREG.read_text(encoding="utf-8").splitlines():
    m = row_re.fullmatch(line.strip())
    if m:
        cell, sym, tol, depth, k, conf, exit_mode = m.groups()
        law[cell] = {
            "symbol": sym,
            "tol": Decimal(tol),
            "depth": Decimal(depth),
            "vol_filter": int(k),
            "conf_min": int(conf),
            "exit_mode": exit_mode,
        }
for cell in SURVIVORS:
    if cell not in law:
        fail(f"клетка {cell} не найдена в законе E-2-F")


def read_metrics(folder: Path) -> tuple[dict[str, str], Path]:
    csvs = [p for p in sorted(folder.glob("*.csv")) if not p.name.endswith("_journal_p.csv")]
    if len(csvs) != 1:
        fail(f"{folder.name}: metrics-csv найдено {len(csvs)}")
        raise SystemExit(1)
    rows = list(csv.reader(io.StringIO(csvs[0].read_text(encoding="utf-8"), newline="")))
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "fork")
    return dict(zip(rows[hi], rows[hi + 1])), csvs[0]


nets: dict[str, Decimal] = {}
anns: dict[str, Decimal] = {}
for cell in SURVIVORS:
    L = law[cell]
    folder = CALIB / f"holdout_{cell}"
    if not folder.is_dir():
        fail(f"{cell}: нет артефактной папки {folder}")
        continue
    m, cpath = read_metrics(folder)
    exp = {
        "symbol": str(L["symbol"]), "tol": str(L["tol"]), "depth": str(L["depth"]),
        "conf_min": str(L["conf_min"]), "vol_filter": str(L["vol_filter"]),
        "exit_mode": str(L["exit_mode"]), "start_ts": str(W_START), "end_ts": str(W_END),
        "ctx_hours": "960", "zone_tf_secs": "14400", "touches": "2",
        "capital": "10000", "taker_fee": "0.0006", "slippage": "0.0005",
        "fork": "pessimistic",
    }
    for k, v in exp.items():
        if m[k] != v:
            fail(f"{cell}: колонка {k}={m[k]!r} ≠ закону {v!r}")

    # B) инварианты (Decimal точно)
    net = Decimal(m["net_pnl"])
    if net != Decimal(m["price_gross"]) - Decimal(m["friction"]):
        fail(f"{cell}: net != price_gross - friction")
    if Decimal(m["equity"]) != CAPITAL + net:
        fail(f"{cell}: equity != capital + net")
    r = int(m["rounds"])
    if r != sum(int(m[k]) for k in ("n_stops", "n_takes", "n_tb", "n_forced")):
        fail(f"{cell}: rounds != stops+takes+tb+forced")
    ann = net / CAPITAL * Decimal(365) / DAYS * Decimal(100)
    if format(ann, "f") != m["annualized_pct"]:
        fail(f"{cell}: пересчёт ann {format(ann, 'f')} != csv {m['annualized_pct']}")
    stem = cpath.with_suffix("")
    jr = list(csv.DictReader(io.StringIO((stem.parent / (stem.name + "_journal_p.csv")).read_text(encoding="utf-8"), newline="")))
    if len(jr) != 2 * r:
        fail(f"{cell}: journal {len(jr)} строк != 2*rounds")
    jsum = sum((Decimal(row["cash_delta"]) for row in jr), Decimal(0))
    if jsum != net:
        fail(f"{cell}: journal cash_delta sum != net")
    open_e = False
    for row in jr:
        if row["kind"] == "entry":
            if open_e:
                fail(f"{cell}: entry на входе")
            open_e = True
        else:
            if not open_e:
                fail(f"{cell}: exit без entry")
            open_e = False
        ts = int(row["ts"])
        if not (W_START <= ts < W_END):
            fail(f"{cell}: journal ts {ts} вне окна E-7")
    nets[cell] = net
    anns[cell] = Decimal(m["annualized_pct"])

    # C) stdout-лог == csv; arms-событие есть, отказа нет
    log = MEAS / f"{cell}.log"
    if log.exists():
        text = log.read_text(encoding="utf-8")
        if "HOLDOUT_ARMED" not in text:
            fail(f"{cell}: в логе нет HOLDOUT_ARMED")
        if "HOLDOUT_GUARD" in text:
            fail(f"{cell}: в логе HOLDOUT_GUARD (отказ!)")
        for pat, want in (
            (r"net_pnl: (\S+) annualized_pct: (\S+)", (m["net_pnl"], m["annualized_pct"])),
            (r"rounds: (\d+) events: (\d+)", (m["rounds"], m["events"])),
            (r"deviation_version: ([0-9a-f]{12})", (m["deviation_version"],)),
        ):
            g = re.search(pat, text)
            if not g or g.groups() != want:
                fail(f"{cell}: лог {g.groups() if g else None} != csv {want}")
    else:
        fail(f"{cell}: нет raw-лога {log}")

# ---------- D) H1/H2 своей реализацией ----------
if set(nets) == set(SURVIVORS):
    h1_n = sum(1 for v in nets.values() if v >= 0)
    h1 = h1_n >= 2
    total = sum(nets.values(), Decimal(0))
    h2_val = total / 3 / CAPITAL * Decimal(365) / Decimal(38) * Decimal(100)
    h2 = h2_val >= Decimal("3.0")
    verdict = "GO" if (h1 and h2) else "NO-GO"
    print(f"H1: net>=0 у {h1_n}/3 → {'PASS' if h1 else 'FAIL'}")
    print(f"sum(net) = {format(total, 'f')}")
    print(f"H2: средняя годовая = {format(h2_val, 'f')} %/год → {'PASS' if h2 else 'FAIL'}")
    print(f"вердикт-формула: {verdict}")
    # сверка с публикацией замерщика (точные строки чисел)
    rt = RESULTS.read_text(encoding="utf-8") if RESULTS.exists() else ""
    for cell in SURVIVORS:
        if format(nets[cell], "f") not in rt:
            fail(f"{cell}: net {format(nets[cell], 'f')} не найден в RESULTS замерщика")
else:
    fail("не все клетки прочитаны — H1/H2 не посчитаны")

# ---------- E) rerun-контроль побайтно сам ----------
orig = CALIB / "holdout_E-a3"
rer = CALIB / "holdout_E-a3-rerun"
if orig.is_dir() and rer.is_dir():
    names = sorted(p.name for p in orig.iterdir())
    if len(names) != 3:
        fail(f"holdout_E-a3: {len(names)} файлов, ждал 3")
    for name in names:
        q = rer / name
        if not q.exists():
            fail(f"rerun: нет {name}")
        elif hashlib.sha256((orig / name).read_bytes()).hexdigest() != hashlib.sha256(q.read_bytes()).hexdigest():
            fail(f"rerun: {name} НЕ побайтно идентичен")
    print(f"rerun sha256: проверено {len(names)} файла")
else:
    fail("нет папок rerun-контроля")

print()
print(f"SUMMARY: расхождений = {len(FAILS)}")
sys.exit(1 if FAILS else 0)
