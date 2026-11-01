# Task 5 report: Брокер вытесняет устаревший зеркальный стоп

**Status:** DONE
**Commit:** `8b303a7` — `fix: superseded mirrored stops leave the pending queue`
**Branch:** `spec01-two-sided-momentum` (not pushed, as instructed)

## What was implemented

In `strategylab/broker/sim.py`, method `_mirror_reduced`: the STOP_MARKET branch now
filters `self._pending`, removing resting orders matching the dedup key
`reduce_only and kind is OrderKind.STOP_MARKET and side is order.side`. The call site
in `submit` runs `_mirror_reduced` **before** `self._pending.append(order)`, so the new
order survives and only the stale mirror of the same side leaves the queue. The LIMIT
branch is untouched — partial take-profit targets are intentionally not deduplicated
(the `sweep_liquidity` live branch relies on resting targets surviving).

The method docstring now explains WHY: the mirrored level is already rewritten into
the position, a stale resting reduce-only stop can never fill (`_apply_stops` skips
reduce-only orders) and only pays for the `_pending` traversal in `apply_bar` —
without this, repeated stop updates degrade the run quadratically.

## Tests

Added to `tests/test_broker_sim.py`, both using the file's existing construction and
import style (`BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)`, positional
`Order(Side, OrderKind, qty, limit_price, stop_price, reduce_only)`, `_bar` 5-tuple):

1. `test_second_stop_submit_replaces_the_resting_mirror` — brief Step 1, verbatim.
   After two reduce-only STOP_MARKET SELL orders (95 then 98): exactly one STOP_MARKET
   in `pending_orders` with `stop_price == Decimal("98")`, and
   `b.position.stop_price == Decimal("98")`.
2. `test_limit_mirror_is_not_deduplicated` — brief Step 2 sketch constructed per the
   stated intent: two reduce-only LIMIT SELL orders differing in qty (0.4 @ 105,
   0.6 @ 110) must BOTH remain resting. The brief's assertion line kept verbatim:
   `assert len([o for o in b.pending_orders if o.kind is OrderKind.LIMIT]) == 2`.

## TDD Evidence

### RED

Command: `.venv\Scripts\python.exe -m pytest tests/test_broker_sim.py -v`

```
tests/test_broker_sim.py::test_second_stop_submit_replaces_the_resting_mirror FAILED [ 98%]
tests/test_broker_sim.py::test_limit_mirror_is_not_deduplicated PASSED   [100%]

    stops = [o for o in b.pending_orders if o.kind is OrderKind.STOP_MARKET]
>   assert len(stops) == 1 and stops[0].stop_price == Decimal("98")
E   AssertionError: assert (2 == 1)
E    +  where 2 = len([Order(... stop_price=Decimal('95'), reduce_only=True),
                        Order(... stop_price=Decimal('98'), reduce_only=True)])

======================== 1 failed, 55 passed in 0.54s =========================
```

Expected: without the filter both stops stay in `_pending` → `2 == 1` fails, matching
the brief's expected `assert 2 == 1`. The LIMIT test passed immediately — by design:
it pins existing behavior that must not regress (controller note confirmed this is
the expected RED shape).

### GREEN

Command: `.venv\Scripts\python.exe -m pytest tests/test_broker_sim.py -v`

```
tests/test_broker_sim.py::test_second_stop_submit_replaces_the_resting_mirror PASSED [ 98%]
tests/test_broker_sim.py::test_limit_mirror_is_not_deduplicated PASSED   [100%]
============================= 56 passed in 0.24s ==============================
```

Full suite: `.venv\Scripts\python.exe -m pytest -q`

```
372 passed, 3 skipped in 6.23s
```

Baseline 370 passed + 2 new = 372, 3 skipped unchanged (the brief's "363 + новые"
was stale, as the controller flagged). Sweep-liquidity regression check:
`.venv\Scripts\python.exe -m pytest tests/test_strategy_sweep_liquidity.py -q`
→ `35 passed` (partial take-profits still rest).

Static checks:
- `.venv\Scripts\python.exe -m mypy strategylab` → `Success: no issues found in 33 source files`
- `.venv\Scripts\python.exe -m ruff check .` → `All checks passed!`

## Files changed

- `strategylab/broker/sim.py` (+15): dedup filter + docstring WHY in `_mirror_reduced`
- `tests/test_broker_sim.py` (+21): two new tests

Both committed together in `8b303a7`; nothing else staged (`strategylab.egg-info/`
left untracked, untouched).

## Self-review findings

- Filter placement: `_mirror_reduced` runs before `append` in `submit` — verified by
  reading the call site; new order survives (test asserts the 98 stop remains).
- Dedup key is exactly `reduce_only and kind is STOP_MARKET and side is order.side`
  — matches the brief's Interfaces contract. `is` comparison for `Side` enum members
  matches surrounding style (`_cancel_reduced`, `submit` guards).
- LIMIT branch untouched; the docstring also states why LIMITs are not deduplicated.
- Tests verify real broker state through the public `pending_orders` / `position`
  properties, not internals.
- Diff is pure insertions (36+, 0−): existing lines unchanged, lowest regression risk.

## Concerns

None. One note for downstream tasks: dedup by side means a same-side reduce-only
STOP_MARKET always replaces the resting one even if prices are equal — which is the
correct "exactly one resting stop per side" contract the trailing-stop task needs.
