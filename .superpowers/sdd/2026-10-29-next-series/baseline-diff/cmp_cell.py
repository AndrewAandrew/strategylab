"""Content-level compare: per-cell harness run vs frozen grid artifact.

Usage: python cmp_cell.py <cell_out_dir> <frozen_stem_prefix>
Compares (byte-equality of text lines):
  1. CSV data rows for the cell's spec_version (both forks).
  2. Journal rows (p and o) filtered by spec_version, in order.
  3. report_p.txt cell block matched by "# cell: spec_version=<v>".
Frozen artifact = multi-cell grid run; per-cell run has a different "grid:"
header line, so whole-file comparison is not applicable.
"""
from __future__ import annotations

import sys
from pathlib import Path


def read_lines(path: Path) -> list[str]:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read().split("\n")


def report_block(lines: list[str], version: str) -> list[str]:
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"# cell: spec_version={version} "):
            start = i
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("# cell: spec_version="):
            end = j
            break
    block = lines[start:end]
    while block and block[-1] == "":
        block.pop()
    return block


def main() -> int:
    cell_dir = Path(sys.argv[1])
    frozen_prefix = Path(sys.argv[2])
    stem = None
    for p in cell_dir.iterdir():
        if p.name.endswith(".csv") and "_journal_" not in p.name:
            stem = p.name[: -len(".csv")]
    if stem is None:
        print("NO CELL CSV FOUND")
        return 2
    cell_csv = read_lines(cell_dir / f"{stem}.csv")
    frozen_csv = read_lines(frozen_prefix.with_suffix(".csv"))
    data = [ln for ln in cell_csv if ln.startswith(("pessimistic,", "optimistic,"))]
    if len(data) != 2:
        print(f"UNEXPECTED per-cell data rows: {len(data)}")
        return 2
    version = data[0].split(",")[1]
    frozen_rows = [ln for ln in frozen_csv if ln.startswith(("pessimistic,", "optimistic,"))]
    frozen_match = [ln for ln in frozen_rows if ln.split(",")[1] == version]
    ok = True
    if frozen_match == data:
        print(f"CSV rows: IDENTICAL (spec_version={version}, 2 fork rows)")
    else:
        ok = False
        print(f"CSV rows: DIFF (spec_version={version})")
        print("  cell  :", data)
        print("  frozen:", frozen_match)
    for suffix in ("_journal_p.csv", "_journal_o.csv"):
        cj = read_lines(cell_dir / f"{stem}{suffix}")
        fj = read_lines((frozen_prefix.parent / f"{frozen_prefix.name}{suffix}"))
        c_rows = [ln for ln in cj if ln and not ln.startswith("spec_version,")]
        f_rows = [ln for ln in fj if ln and ln.split(",")[0] == version]
        if c_rows == f_rows:
            print(f"{suffix}: IDENTICAL ({len(c_rows)} rows)")
        else:
            ok = False
            print(f"{suffix}: DIFF (cell {len(c_rows)} rows vs frozen {len(f_rows)} rows)")
            for a, b2 in zip(c_rows, f_rows):
                if a != b2:
                    print("  cell  :", a)
                    print("  frozen:", b2)
    rp = read_lines(cell_dir / f"{stem}_report_p.txt")
    fr = read_lines(frozen_prefix.parent / f"{frozen_prefix.name}_report_p.txt")
    cb = report_block(rp, version)
    fb = report_block(fr, version)
    if cb and cb == fb:
        print(f"report_p block: IDENTICAL ({len(cb)} lines)")
    else:
        ok = False
        print(f"report_p block: DIFF (cell {len(cb)} lines vs frozen {len(fb)} lines)")
        for a, b2 in zip(cb, fb):
            if a != b2:
                print("  cell  :", a)
                print("  frozen:", b2)
    print("VERDICT:", "IDENTICAL" if ok else "DIFF")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
