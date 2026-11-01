# Task 10 report: Трейлинг стоп после входа

Commit: `72096bf feat: trailing atr stop on both sides` (branch `spec01-two-sided-momentum`, not pushed).

## What was implemented

`strategylab/strategy/two_sided_momentum.py`:

- New state: `_since_high`/`_since_low` (extremes since entry) and `_stop_level`
  (the strategy's own current-stop level; the engine never echoes the broker
  stop back). All three `Decimal | None`, `None` = no trailing.
- `_init_trailing(entry_price, stop)` — single place that (re)starts the
  trailing state.
- `_bracket_orders` calls `_init_trailing` in both non-flat branches at the
  moment the bracket order is emitted; the flat `else` branch (м16 guard) does
  not touch it.
- `_trailing_orders(bar15, state)` — called from `on_bar` only when a 15m bar
  closed, AFTER `_entry_order` (so on a reversal bar the freshly-set pending
  suppresses the trail — lifecycle exclusion per spec):
  - guards: `position == 0`, `_pending_entry is not None`, any trailing field
    or `_atr_value` `None` (mid-fill before bracket; before warmup boundary)
    → `[]`;
  - LONG: `_since_high = max(_since_high, bar15.high)` (extremes updated
    BEFORE the candidate — the closing bar's high belongs to «max High с
    входа»), `candidate = _since_high − atr_mult × self._atr_value` (LIVE ATR;
    frozen `_pending_atr` remains only the bracket width); if `candidate >
    _stop_level` → emit `Order(SELL, STOP_MARKET, position, None, candidate,
    True)` and move the level; else `[]` — equality does NOT re-submit (strict
    `>`), so a stop is never re-lowered/re-raised;
  - SHORT mirrors it from `_since_low` with `<` and BUY. reduce-only, full
    position size, never LIMIT.
- Flatten detection at the top of `on_bar`: `state.position == 0 and
  _pending_entry is None` → reset `_since_high/_since_low/_stop_level = None`
  (no dead state between legs; runs before entry/trailing logic of the same
  bar). Module + `on_bar` docstrings updated to describe trailing.

## Chosen initialization moment

Extremes and level are created together at **bracket emission on the fill
bar** (`_init_trailing` called from `_bracket_orders`): `_since_high =
_since_low = bar.close` (the same entry estimate the stop is measured from —
the engine gives neither fill price nor the resting stop back), `_stop_level =
emitted stop`. The entry's own 15m group closes later and its real high/low
fold into the extremes at that close, before any trail candidate is computed.
Fresh entries and reversal legs share this exact call, which is what makes R2
free.

## R2 wiring description

The reversal path is unchanged code: `_entry_order` sets `_pending_entry`, the
`filled` consumption in `on_bar` clears it and calls `_bracket_orders`, and
`_bracket_orders` now calls `_init_trailing`. So the flipped leg's bracket
re-initializes `_since_high/_since_low/_stop_level` at the same consumption
point — the new leg can never trail from the old leg's extremes/level. Trailing
is additionally muted while the reversal is pending (guard + call-after-entry).

## Exact bar-by-bar numbers (all TR = 0.5 or chosen so Wilder ATR stays pinned)

### `test_long_stop_trails_up_only` (atr_mult 2, donch 2, tape `_bars_then_breakout`)

| closed 15m bar | H / L / C | ATR after | since_high | candidate | level before | emitted |
|---|---|---|---|---|---|---|
| #18 signal | 100.5/100/100.5 | 0.5 (frozen 0.5) | — | — | — | BUY MARKET 100 |
| #19 fill: min0 100/100/100/100 (close 100), min1-14 100/100.5/100/100.5 → agg 100.5/100/100.5 | | 0.5 | init 100 → 100.5 | 100.5−1=99.5 | bracket 99 | min0: SELL STOP 100 @ **99**; close: SELL STOP 100 @ 99.5 |
| #20…#28 ladder, each 15×(prevC, prevC+0.5, prevC, prevC+0.5), +0.5/bar | #28: 105/104.5/105 | 0.5 (TR 0.5 each) | →105 | #28: 105−1=**104** | 103.5 | one SELL STOP 100/bar; #28 == `[Order(SELL, STOP_MARKET, Decimal("100"), None, Decimal("104"), True)]` (pinned) |
| #29 equality bar 15×(104.5, 105, 104.5, 105) | TR 0.5, close 105 == hh → no signal | 0.5 | stays 105 | 104 == level | 104 | `[]` (pinned; equality deliberately hit) |

Every climb bar is an up-breakout signal while +100 — blocked by the T9
same-direction rule, so the trailing order is the only emission per group.

### `test_short_stop_trails_down_only` (tape `_bars_then_breakdown`)

| closed 15m bar | H / L / C | ATR | since_low | candidate | level before | emitted |
|---|---|---|---|---|---|---|
| #18 signal | 100/99.5/99.5 | 0.5 frozen | — | — | — | SELL MARKET 100 |
| #19 fill min0 100×4 (close 100); min1-14 99.5/100/99.5/99.5 → agg 100/99.5/99.5 | | 0.5 | init 100 → 99.5 | 99.5+1=100.5 | bracket 101 | min0: BUY STOP 100 @ **101**; close: BUY STOP 100 @ 100.5 |
| #20…#28 ladder, each 15×(prevC, prevC, prevC−0.5, prevC−0.5) | #28: 95.5/95/95 | 0.5 | →95 | #28: 95+1=**96** | 96.5 | one BUY STOP 100/bar; #28 == `[Order(BUY, STOP_MARKET, Decimal("100"), None, Decimal("96"), True)]` (pinned) |
| #29 equality 15×(95, 95.5, 95, 95.5) | TR 0.5; close 95.5 > ll=95 → no signal | 0.5 | stays 95 | 96 == level | 96 | `[]` |

### `test_reversal_reinitialises_trailing_on_new_leg` (R2)

Long leg = the ladder above (asserts `_stop_level == 104`, `_since_high ==
105`). #29 = 15×(97.5, 104, 97.5, 97.5): agg 104/97.5/97.5, TR = |97.5−105| =
**7.5** → ATR = (13×0.5+7.5)/14 = **1.0 exactly**; close 97.5 < ll=104 →
reversal `SELL MARKET 150` (= 100 + 10000·0.01/(2·1.0)); trailing muted
(pending). Fill min0 of #30 = 97.5×4 with position −50 → bracket `BUY STOP 50
@ 99.5` (= 97.5 + 2×1.0) and **R2 re-init** (asserted `_since_high == 97.5`,
`_stop_level == 99.5`). #30 close (14×(97.5, 98.5, 97.5, 98) → agg 98.5/97.5/
98, TR 1.0, ATR stays 1.0): candidate = 97.5+2 = 99.5 == level → `[]` — with
the old level 104 this would wrongly have re-submitted 99.5; this assertion is
the R2 discriminator. #31 (15×(97.5, 98, 97, 97.5), TR 1.0): `_since_low` =
min(97.5, 97) = 97 → candidate 99 < 99.5 → `BUY STOP 50 @ 99` — trailing from
the NEW leg's extremes only.

## TDD evidence

RED (`pytest tests/test_strategy_two_sided_momentum.py -q` at base):
`4 failed, 15 passed` —
`test_long_stop_trails_up_only`, `test_short_stop_trails_down_only`,
`test_flatten_clears_trailing_state` (AttributeError: no `_stop_level`),
`test_reversal_reinitialises_trailing_on_new_leg`, each on `assert [] ==
[Order(...STOP_MARKET...)]` at the first trailing expectation (brackets of the
existing code pass through unchanged).

**Step 3 — truthful disclosure:** `test_short_trailing_stop_fills_on_gap_up`
PASSES already at base (it pins BrokerSimulator pessimistic gap handling, not
strategy code — same category as T5's LIMIT and T9's engine pins; first run
additionally caught my wrong signed-`qty` assertion since kernel `Position.qty`
is unsigned, fixed to the `(side, qty)` idiom before RED recording).
`test_ten_trailing_stop_resubmits_leave_one_resting_order` (Step 7) likewise
passes at base — it is Task 5's dedup exercised under a 10-step trailing
ladder. Both were kept as behavior pins; only the four strategy tests drove
the implementation.

GREEN: `19 passed` in the strategy file; full suite
`394 passed, 3 skipped` (baseline 388 + 6 new, within the predicted 392–394);
`mypy strategylab` → `Success: no issues found in 34 source files`;
`ruff check .` → `All checks passed!`.

## Files changed

- `strategylab/strategy/two_sided_momentum.py` — trailing state, `_init_trailing`,
  `_trailing_orders`, bracket-time init, flatten clear, docstrings.
- `tests/test_strategy_two_sided_momentum.py` — appended: `_group_orders`
  helper, `_trail_long_to_high_105` builder, 4 new tests. No earlier test
  touched (purely additive diff).
- `tests/test_broker_sim.py` — appended 2 tests only. No earlier test touched.

## Self-review findings

1. First draft asserted the gap test on `b.position.qty == Decimal("-1")` —
   kernel `Position` is unsigned + `side`; fixed before committing (RED/GREEN
   recorded after the fix).
2. Two garbled docstring phrases (`стратегию` case, a broken dedup clause)
   found and fixed post-implementation; gates re-run afterwards.
3. Verified: strict `>`/`<` never re-submits at equality (three equality-landing
   cases pinned); LIVE `_atr_value` in candidate vs FROZEN `_pending_atr` in
   bracket width; no trail while pending; `_stop_level` reset on flatten
   (no dead fields); no LIMIT orders anywhere; trailing never touches window/
   bias/Donchian state.
4. Intermediate ladder steps are exercised silently (return values asserted per
   group only where the brief pins them) — keeps exact asserts readable.

## Concerns

- The R2 discriminator is presence-based (`[]` vs a 99.5 re-submit); a
  hypothetical impl that initialized only fresh (flat-origin) entries is caught,
  but an impl that re-initialized with the OLD extremes value copied over would
  not be — not realistic given single shared call site.
- Wilder ATR is kept exactly at test literals by engineering TR = 0.5 (or
  TR = 7.5/1.0 at the reversal); this constrains future tape edits but matches
  the pinned-literal house style of the file.
- `_since_low` is maintained on long legs (and vice versa) though each side
  consumes only one — intentional: a flip re-inits both anyway, and keeping
  both updated costs nothing and documents the same rule for both sides.
