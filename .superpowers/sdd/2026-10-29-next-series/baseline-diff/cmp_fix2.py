# -*- coding: utf-8 -*-
"""Компаратор шага 4: артефакты fix2 (bdiff_out/fix2/all) vs замороженные (data/calib root).

Вердикты на файл:
  IDENTICAL  - побайтово равен замороженному (SHA256);
  INTENDED   - только разрешённые расхождения:
      1) *_report_p.txt: вставлены строки '# window_cancelled: N' сразу после
         каждой строки '# cell:', остальной текст равен замороженному;
         инвариант: N==1 у limit_pullback ETH f1 (старый pending_limit_buy==1),
         N==0 у всех остальных ячеек;
      2) limit_pullback_ETHUSDT_1761523200_1771113600.csv: pending_limit_buy
         1->0 в обеих fork-строках, прочие колонки и строки равны;
  REGRESSION - любое другое расхождение (журналы обязаны быть побайтово равны).

Вне репо: C:\\...\\bdiff_tools\\cmp_fix2.py. Ничего не пишет в репо.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

OUT_DIR = Path(r"C:\Users\egoro\AppData\Local\Temp\opencode\bdiff_out\fix2\all")
FROZEN_DIR = Path(r"C:\Users\egoro\Documents\GitHub\strategylab\data\calib")
RESULT_TXT = Path(r"C:\Users\egoro\AppData\Local\Temp\opencode\bdiff_out\fix2\compare_report.txt")
PENDING_CSV = "limit_pullback_ETHUSDT_1761523200_1771113600.csv"
PENDING_REPORT = "limit_pullback_ETHUSDT_1761523200_1771113600_report_p.txt"
WC_RE = re.compile(r"^# window_cancelled: (\d+)$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_lines(path: Path) -> list[str]:
    return path.read_bytes().decode("utf-8").split("\n")


def cmp_report(new: Path, old: Path) -> tuple[str, str, list[int]]:
    """Новый отчёт == замороженный + вставки '# window_cancelled: N' после '# cell:'."""
    new_lines = read_lines(new)
    old_lines = read_lines(old)
    stripped: list[str] = []
    cancelled: list[int] = []
    problems: list[str] = []
    prev_cell = False
    cells_seen = 0
    for line in new_lines:
        m = WC_RE.match(line)
        if m:
            if not prev_cell:
                problems.append("строка window_cancelled не сразу после '# cell:'")
            cancelled.append(int(m.group(1)))
            prev_cell = False
            continue
        if prev_cell:
            problems.append(f"после '# cell:' нет '# window_cancelled:' (далее {line[:50]!r})")
        if line.startswith("# cell:"):
            cells_seen += 1
            prev_cell = True
        else:
            prev_cell = False
        stripped.append(line)
    if cells_seen != len(cancelled):
        problems.append(f"ячеек={cells_seen}, строк window_cancelled={len(cancelled)}")
    if stripped != old_lines:
        if len(stripped) != len(old_lines):
            problems.append(f"число строк (без window_cancelled): new={len(stripped)} frozen={len(old_lines)}")
        for idx, (a, b) in enumerate(zip(stripped, old_lines)):
            if a != b:
                problems.append(f"строка {idx + 1}: new={a[:80]!r} frozen={b[:80]!r}")
                break
    if problems:
        return "REGRESSION", "; ".join(problems), cancelled
    return "INTENDED", f"вставлены window_cancelled={cancelled}", cancelled


def cmp_pending_csv(new: Path, old: Path) -> tuple[str, str, int]:
    """CSV: метрические колонки равны; pending_limit_buy 1->0 в обеих fork-строках."""
    new_lines = read_lines(new)
    old_lines = read_lines(old)
    if len(new_lines) != len(old_lines):
        return "REGRESSION", f"число строк: new={len(new_lines)} frozen={len(old_lines)}", 0
    header_idx = next(i for i, line in enumerate(old_lines) if line.startswith("fork,spec_version,"))
    cols = old_lines[header_idx].split(",")
    i_lb = cols.index("pending_limit_buy")
    i_oth = cols.index("pending_other")
    problems: list[str] = []
    pending_drops = 0
    old_pending_sum = 0
    for idx, (nl, ol) in enumerate(zip(new_lines, old_lines)):
        if nl == ol:
            continue
        if not ol.startswith(("pessimistic,", "optimistic,")):
            problems.append(f"строка {idx + 1}: отличается не fork-строка ({ol[:60]!r})")
            continue
        nf, of = nl.split(","), ol.split(",")
        if len(nf) != len(of):
            problems.append(f"строка {idx + 1}: число колонок {len(nf)} vs {len(of)}")
            continue
        diff = [j for j in range(len(of)) if nf[j] != of[j]]
        ok = all(j == i_lb and of[j] == "1" and nf[j] == "0" for j in diff)
        if diff and ok:
            pending_drops += 1
        else:
            problems.append(
                f"строка {idx + 1}: расхождение колонок {diff}: "
                + "; ".join(f"{cols[j]}: {of[j]}->{nf[j]}" for j in diff)
            )
    # старый pending из замороженного файла (для сверки с window_cancelled)
    for line in old_lines[header_idx + 1:]:
        if line.startswith("pessimistic,"):
            old_pending_sum = int(line.split(",")[i_lb])
            break
    if problems:
        return "REGRESSION", "; ".join(problems), old_pending_sum
    if pending_drops != 2:
        return "REGRESSION", f"ожидалось 2 fork-строки с pending 1->0, получено {pending_drops}", old_pending_sum
    return "INTENDED", "pending_limit_buy 1->0 в обеих fork-строках; метрики равны", old_pending_sum


def main() -> int:
    frozen_root = {p.name for p in FROZEN_DIR.iterdir() if p.is_file()}
    out_names = sorted(p.name for p in OUT_DIR.iterdir() if p.is_file()) if OUT_DIR.exists() else []
    missing = sorted(frozen_root - set(out_names))
    extra = sorted(set(out_names) - frozen_root)
    lines: list[str] = []
    n_identical = n_intended = n_regression = 0
    old_pending = None
    eth_f1_cancelled = None
    for name in out_names:
        new, old = OUT_DIR / name, FROZEN_DIR / name
        if not old.exists():
            lines.append(f"{name}\tREGRESSION\tнет замороженного аналога")
            n_regression += 1
            continue
        if sha256(new) == sha256(old):
            lines.append(f"{name}\tIDENTICAL")
            n_identical += 1
            continue
        if name.endswith("_report_p.txt"):
            verdict, details, cancelled = cmp_report(new, old)
            expect = [1] if name == PENDING_REPORT else [0] * len(cancelled)
            if cancelled != expect:
                verdict = "REGRESSION"
                details += f"; window_cancelled={cancelled}, ожидалось {expect}"
            if name == PENDING_REPORT and cancelled:
                eth_f1_cancelled = cancelled[0]
            lines.append(f"{name}\t{verdict}\t{details}")
        elif name == PENDING_CSV:
            verdict, details, old_pending = cmp_pending_csv(new, old)
            lines.append(f"{name}\t{verdict}\t{details}")
        else:
            verdict = "REGRESSION"
            lines.append(f"{name}\t{verdict}\tпобайтовое расхождение (журнал/csv вне разрешённых)")
        if verdict == "INTENDED":
            n_intended += 1
        else:
            n_regression += 1
    invariant_ok = None
    if old_pending is not None and eth_f1_cancelled is not None:
        invariant_ok = old_pending == eth_f1_cancelled
        lines.append(
            f"INVARIANT old_pending({old_pending}) == window_cancelled({eth_f1_cancelled}): {invariant_ok}"
        )
    summary = (
        f"files: out={len(out_names)} frozen_root={len(frozen_root)} "
        f"missing={len(missing)} extra={len(extra)}\n"
        f"IDENTICAL={n_identical} INTENDED={n_intended} REGRESSION={n_regression}\n"
        f"invariant_old_pending_eq_N={invariant_ok}"
    )
    if missing:
        summary += "\nMISSING: " + ", ".join(missing)
    if extra:
        summary += "\nEXTRA: " + ", ".join(extra)
    full = summary + "\n\n" + "\n".join(lines) + "\n"
    RESULT_TXT.write_text(full, encoding="utf-8")
    print(summary)
    for line in lines:
        if "\tIDENTICAL" not in line:
            print(line)
    ok = n_regression == 0 and not missing and not extra and len(out_names) == 160 and invariant_ok
    print("CMP_FIX2_OK" if ok else "CMP_FIX2_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
