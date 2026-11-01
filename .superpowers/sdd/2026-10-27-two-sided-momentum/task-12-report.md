# Task 12 report — Развёртка 18 комбинаций, гейты и отчёт

Branch `spec01-two-sided-momentum`. Commit: `77a7c7d feat: gate evaluation for two-sided momentum and spec 01 report`
(3 files: `scripts/gates.py`, `tests/test_gates.py`, `strategydocs/отчёт_spec_01_two_sided_momentum_20261027.md`).
Step 1 was controller pre-work; skipped as instructed.

## Steps 2–4: sweeps + mechanical checks

All commands exactly as in the brief, run with `$env:PYTHONUTF8="1"` and
`| Out-File -Encoding utf8 data\<name>.csv`; stderr NOT redirected (would show in console).

| # | command (essence) | elapsed | rows | zones/touches/intrabar header | stderr |
|---|---|---|---|---|---|
| 1 | `scripts\sweep.py data\btcusdt.db BTCUSDT 1761523200 1790294400 two_sided_momentum --param donch_len=24,48,96 --param atr_mult=1.0,1.5,2.0 --param bias_len=0,48` | 332.62 s (Measure-Command; CSV tail `elapsed: 332.08s`) | **18** | `zones_used: fit`, `touches: holdout=0, live=0`, `intrabar: PESSIMISTIC` | none, no `отклонено`; tail `комбинаций: 18` |
| 2 | same for `data\ethusdt.db ETHUSDT 1761523200 1790294400` | 339.17 s (tail `338.62s`) | **18** | identical: fit / holdout=0, live=0 / PESSIMISTIC | none; `комбинаций: 18` |
| 3 | fold 1: `sweep.py ethusdt.db ETHUSDT 1761523200 1771113600 … --param donch_len=48 --param atr_mult=1.5 --param bias_len=48` | 5.86 s | **1** | fit / 0,0 / PESSIMISTIC | none; `комбинаций: 1` |
| 4 | fold 2: window 1771113600→1780704000, same params | 5.80 s | **1** | fit / 0,0 / PESSIMISTIC | none |
| 5 | fold 3: window 1780704000→1790294400, same params | 5.87 s | **1** | fit / 0,0 / PESSIMISTIC | none |

Row counting was mechanical, never eyeballed: python one-liner helper
(`C:\Users\egoro\AppData\Local\Temp\opencode\check_sweep.py`) opening each file
`encoding="utf-8-sig"`, counting non-empty lines after the `spec_version,` line until
the first empty line.

Design note for folds: ran them with explicit single-value `--param donch_len=48 --param atr_mult=1.5 --param bias_len=48`
(instead of an empty grid) so the fold CSVs carry the three named param columns the gates
contract requires to identify the default row by column values.

Touch guard: **`data\touch_registry.json` absent through everything** — verified `Test-Path → False`
before runs, after BTC grid, after ETH grid, after folds, after gates.py runs, after commit.
All ten touch-count readings are `holdout=0, live=0`. No Global Constraint 12 violation.

CSVs live in `data/` which is git-ignored (`data/` line in .gitignore; `git check-ignore` confirms);
never staged. `gates_stdout.txt`, `spread_btc.txt`, `spread_eth.txt` also captured into `data/` (untracked).

Total grid wall time ≈ 11 min vs the ~13–15 min estimate — within bounds, no stop condition triggered.

## Steps 5–6: gates.py via TDD

**RED:** `tests/test_gates.py` written first (19 tests). `pytest tests\test_gates.py -q` →
collection error `FileNotFoundError: …\scripts\gates.py` — 0 passed, all blocked. Evidence of correct failing state.

**GREEN:** implemented `scripts/gates.py`; first run 14 passed / 5 failed — all five failures were
test-fixture bugs, not implementation bugs: (a) four tests wrote their variant CSV to `btc.csv` *before*
`_passing_fixture()` re-wrote the same path (fixed: fixture first, variants under distinct filenames);
(b) the gate-5 zero-side fixture broke gate 1 too (long 0 + short 40 = 40 → verdict legitimately flipped;
fixed with `short_trades=100`). Also a test-loader fix: register the importlib-loaded module in
`sys.modules` before `exec_module`, required by `@dataclass` under `from __future__ import annotations`
on Python 3.14 (documented in the test docstring). Final: **19 passed**.

Trap cases pinned by the suite (as mandated): null-ranks-above-numbers changes the median vs zero/drop
(unit + evaluate level: 9 nulls + 9×0.5 → median ∞ → гейт пройден); <30 → `расширять спек` vs 30–99 →
`не пройден` (29 vs 30 exact boundaries, 99 vs 100); default row found by the three named columns with
the grid rotated (row order irrelevant, `1.2` surfaces in гейт 3 while median stays 1.5); duplicate/missing
default row → ValueError; BOM-prefixed file parses; `комбинаций/отклонено/elapsed` tail after the empty
line never becomes rows; `profit_factor null → None` not 0; header without `intrabar: PESSIMISTIC` → error;
gate-5 `одно-система` annotation and zero-side failure never flip the `ГЕЙТЫ:` verdict line; CLI contract
`--btc/--eth/--folds` prints table + verdict.

### gates.py design

- Pure stdlib (argparse, csv, dataclasses, decimal); **no strategylab imports, no DB, no registry writes**.
- `DEFAULT_COMBO = {"donch_len": Decimal("48"), "atr_mult": Decimal("1.5"), "bias_len": Decimal("48")}` module
  constant; `find_default_row()` matches by the three CSV columns' values (exactly one match required).
- Parser per the sweep.py stdout contract: `utf-8-sig`, skip lines until `spec_version,`, require
  `intrabar: PESSIMISTIC` in the header, first empty line ends the table; everything Decimal; nulls → None.
- `median_pf()`: None ranks above every number; odd n → middle element; even n → mean of the two middle
  values, and if either middle position is None the median is infinite (returned None, printed `∞`).
  Null-row counts always printed on a dedicated line (`null-рядов: BTC=…, ETH=…`).
- Gate 1 per-instrument three-way outcome (`≥100` / `30–99` / `<30` strings above), computed on default
  row `long_trades + short_trades`, required on BTC AND ETH. Gate 2: median PF ≥ 1.15 on both instruments'
  18-row grids. Gate 3: default PF ≥ 1.3 both instruments; None = ∞ = pass (documented in module docstring).
  Gate 4: ≥2 of 3 fold default-row `net_pnl > 0`. Gate 5: both sides `trades ≥ 1` else «не опубликована»;
  `одно-система` annotation iff one side `net_pnl ≤ 0` and the other is `> 0` (holds all net profit);
  gate 5 line is labeled «в вердикт не входит» and excluded from the verdict boolean (gates 1–4 only).

### Real gates run (Step 6), full stdout (byte-identical before/after the ruff E501 refactor)

```
строки сетки: BTC=18, ETH=18
гейт 1 (≥100 сделок на инструмент, long+short дефолта): BTC: 905 — пройден; ETH: 802 — пройден
гейт 2 (медианный PF сетки ≥ 1.15 на каждом инструменте): BTC: 0.3992907605541383151920188701 — не пройден; ETH: 0.4513777252148865231215316951 — не пройден
null-рядов: BTC=0, ETH=0 (PF=null — ни одной убыточной сделки, в медиане выше любого числа)
гейт 3 (PF дефолтной комбинации ≥ 1.3): BTC: 0.4014469398734172712187715749 — не пройден; ETH: 0.4443193303697570630578935551 — не пройден
гейт 4 (≥2 из 3 ETH-фолдов с net_pnl > 0): фолд1: -6250.646053630254496394382650, фолд2: -7049.928536826043523483533541, фолд3: -6778.891134796426656618597000 — не пройдено (0 из 3)
гейт 5 (раздельность long/short опубликована; в вердикт не входит): BTC: долг 448 / шорт 457 — опубликована; ETH: долг 411 / шорт 391 — опубликована
ГЕЙТЫ: не пройдены
```

## Step 7: spread width (cli run, both forks, default combo, full fit interval)

BTCUSDT (`spec_version: 3b64a6527077`): pessimistic net_pnl `-9921.984938580256861831677647`,
optimistic net_pnl `-9921.984938580256861831677647`, 905 trades each — forks identical, `ВЕРДИКТ: измерима`.
ETHUSDT (`spec_version: 3b6c86fce9ac`): pessimistic `-9649.268006566539404816100970`,
optimistic `-9649.268006566539404816100970`, 802 trades each — identical, `ВЕРДИКТ: измерима`.
Cross-check: identical to the pessimistic CSV default rows — coherent. Recorded in the report as a fact,
not a robustness conclusion (brief line 68).

## Step 8: report

`strategydocs/отчёт_spec_01_two_sided_momentum_20261027.md` — exact filename (date per plan, not "fixed").
Contains: header table of all five развёртки (windows, row counts, base spec_versions, zones, touches, intrabar,
elapsed), cost model line, the full 18-combo tables for BTC and ETH (generated from the CSVs by a throwaway
script — cells copied at source precision, never retyped by hand), median + central PF with exact Decimals and
the two middle values that form each median, 3-fold ETH table at full Decimal precision plus long/short columns,
long/short breakdown of the defaults, spread table, the verbatim gates stdout block, and the gate summary table.
Final verdict section (pasted):

```
## Вердикт

**ГЕЙТЫ: не пройдены.** Сработала заранее зарегистрированная в спеке вилка провала («медианный PF сетки < 1.15 на BTC и ETH»):

> 2025-10..2026-10 не торговать классику вообще; следующий шаг — не подгон параметров, а смена класса идей.

Holdout и live не тронуты — касаний ноль (`touches: holdout=0, live=0` во всех пяти развёртках), `data/touch_registry.json` отсутствует. Холдout-прогон по 18 суткам `[1790294400, 1791849600)` не выполняется: гейты не пройдены, и по спеке следующий шаг — смена класса идей, а не новое окно.
```

No reinterpretation, no hedging, no "если бы"; verdict recorded exactly as computed, pre-registered FAILURE
conclusion verbatim.

## Steps 9–10: verification

- `pytest -q`: **416 passed, 3 skipped** (baseline 397/3 + 19 new gate tests).
- `ruff check .`: All checks passed (8 initial E501 in gates.py fixed; output verified byte-identical after).
- `mypy strategylab`: Success, 34 source files.
- Commit `77a7c7d`: exactly the three intended files (verified via `git status --short` before/after staging);
  CSVs + captures in `data/` remain untracked/ignored; `strategylab.egg-info/` left untracked as before.
- No push, no branch switch.

## Self-review

- Every number in the report traces to a CSV cell or captured stdout; the tables were machine-generated from
  the CSVs through `gates.parse_sweep`, so the report and the gates read the same parser contract.
- Verdict strings in the report are the captured gates stdout, unmodified.
- Registry absence re-verified after every phase (final `Test-Path → False`).
- Gates 1–4 = sole verdict input; gate 5 labeled separately, flipped nothing — matches spec honesty rule.

## Concerns / notes for controller

1. Fold CSVs were produced with explicit single-value `--param` args (see design note) — deviation from a
   possible empty-grid reading of Step 4, chosen deliberately because the gates contract identifies the default
   row by the three named columns, which an empty grid omits from the CSV entirely.
2. The recurring Cyrillic-homoglyph path (`C:\Users\егого\…`) fired three more times this session (shell/edit
   calls); each failed loudly and was corrected immediately — nothing was written to the wrong location.
3. Git warned LF→CRLF on the three committed files (autocrlf); cosmetic, contents committed as-is.
4. Median PF ≈ 0.40/0.45 on both instruments with 0/3 folds positive: the pre-registered failure branch is
   now the spec's stated next step (смена класса идей); holdout window remains untouched and spendable.
