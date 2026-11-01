# Task 2 report: `SideMetrics` и отношения в `Metrics`

**Status:** DONE
**Commit:** `50e9504` — `feat: split trade metrics by position side`
**Branch:** `spec01-two-sided-momentum` (not pushed, as instructed)

## What was implemented

`strategylab/core/metrics.py`:

- Module-level ratio functions — the single source of ratio logic, per the brief:
  - `profit_factor_of(pnls: Sequence[Decimal], losses: int) -> Decimal | None` — moved verbatim
    from `Metrics.profit_factor` body; `None` when `losses == 0` (global constraint 8: no infinity,
    Decimal only, no `Decimal("Infinity")`).
  - `win_rate_of(wins: int, trades: int) -> Decimal | None` — moved verbatim from `Metrics.win_rate`;
    `None` when `trades == 0`.
- `SideMetrics` — mutable `@dataclass(slots=True)`: `trades/wins/losses: int = 0`,
  `net_pnl: Decimal = Decimal("0")`, private `_pnls: list[Decimal] = field(default_factory=list, repr=False)`.
  Methods: `record(pnl)` (counts trade/win/loss, accumulates net_pnl and `_pnls`), `profit_factor()`,
  `win_rate()` — both delegate to the module functions, no duplicated formulas.
- `Metrics`:
  - new fields `long: SideMetrics` / `short: SideMetrics` with `field(default_factory=SideMetrics)`
    (placed after public fields, before `_peak`/`_pnls`).
  - `record(self, fill: Fill, pnl: Decimal, side: Side | None = None) -> None` — keeps the entire old
    body (trades/wins/losses/net_pnl/equity/peak/drawdown) byte-for-byte, then, if `side is not None`,
    routes `pnl` to the bucket: `bucket = self.long if side is Side.BUY else self.short`.
    Chose the plain-ternary form over the sketch's `getattr(...).record(pnl)` — same behavior, type-checks
    and reads cleaner.
  - `profit_factor()` / `win_rate()` now delegate to `profit_factor_of(self._pnls, self.losses)` /
    `win_rate_of(self.wins, self.trades)` — old docstrings kept.
  - `record` got a short docstring pinning the trap named in the brief: third argument is the side of
    the **closed position**, opposite of the fill's side.

`tests/test_metrics.py`:

- Added `from dataclasses import replace` and helper `def _fill(side: Side) -> Fill: return replace(make_fill(), side=side)`
  — wraps `make_fill()`, which was left untouched, per the brief.
- Added the three tests from Steps 1–3 verbatim (including inline Russian comments):
  `test_record_splits_by_side`, `test_side_profit_factor_is_none_without_losses`,
  `test_record_without_side_keeps_totals_only`.

## TDD evidence

### RED (before implementation)

```
.venv\Scripts\python.exe -m pytest tests/test_metrics.py -v
```

3 new tests failed with exactly the errors the brief predicted (Step 4: TypeError from the 2-arg
`record` signature and AttributeError from the missing `long` field):

```
E   TypeError: Metrics.record() takes 3 positional arguments but 4 were given   (test_record_splits_by_side,
                                                                              test_side_profit_factor_is_none_without_losses)
E   AttributeError: 'Metrics' object has no attribute 'long'                    (test_record_without_side_keeps_totals_only)
======================== 3 failed, 14 passed in 0.43s ==========================
```

The 14 pre-existing tests stayed passing at every point — the old 2-arg call contract was never broken.

### GREEN (after implementation)

```
.venv\Scripts\python.exe -m pytest tests/test_metrics.py -v  -> 17 passed in 0.14s
.venv\Scripts\python.exe -m pytest -q                        -> 366 passed, 3 skipped in 5.88s
.venv\Scripts\python.exe -m mypy strategylab                 -> Success: no issues found in 33 source files
.venv\Scripts\python.exe -m ruff check .                     -> All checks passed!
```

366 = baseline 363 + 3 new; the 3 skips are the pre-existing network-marked tests.

## Files changed

- `strategylab/core/metrics.py` (+~55/-10)
- `tests/test_metrics.py` (+~38)

## Self-review findings

- **Call-site safety:** grepped the package — only external constructor call is `Metrics(equity=capital)`
  in `broker/fork.py` (keyword-only, unaffected by field insertion); engine calls `metrics.record(fill, realized)`
  2-arg in `core/engine.py` (contract preserved). `report/report.py` explicitly avoids `asdict/vars`, so the new
  fields do not leak into existing serialization (report/gate tasks handle surfacing them).
- **Zero-pnl symmetry:** `SideMetrics.record` treats `pnl == 0` as a trade but neither win nor loss — same
  semantics as `Metrics.record`, verified by the existing `test_zero_pnl_counts_as_neither_win_nor_loss` behavior.
- **Naming/YAGNI:** no extra accumulators, no `to_text` changes, no premature `side` attribution in the engine —
  exactly the brief's interface. `bucket` chosen over `getattr` for type safety (mypy strict).
- **Output pristine:** no warnings from pytest/mypy/ruff. Git printed a benign `LF will be replaced by CRLF`
  autocrlf warning on metrics.py; content committed correctly.

## Issues / concerns

- None blocking. Note for the controller: the prompt's report path contains Cyrillic `егого`; the real profile
  directory is `egoro` (brief was read from that path successfully), so this report lives at the `egoro` path.
  Untracked `strategylab.egg-info/` was left alone (pre-existing, not in this task's file list).
