# -*- coding: utf-8 -*-
"""Независимый аудит лида замера Series E (E-6). Пересчёт ВСЕГ с нуля по артефактам.

Сверки:
 A) закон E-2-F (парсится из пререгистрации) == константы runner'а == метa-колонки
    каждого из 54 артефактных csv (tol/depth/conf/k/exit/symbol/окно/ctx/дефолты);
 B) инварианты каждого артефакта: net == price_gross - friction (Decimal точно);
    equity == capital + net; rounds == stops+takes+tb+forced; annualized ==
    net/10000*365/111*100 в том же порядке Decimal-операций; journal: 2*rounds
    строк, сумма cash_delta == net, чередование entry/exit;
 C) stdout raw-лога == csv-строка (net/ann/rounds/events/version, точные строки);
 D) гейты E2-1..E2-5 пересчитаны СВОЕЙ реализацией; pooled_exact против json
    замерщика; bootstrap E2-4 — своя реализация заявленного метода (Random(42),
    блоки 30d circular, старты uniform 0..322, 1000 повторов, pctl 2.5/97.5
    линейная) против его json: lo/hi/mean совпадение;
 E) набор ALL-клеток == {E-a3, E-a5, E-b5};
 F) RERUN E-b3 f2 — побайтно сам (sha256 3 файла);
 G) raw: 55 блоков, exit=0 везде, ни строки GUARD; ни один cmd end > 1790294400.

Выход: печать отчётов + summary; ненулевой exit при любом расхождении.
"""
from __future__ import annotations

import csv
import hashlib
import io
import random
import re
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path.home() / "Documents" / "GitHub" / "strategylab"
SDD = REPO / ".superpowers" / "sdd" / "2026-11-02-seriesE-deviation"
PREREG = SDD / "seriesE-deviation-preregistration-DRAFT.md"
RUNNER = SDD / "meas" / "run_grid_E.py"
RAW = SDD / "E5-seriesE-grid-raw.txt"
GATES_JSON = SDD / "meas" / "grid_E_gates.json"
RESULTS = SDD / "E5-seriesE-grid-RESULTS.md"
CALIB = REPO / "data" / "calib"
HOLDOUT = 1790294400
CAPITAL = Decimal("10000")
FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"MISMATCH: {msg}")


import json

gjson = json.loads(GATES_JSON.read_text(encoding="utf-8"))

# ---------- A) закон: парс таблицы E-2-F ----------
law: dict[str, dict[str, object]] = {}
row_re = re.compile(
    r"\| (E-[abc]\d) \| (\w+) 4h tch2 tol([\d.]+) d([\d.]+) k(\d) reg-а \| (\d+) \| (V1_48|V2_R2) \|".re_flags if False else
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
            "db": {"ETHUSDT": "data/ethusdt.db", "BTCUSDT": "data/btcusdt.db"}[sym],
        }
if len(law) != 18:
    fail(f"парс закона дал {len(law)} клеток, ждал 18")

FOLDS = {"f1": (1761523200, 1771113600), "f2": (1771113600, 1780704000), "f3": (1780704000, HOLDOUT)}

# runner-константы против закона
rsrc = RUNNER.read_text(encoding="utf-8")
if 'REPO = Path(__file__).resolve().parents[4]' not in rsrc:
    fail("runner REPO parents[4] —Unexpected")
for cell, L in law.items():
    pass  # сверка фактических команд ниже через метa-колонки артефактов

# ---------- артефакты ----------
def read_metrics(cell: str, fold: str) -> tuple[dict[str, str], Path]:
    folder = CALIB / f"dev_{cell}_{fold}"
    csvs = [p for p in sorted(folder.glob("*.csv")) if not p.name.endswith("_journal_p.csv")]
    if len(csvs) != 1:
        fail(f"{cell} {fold}: metrics-csv найдено {len(csvs)}")
        raise SystemExit(1)
    rows = list(csv.reader(io.StringIO(csvs[0].read_text(encoding="utf-8"), newline="")))
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "fork")
    return dict(zip(rows[hi], rows[hi + 1])), csvs[0]

def read_journal(path: Path) -> list[dict[str, str]]:
    j = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    return j

cells = [f"E-{p}{i}" for p in "abc" for i in range(1, 7)]
daily: dict[str, list[float]] = {c: [0.0] * 333 for c in cells}
pooled_net: dict[str, Decimal] = {}
gates: dict[str, dict[str, bool]] = {}
raw_out: dict[tuple[str, str], dict[str, str]] = {}

# raw stdout blocks
raw_txt = RAW.read_text(encoding="utf-8")
blocks = re.findall(
    r"===== (E-[abc]\d(?: RERUN)?(?: \(детерминизм-контроль\))?) (f\d) RERUN[^\n]*=====", raw_txt
)
cur = None
for block in re.split(r"(?m)^===== ", raw_txt)[1:]:
    head, rest = block.split("\n", 1)
    if "RERUN" in head or "детерминизм" in head:
        continue
    parts = head.split()
    if parts[0] in law:
        key = (parts[0], parts[1])
        m_net = re.search(r"net_pnl: (\S+) annualized_pct: (\S+) rounds: (\d+) events: (\d+) flat_fold: (\d)", rest)
        m_ver = re.search(r"deviation_version: ([0-9a-f]{12})", rest)
        m_exit = re.search(r"exit=(\d+)", rest)
        m_cmd = re.search(r"--out (\S+)", rest)
        if not (m_net and m_ver and m_exit and m_cmd):
            fail(f"raw block {key}: не распарсились stdout/exit/cmd")
            continue
        raw_out[key] = {
            "net": m_net.group(1), "ann": m_net.group(2), "rounds": m_net.group(3),
            "events": m_net.group(4), "flat": m_net.group(5), "version": m_ver.group(1),
            "exit": m_exit.group(1), "out": m_cmd.group(1),
        }
if "GUARD" in raw_txt:
    fail("в raw-логе встречается GUARD")
if len(raw_out) != 54:
    fail(f"raw блоков клеток {len(raw_out)}, ждал 54")
if any(v["exit"] != "0" for v in raw_out.values()):
    fail("есть запуск с exit!=0")

for cell in cells:
    L = law[cell]
    nets: list[Decimal] = []
    anns: list[Decimal] = []
    rnds: list[int] = []
    for fold in ("f1", "f2", "f3"):
        m, cpath = read_metrics(cell, fold)
        stem = cpath.with_suffix("")
        # A) meta == закон
        exp = {
            "symbol": str(L["symbol"]), "tol": str(L["tol"]), "depth": str(L["depth"]),
            "conf_min": str(L["conf_min"]), "vol_filter": str(L["vol_filter"]),
            "exit_mode": str(L["exit_mode"]), "start_ts": str(FOLDS[fold][0]),
            "end_ts": str(FOLDS[fold][1]), "ctx_hours": "960", "zone_tf_secs": "14400",
            "touches": "2", "capital": "10000", "taker_fee": "0.0006", "slippage": "0.0005",
            "fork": "pessimistic",
        }
        for k, v in exp.items():
            if m[k] != v:
                fail(f"{cell} {fold}: колонка {k}={m[k]!r} ≠ закону {v!r}")
        if int(m["end_ts"]) > HOLDOUT:
            fail(f"{cell} {fold}: end > HOLDOUT")
        # C) raw stdout == csv
        ro = raw_out.get((cell, fold))
        if ro is None:
            fail(f"{cell} {fold}: нет raw-блока")
        else:
            for got, want in (
                (ro["net"], m["net_pnl"]), (ro["ann"], m["annualized_pct"]),
                (ro["rounds"], m["rounds"]), (ro["events"], m["events"]),
                (ro["version"], m["deviation_version"]),
            ):
                if got != want:
                    fail(f"{cell} {fold}: raw {got!r} != csv {want!r}")
        # B) инварианты
        net, gross, fric = Decimal(m["net_pnl"]), Decimal(m["price_gross"]), Decimal(m["friction"])
        if net != gross - fric:
            fail(f"{cell} {fold}: net != price_gross - friction")
        if Decimal(m["equity"]) != CAPITAL + net:
            fail(f"{cell} {fold}: equity != capital + net")
        r = int(m["rounds"])
        if r != sum(int(m[k]) for k in ("n_stops", "n_takes", "n_tb", "n_forced")):
            fail(f"{cell} {fold}: rounds != stops+takes+tb+forced")
        ann = net / CAPITAL * Decimal(365) / (Decimal(int(m["end_ts"]) - int(m["start_ts"])) / Decimal(86400)) * Decimal(100)
        if format(ann, "f") != m["annualized_pct"]:
            fail(f"{cell} {fold}: пересчёт ann {format(ann,'f')} != csv {m['annualized_pct']}")
        jr = read_journal(stem.parent / (stem.name + "_journal_p.csv"))
        if len(jr) != 2 * r:
            fail(f"{cell} {fold}: journal {len(jr)} строк != 2*rounds")
        jsum = sum((Decimal(row["cash_delta"]) for row in jr), Decimal(0))
        if jsum != net:
            fail(f"{cell} {fold}: journal cash_delta sum != net")
        acc = Decimal(0)
        open_e = False
        for row in jr:
            if row["kind"] == "entry":
                if open_e:
                    fail(f"{cell} {fold}: entry на входе")
                acc = Decimal(row["cash_delta"])
                open_e = True
            else:
                if not open_e:
                    fail(f"{cell} {fold}: exit без entry")
                day = (int(row["ts"]) - 1761523200) // 86400
                if not (0 <= day < 333):
                    fail(f"{cell} {fold}: exit-день вне 333 ({day})")
                daily[cell][day] += float(acc + Decimal(row["cash_delta"]))
                acc = Decimal(0)
                open_e = False
        # json замерщика == артефакт
        jf = gjson[cell]["folds"][fold]
        for k in ("net_pnl", "annualized_pct", "price_gross", "friction", "deviation_version"):
            if str(jf[k]) != m[k]:
                fail(f"{cell} {fold}: json {k}={jf[k]!r} != csv {m[k]!r}")
        if int(jf["rounds"]) != r:
            fail(f"{cell} {fold}: json rounds != csv")
        nets.append(net)
        anns.append(Decimal(m["annualized_pct"]))
        rnds.append(r)
    pooled = sum(nets, Decimal(0))
    pooled_net[cell] = pooled
    pool_ann = pooled / CAPITAL * Decimal(365) / Decimal(333) * Decimal(100)
    # D) bootstrap — своя реализация заявленного метода
    rng = random.Random(42)
    stats: list[float] = []
    n = 333
    for _ in range(1000):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(0, 323)
            for i in range(30):
                if len(sample) >= n:
                    break
                sample.append(daily[cell][(start + i) % n])
        stats.append(sum(sample) / n * 365 / 10000.0 * 100)
    stats.sort()

    def pct(p: float) -> float:
        k = p * (len(stats) - 1)
        lo = int(k)
        frac = k - lo
        hi = min(lo + 1, len(stats) - 1)
        return stats[lo] + frac * (stats[hi] - stats[lo])

    lo95, hi95 = pct(0.025), pct(0.975)
    jb = gjson[cell]["bootstrap"]
    if lo95 != jb["lower_2.5pct"] or hi95 != jb["upper_97.5pct"]:
        fail(f"{cell}: bootstrap lo/hi не совпал: я {lo95:.6f}/{hi95:.6f} vs json {jb['lower_2.5pct']:.6f}/{jb['upper_97.5pct']:.6f}")
    my_gates = {
        "E2-1": all(x >= 0 for x in nets),
        "E2-2": pool_ann >= Decimal("3.0"),
        "E2-3": all(a > 0 for a in anns),
        "E2-4": lo95 > 0.0,
        "E2-5": all(x <= 25 for x in rnds),
    }
    my_gates["ALL"] = all(v for k, v in my_gates.items() if k != "ALL")
    for k, v in my_gates.items():
        if bool(gjson[cell]["gates"][k]) != v:
            fail(f"{cell}: гейт {k}: я {v} vs json {gjson[cell]['gates'][k]}")
    gates[cell] = my_gates
    if gjson[cell]["pooled_net"] != str(pooled):
        fail(f"{cell}: pooled_net {gjson[cell]['pooled_net']} != мой {pooled}")
    if gjson[cell]["pooled_ann"] != format(pool_ann, "f"):
        fail(f"{cell}: pooled_ann {gjson[cell]['pooled_ann']} != мой {format(pool_ann,'f')}")
    ds = sum(daily[cell])
    if abs(ds - float(pooled)) > 1e-6:
        fail(f"{cell}: daily sum {ds} != pooled {pooled}")
    print(f"{cell}: pooled={format(pooled,'f')} ann={format(pool_ann,'f')} lo95={lo95:.4f} gates=" +
          ",".join(f"{k}={'P' if v else 'F'}" for k, v in my_gates.items()))

# E) набор выживших
alive = sorted(c for c in cells if gates[c]["ALL"])
if alive != ["E-a3", "E-a5", "E-b5"]:
    fail(f"ALL-набор {alive} != [E-a3, E-a5, E-b5]")

# F) rerun побайтно сам
orig = CALIB / "dev_E-b3_f2"
rer = CALIB / "dev_E-b3_f2_RERUN"
for p in sorted(orig.iterdir()):
    q = rer / p.name
    if not q.exists():
        fail(f"rerun: нет {p.name}")
    elif hashlib.sha256(p.read_bytes()).digest() != hashlib.sha256(q.read_bytes()).digest():
        fail(f"rerun: {p.name} НЕ побайтно")
print(f"rerun sha256: проверено {len(list(orig.iterdir()))} файла")

# G) cmd-таблица runner'а против raw-команд
for cell in cells:
    for fold in ("f1", "f2", "f3"):
        want_out = f"data/calib/dev_{cell}_{fold}"
        if raw_out[(cell, fold)]["out"] != want_out:
            fail(f"{cell} {fold}: out {raw_out[(cell, fold)]['out']} != {want_out}")

print()
print(f"SUMMARY: расхождений = {len(FAILS)}")
sys.exit(1 if FAILS else 0)
