# Task 4 report: Отчёт печатает долг и шорт раздельно

**Commit:** `d785150` — `feat: report publishes long and short metrics separately`
**Branch:** `spec01-two-sided-momentum`

## What was implemented

`Report` now publishes per-side metrics as a fixed contract, in the exact order
`long_trades, long_net_pnl, long_profit_factor, short_trades, short_net_pnl,
short_profit_factor`:

- `to_text()`: six new lines appended after `sharpe`, reading `Metrics.long` /
  `Metrics.short` (`SideMetrics`). `net_pnl` via `_plain`; `profit_factor()` via
  `_plain_or_null`, so `None` (no losing trade on the side — or zero trades) prints
  `null` (invariant 8; no `Infinity` anywhere).
- `to_json()`: same six keys appended at the end of the payload dict, `None` via
  `_optional_plain` → JSON `null`. Existing header/metric lines untouched — the
  diff is purely additive.

Tests (TDD, brief order):

- **Step 1:** `make_report_with_metrics` extended with `long_pnls: Sequence[Decimal] = ()`
  and `short_pnls: Sequence[Decimal] = ()`, recorded AFTER the existing loops via the
  same `metrics.record(fill, pnl, Side.BUY)` / `Side.SELL` mechanism — no direct
  `SideMetrics` field mutation, so the sum invariant and honest PF hold. All existing
  call sites unchanged (defaults are empty tuples). New test
  `test_report_publishes_sides_separately` copied verbatim from the brief.
- **Step 2:** `test_json_keys_include_sides_after_sharpe` — pins the last six JSON
  keys verbatim from the brief (order is part of the contract, not incidental).
- **Step 5:** whole-text pins updated (expectations changed, format not):
  - `test_to_text_includes_the_ratio_metrics_with_the_json_values`: `lines[-3:]` →
    `lines[-9:]`, six new lines `long_*/short_*: 0 / null` (fixture has no side args).
  - `test_json_key_order_is_fixed`: six keys appended after `"sharpe"`.
  - `test_to_text_puts_header_in_first_five_lines`: `len(lines) == 14` → `20`, plus
    pins for `lines[14..19]` (the test pins every line; leaving the six unpinned
    would be inconsistent with its own contract).
  - `test_run_prints_both_reports_and_verdict` (test_cli.py): the six literal lines
    from the brief inserted after `sharpe: null` in BOTH the PESSIMISTIC and the
    OPTIMISTIC block. The values (`long_trades: 1`, `long_net_pnl: -127.083013460`,
    `long_profit_factor: 0`, `short_trades: 0`, `short_net_pnl: 0`,
    `short_profit_factor: null`) came from the brief and passed against real CLI
    rendering — the engine's side attribution is exercised end-to-end.
  - `test_profit_factor_serialises_as_null` — checked: it uses substring/payload-key
    asserts, not full-text comparison; unchanged, still green.

## TDD Evidence

**RED** — `pytest tests/test_report.py -v` after Steps 1–2, before implementation:

```
FAILED tests/test_report.py::test_report_publishes_sides_separately - Assertio...
FAILED tests/test_report.py::test_json_keys_include_sides_after_sharpe - Assert...
======================== 2 failed, 11 passed in 0.64s =========================
```

Why expected: the failure output shows `to_text()` ending at `sharpe: ...` with no
`long_trades`/`long_net_pnl` lines present (fixture correctly aggregated trades: 3,
wins: 1, losses: 2 — only the side publication was missing), and `list(payload)[-6:]`
still `['net_pnl', ..., 'sharpe']`. All 11 pre-existing tests stayed green, so the
helper extension broke nothing.

**GREEN** — after Step 4 implementation + Step 5 pin updates:

```
pytest tests/test_report.py tests/test_cli.py -v  →  27 passed in 1.68s
```

**Full suite + gates before commit:**

```
pytest                →  370 passed, 3 skipped in 5.64s   (baseline 368 + 3 skipped; +2 new tests)
mypy strategylab      →  Success: no issues found in 33 source files
ruff check .          →  All checks passed!
```

## Files changed

- `strategylab/report/report.py` (+12)
- `tests/test_report.py` (+51 / −4)
- `tests/test_cli.py` (+20 / −6) — included per controller ruling R1

## Self-review findings

- Diff reviewed line by line: purely additive in report.py; no existing line reordered
  or reformatted; `_plain`/`_plain_or_null`/`_optional_plain` reused, no new formatting
  helpers; all lines < 100 chars.
- Both forks of the CLI pin updated identically (two insertions, verified in diff).
- Sweep CSV (`scripts/sweep.py`) builds rows from `report.metrics` directly, not from
  `to_json` — unaffected by the payload growth; its test stayed green untouched.

## Concerns

1. **Scope beyond the brief's named pins (all expectation-only, covered by R1's
   test_cli.py allowance):** besides the two named whole-text pins, three other
   positional stdout pins had to shift because every block grew by 6 lines:
   `test_run_parses_strategy_params_as_decimal_strings` (`lines[14:18]→[20:24]`,
   `[18]→[24]`, `[19]→[25]`, verdict `[28]→[40]`), and `lines[19]→lines[25]` in
   `test_run_trades_when_capital_is_given`, `test_run_without_capital_trades_by_default`,
   `test_run_does_not_trade_with_zero_capital`. If the controller wants fewer touched
   tests, these could instead be rewritten to locate lines by content — they are not.
2. The side lines are NOT yet in the sweep CSV — spec gate 5 only requires the report;
   flagging in case a later task expects them there.
