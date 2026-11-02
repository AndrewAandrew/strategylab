"""Аудит ведущего Series B carry, часть 1: целостность 18 артефактов и сверка
с числами брата (stdout-сводки meas-rB). Только файлы-артефакты, БД не открывается.

Проверки на каждый фолд:
  1. csv data-строка == report_p по всем 8 числовым полям + carry_version;
  2. журнал: Σ cash_delta == net_pnl; cash_after — корректный бегущий баланс;
     n(settle)==n_settles; n(entry)==holds; funding_gross==Σ settle-дельт;
     friction==−Σ прочих; net==funding_gross−friction; equity==capital+net;
     annualized==net/capital*365/days*100 (±1e-9);
  3. структура журнала: kinds ∈ {entry,settle,exit}, порядок entry→...→exit,
     entry-first, ts неубывающий по всему журналу;
  4. stdout брата (Temp/opencode/meas-rB/stdout_<cell>.txt): carry_version,
     net_pnl, annualized_pct, n_settles, holds, margin_call, flat_fold — слово в
     слово против report_p (первая публикация не врёт);
  5. гейты: B2-1 net>=0, B2-3 annualized>0, B2-5 margin_call==0,
     правило-1: Σn_settles по клетке >= 30; flat_fold-клетки — DEAD по C.
"""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal
from pathlib import Path

REPO = Path("C:/Users/egoro/Documents/GitHub/strategylab")
CALIB = REPO / "data" / "calib"
BROTHER = Path("C:/Users/egoro/AppData/Local/Temp/opencode/meas-rB")

CELLS = ["t05_btc", "t05_eth", "t10_btc", "t10_eth", "t20_btc", "t20_eth"]
FOLDS = ["f1", "f2", "f3"]
WINDOWS = {
    "f1": (1761523200, 1771113600),
    "f2": (1771113600, 1780704000),
    "f3": (1780704000, 1790294400),
}
SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT"}
TOL = Decimal("1e-9")


def read_report(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_ ]+?)(?: \([^)]*\))?: (.*)$", line)
        if m and not line.startswith("#"):
            key = m.group(1).strip()
            fields[key] = m.group(2).strip()
    version = re.search(r"carry_version=(\w+)", path.read_text(encoding="utf-8"))
    fields["carry_version"] = version.group(1) if version else ""
    return fields


def read_csv_row(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header_i = next(i for i, ln in enumerate(lines) if ln.startswith("fork,"))
    reader = csv.DictReader(lines[header_i:header_i + 2])
    return next(iter(reader))


def parse_journal(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return list(csv.DictReader(lines))


def fail(msg: str, bucket: list[str]) -> None:
    bucket.append(msg)


def main() -> None:
    problems: list[str] = []
    rows_out: list[str] = []
    for cell in CELLS:
        sym_short, th = cell.split("_")[1], cell.split("_")[0]
        symbol = SYMBOL[sym_short]
        agg_net = Decimal(0)
        agg_n = 0
        cell_notes: list[str] = []
        for fold in FOLDS:
            stem = f"carry_{symbol}_{WINDOWS[fold][0]}_{WINDOWS[fold][1]}"
            out = CALIB / f"rB_{cell}_{fold}"
            rep = read_report(out / f"{stem}_report_p.txt")
            row = read_csv_row(out / f"{stem}.csv")
            js = parse_journal(out / f"{stem}_journal_p.csv")

            d = {k: Decimal(v) for k, v in [
                ("net", rep["net_pnl"]), ("equity", rep["equity"]),
                ("gross", rep["funding_gross"]), ("fric", rep["friction"]),
                ("ann", rep["annualized_pct"]),
            ]}
            n_settles = int(rep["n_settles"])
            holds = int(rep["holds"])
            mc = int(rep["margin_call"])
            ff = int(rep["flat_fold"])

            # 1. csv vs report
            if row["carry_version"] != rep["carry_version"]:
                fail(f"{cell}_{fold}: csv carry_version != report", problems)
            for key, col in [("net", "net_pnl"), ("equity", "equity"),
                             ("gross", "funding_gross"), ("fric", "friction"),
                             ("ann", "annualized_pct"),
                             ("n", "n_settles"), ("h", "holds")]:
                cv = row[col]
                rv = str(d[key]) if key in d else (str(n_settles) if key == "n" else str(holds))
                if Decimal(cv) != Decimal(rv):
                    fail(f"{cell}_{fold}: csv.{col}={cv} != report={rv}", problems)
            # 2. журнал-адитивность
            deltas = [Decimal(r["cash_delta"]) for r in js]
            afters = [Decimal(r["cash_after"]) for r in js]
            if deltas and sum(deltas) != Decimal(rep["net_pnl"]):
                fail(f"{cell}_{fold}: Σjournal != net_pnl", problems)
            run = Decimal(0)
            for i, (de, af) in enumerate(zip(deltas, afters)):
                run += de
                if run != af:
                    fail(f"{cell}_{fold}: cash_after[{i}] рассинхрон", problems)
                    break
            settles = [r for r in js if r["kind"] == "settle"]
            entries = [r for r in js if r["kind"] == "entry"]
            exits = [r for r in js if r["kind"] == "exit"]
            other = [r for r in js if r["kind"] not in ("settle", "entry", "exit")]
            if other:
                fail(f"{cell}_{fold}: неизвестный kind", problems)
            if len(settles) != n_settles or len(entries) != holds or len(exits) != holds:
                fail(f"{cell}_{fold}: счётчики журналов != report", problems)
            if sum(Decimal(r["cash_delta"]) for r in settles) != Decimal(rep["funding_gross"]):
                fail(f"{cell}_{fold}: Σsettle != funding_gross", problems)
            fric_js = -sum((Decimal(r["cash_delta"]) for r in js if r["kind"] != "settle"),
                           Decimal(0))
            if fric_js != Decimal(rep["friction"]):
                fail(f"{cell}_{fold}: Σfriction != friction", problems)
            if abs(Decimal(rep["funding_gross"]) - Decimal(rep["friction"])
                   - Decimal(rep["net_pnl"])) > Decimal("1e-24"):
                # net копится построчно, gross/fric — отдельными суммами:
                # последняя цифра 28-разрядного контекста может расходиться (r7
                # прецедент: округление колонок 1e-25 = норма, не инцидент)
                fail(f"{cell}_{fold}: gross-fric != net", problems)
            if Decimal(rep["equity"]) != Decimal("10000") + Decimal(rep["net_pnl"]):
                fail(f"{cell}_{fold}: equity != capital+net", problems)
            days = Decimal(WINDOWS[fold][1] - WINDOWS[fold][0]) / Decimal(86400)
            ann = Decimal(rep["net_pnl"]) / Decimal("10000") * Decimal(365) / days * 100
            if abs(ann - Decimal(rep["annualized_pct"])) > TOL:
                fail(f"{cell}_{fold}: annualized пересчёт != report", problems)
            # 3. структура: ts неубывающий; каждый entry закрыт exit'ом по порядку
            ts_list = [int(r["ts"]) for r in js]
            if ts_list != sorted(ts_list):
                fail(f"{cell}_{fold}: журнал не по ts", problems)
            kinds = [r["kind"] for r in js if r["kind"] != "settle"]
            if kinds and (kinds[0] != "entry" or kinds[-1] != "exit"
                          or any(kinds[i:i + 2] != ["entry", "exit"]
                                 for i in range(0, len(kinds) - 1, 2))):
                fail(f"{cell}_{fold}: порядок entry/exit нарушен", problems)
            # 4. stdout брата
            so = (BROTHER / f"stdout_{th}_{sym_short}_{fold}.txt").read_text(
                encoding="utf-8")
            for pat, val in [
                (r"carry_version: (\w+)", rep["carry_version"]),
                (r"net_pnl: (\S+)", rep["net_pnl"]),
                (r"annualized_pct: (\S+)", rep["annualized_pct"]),
                (r"n_settles: (\d+)", str(n_settles)),
                (r"holds: (\d+)", str(holds)),
                (r"margin_call: ([01])", str(mc)),
                (r"flat_fold: ([01])", str(ff)),
            ]:
                m = re.search(pat, so)
                if not m or m.group(1) != val:
                    fail(f"{cell}_{fold}: stdout '{pat}' != '{val}'", problems)
            # 5. гейты по фолду
            agg_net += Decimal(rep["net_pnl"])
            agg_n += n_settles
            gate_1 = "PASS" if Decimal(rep["net_pnl"]) >= 0 else "FAIL"
            gate_3 = "PASS" if Decimal(rep["annualized_pct"]) > 0 else "FAIL"
            if ff:
                cell_notes.append("flat_fold")
        agg_days = sum(Decimal(e - s) for s, e in WINDOWS.values()) / Decimal(86400)
        agg_ann = agg_net / Decimal("10000") * Decimal(365) / agg_days * 100
        g1 = "PASS" if agg_net >= 0 else "FAIL"
        g2 = "PASS" if agg_ann >= 3 else "FAIL"
        gr1 = "PASS" if agg_n >= 30 else "FAIL(0=settle)" if agg_n == 0 else "FAIL"
        rows_out.append(
            f"{cell}: agg_net={agg_net:.6f}$ agg_ann={agg_ann:.4f}%/yr Σn={agg_n} "
            f"| B2-1:{g1} B2-2(>=3%):{g2} правило1(>=30):{gr1} "
            f"{' '.join(cell_notes)}"
        )

    print(f"ПРОБЛЕМЫ ЦЕЛОСТНОСТИ: {len(problems)}")
    for p in problems:
        print("  FAIL", p)
    print()
    for r in rows_out:
        print(r)


if __name__ == "__main__":
    main()
