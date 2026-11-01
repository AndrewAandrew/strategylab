# Task 3 report: Движок передаёт сторону закрываемой позиции

Branch: `spec01-two-sided-momentum` · Commit: `8f52506 feat: engine attributes realized pnl to position side` · Status: DONE_WITH_CONCERNS

## What I implemented

The engine now attributes every realized trade to the bucket of the **closed position's** side, so the
report can publish long/short separately (spec gate 5). Exactly two call sites changed in
`strategylab/core/engine.py`; nothing else in the engine was touched — call order and the pnl formula are
byte-identical to before.

1. `_record_realized` already received `before_side` (the snapshot taken in `Engine.run` before
   `broker.apply_bar`). It is now passed through: `metrics.record(fill, realized, before_side)`.
   For a flip this is correct by construction: only the closed part is realized, and it is realized
   against the side the position had, not the side of the filling order.
2. `_close_window` reads `side_before_close = position.side` **before** `position.apply_fill(fill)` and
   passes it to `metrics.record`. This matters: the closing fill of a short position is a `Side.BUY`
   fill, so taking the fill's side would silently turn every window-closed short into a long trade.

Tests (`tests/test_engine_trades.py`, brief's code verbatim, reusing the file's own `_run` / `_market`
helpers, so both go through a real `Engine().run` over `ListFeed(_bars(5))` with the PESSIMISTIC fork):

- `test_short_trade_lands_in_short_bucket` — sell to open, buy to cover; also asserts the sum invariant
  `long.net_pnl + short.net_pnl == net_pnl` required by brief Step 6.
- `test_window_close_is_bucketed_by_position_side` — open a short and do nothing else; the engine flattens
  it at the last bar, which exercises the `_close_window` call site.

## TDD evidence

### RED — Step 3

Command: `.venv\Scripts\python.exe -m pytest tests/test_engine_trades.py -v`

```
tests/test_engine_trades.py::test_short_trade_lands_in_short_bucket FAILED [ 87%]
tests/test_engine_trades.py::test_window_close_is_bucketed_by_position_side FAILED [100%]
================================== FAILURES ===================================
___________________ test_short_trade_lands_in_short_bucket ____________________
    metrics = _run({0: [_market(Side.SELL, "1")], 2: [_market(Side.BUY, "1")]}, None)
>   assert metrics.short.trades == 1
E   AssertionError: assert 0 == 1
E    +  where 0 = SideMetrics(trades=0, wins=0, losses=0, net_pnl=Decimal('0')).trades
E    +    where ... = Metrics(trades=1, wins=0, losses=1, net_pnl=Decimal('-3777960.26403600'), ...,
E    +          long=SideMetrics(trades=0, ...), short=SideMetrics(trades=0, ...)).short

_______________ test_window_close_is_bucketed_by_position_side ________________
>   assert metrics.short.trades == 1 and metrics.long.trades == 0
E   AssertionError: assert (0 == 1)
========================= 2 failed, 6 passed in 0.44s =========================
```

Failure is exactly the predicted one — `assert 0 == 1` on `metrics.short.trades`. Expected, and it proves
the right thing: the totals were already populated (`trades=1`, non-zero `net_pnl`) while both buckets sat
empty, i.e. the engine was calling `metrics.record(fill, realized)` without the side. The 6 pre-existing
tests in the file stayed green, so the RED is caused by the missing attribution only.

### GREEN — Step 5

Command: `.venv\Scripts\python.exe -m pytest tests/test_engine_trades.py tests/test_engine.py -v`

```
tests/test_engine_trades.py::test_short_trade_lands_in_short_bucket PASSED [ 50%]
tests/test_engine_trades.py::test_window_close_is_bucketed_by_position_side PASSED [ 57%]
tests/test_engine.py::test_flip_realizes_only_the_closed_part PASSED     [ 85%]
tests/test_engine.py::test_open_position_is_closed_at_the_end_of_the_window PASSED [ 92%]
============================= 14 passed in 0.33s ==============================
```

### Full suite — Step 6

Command: `.venv\Scripts\python.exe -m pytest -q`

```
368 passed, 3 skipped in 5.53s
```

Baseline was 366 passed / 3 skipped; +2 = the two new tests, no existing test changed its mind. In
particular `test_recording_does_not_change_metrics` and the fork-report tests still pass, confirming the
journal and the metrics are unaffected by the bucketing.

### Gates — Step 13

- `.venv\Scripts\python.exe -m mypy strategylab` → `Success: no issues found in 33 source files`
- `.venv\Scripts\python.exe -m ruff check .` → `All checks passed!`

## Files changed

- `strategylab/core/engine.py` (+5 / −2): side passed at `_record_realized`, side captured before
  `apply_fill` in `_close_window`, two one-line comments.
- `tests/test_engine_trades.py` (+18): two tests plus one section comment.

Committed with `git add strategylab/core/engine.py tests/test_engine_trades.py` and the exact message
`feat: engine attributes realized pnl to position side`. Pre-existing untracked `strategylab.egg-info/`
was deliberately left out. Nothing pushed, no branch switch.

## Self-review

- **Minimal diff.** `git diff` shows only the two `metrics.record` calls and the `side_before_close`
  capture; no reordering, no formula edit, no new parameters on engine functions.
- **Naming.** Reuses the existing snapshot vocabulary — `before_side` was already the engine's name for
  this value; `side_before_close` matches the `before_*` prefix used in `Engine.run`. No new types, no
  new fixtures, no sqlite machinery in the new tests.
- **Tests verify real behavior.** Both run a real `Engine().run` through `_run`; nothing hand-builds a
  `Metrics`, and no bucket is written by the test itself.
- **YAGNI.** I did not add per-side drawdown, per-side Sharpe, or an accessor that returns the right
  bucket — nothing in this task asks for them.
- **Flip case checked out-of-band.** A throwaway script (temp dir, deleted, never committed) ran
  BUY 1 → SELL 4 (long flipped to short) and SELL 2 → BUY 5: both gave `trades=2, long.trades=1,
  short.trades=1` with `long.net_pnl + short.net_pnl == net_pnl`. The closed part lands in the bucket of
  the position that was closed, and the resulting short goes to `short` when the window sweeps it.
  Output was pristine — no warnings, no stray prints from the engine.

## Concerns

1. **Pre-existing, out of scope, not fixed:** `_close_window` ends with
   `trade_log.close(fill, position.entry_price, ...)` **after** `position.apply_fill(fill)`, and
   `Position.apply_fill` zeroes `entry_price` on an exact flatten (`core/position.py:65-72`). Probed with a
   throwaway run: a window-closed short logs `entry_ts` correctly (TradeLog tracks it itself) but
   `entry_price: 0` instead of the real entry. pnl and the new side bucketing are unaffected (both computed
   before the fill), and no test asserts on that field, which is why the suite is green. I left it alone
   because the brief says "Никаких других изменений в движке нет". If the long/short report ever prints
   entry prices for window closes, this line is the one to fix — worth its own task.
2. The two new tests have no docstrings, unlike their neighbours in the same file, because the brief gives
   their bodies verbatim and I used them as written. Easy to add if style consistency is preferred over
   verbatim brief text.
3. `SideMetrics.wins/losses` are now populated by the engine too (side effect of `record`), but nothing
   reads them yet; they are exercised by Task 2's unit tests, so this is expected, not dead weight I added.
