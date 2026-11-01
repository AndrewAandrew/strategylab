# Task 6 report — Стратегия: каркас, параметры, Дончиан и ATR

**Status:** DONE_WITH_CONCERNS (one documented warmup-gating concern, see «Concerns»)
**Commit:** `828380d feat: donchian and atr core of two-sided momentum strategy` (branch `spec01-two-sided-momentum`, local only)

## What was implemented

New file `strategylab/strategy/two_sided_momentum.py` — skeleton only, no orders yet (`on_bar` returns `[]`):

- Class `TwoSidedMomentumStrategy`: `name = "two_sided_momentum"`, `PARAM_NAMES = ("donch_len", "atr_mult", "bias_len")`, constructor defaults `Decimal("48") / Decimal("1.5") / Decimal("48")`.
- Module constants `_ATR_LEN = 14`, `_RISK_FRAC = Decimal("0.01")` (Global Constraint 10 — not parameters; `from_params({"atr_len": ...})` therefore raises).
- Validation via shared validators: `donch_len` → `positive_int`, `atr_mult` → `positive`. **`bias_len = 0` is checked before delegating to `positive_int`** (which raises on 0): 0 → `self._bias_len = 0`, `self._bias = None` (filter disabled). Public property `bias_len -> int` (plain int).
- `from_params` per house convention: unknown keys → `ValueError(f"неизвестные параметры two_sided_momentum: {unknown}")` (message contains the strategy name).
- 15m state updated on each CLOSED 15m bar via `Aggregator(FIFTEEN_MIN_WIDTH, FIFTEEN_MIN_COUNT)`:
  - order of ops exactly per brief Step 6: `atr = self._atr.update(B.high, B.low, B.close)` → bias update if enabled → expose `hh/ll` computed over `self._window` **before** appending B → `self._window.append((B.high, B.low))`. ATR and bias INCLUDE signal bar B.
  - Donchian window: `deque[tuple[Decimal, Decimal]]`, `maxlen=donch_len`.
  - State attrs exposed as `self._hh`, `self._ll`, `self._atr_value`, `self._bias_value` (`Decimal | None`), mirroring `_atr15_value` of `FlatKeltnerStrategy`.
- Warmup gate: exposure only after `max(donch_len, _ATR_LEN)` closed 15m bars have passed (`self._closed_15m > max(...)`, first exposed on the NEXT closed bar); before that everything stays `None` and nothing raises — incomplete window trades nothing silently. Details in «Concerns».

New file `tests/test_strategy_two_sided_momentum.py` — 4 tests (Steps 1, 2, 5 verbatim from the brief) plus **all five reusable helpers** (Tasks 7–10):

- `_bars(count, first=0)` — sequential flat 1m bars at 100, corridor ±0.25 ⇒ 15m true range 0.5, no breakout anywhere by construction.
- `_rising_1m_bars(count, first=0, base)` — monotonic rising: each bar's close (`base + 0.01·(i+1)`) is strictly above all previous highs ⇒ upside breakout is a tape fact.
- `_flat_state(bar_index=0)` — `StrategyState(position=Decimal("0"), equity=Decimal("10000"), bar_index)`.
- `_orders(strategy, bar, state)` — one bar through `on_bar`, returns order list.
- `_run_strategy(strategy, bars) -> list[list[Order]]` — feeds the sequence with flat state, returns per-bar order lists (empty lists included, index == bar number).
- `START = 1_717_200_000` — hour/15m/5H boundary, same as the other strategy tests; all Decimal values, no float literals.

## TDD Evidence

### RED #1 (Step 3 — before implementation)

`.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py -v`

```
tests\test_strategy_two_sided_momentum.py:21: in <module>
    from strategylab.strategy.two_sided_momentum import TwoSidedMomentumStrategy
E   ModuleNotFoundError: No module named 'strategylab.strategy.two_sided_momentum'
============================== 1 error in 0.66s ===============================
```

Expected exactly this (brief Step 3): the module does not exist yet.

### Skeleton (Step 4) → params tests green

Same command after implementing constructor/`from_params`/`bias_len` only:

```
tests/test_strategy_two_sided_momentum.py::test_constructor_validates_params PASSED
tests/test_strategy_two_sided_momentum.py::test_bias_len_accepts_zero_as_disabled PASSED
tests/test_strategy_two_sided_momentum.py::test_from_params_rejects_unknown PASSED
============================== 3 passed in 0.16s ==============================
```

### RED #2 (Step 5 — warmup test before Step 6 implementation)

```
>       assert s._hh is not None and s._atr_value is not None  # на баре 15 полны оба окна
E       assert (None is not None)
FAILED tests/test_strategy_two_sided_momentum.py::test_warmup_exposes_channel_only_when_both_windows_are_full
========================= 1 failed, 3 passed in 0.26s =========================
```

Fails for the right reason: the first assertion (state still `None` after 14 closed bars) passes trivially, the second fails because nothing updates 15m state yet — the skeleton does not aggregate.

### GREEN (Step 7 — after implementing `_update_15m`)

```
tests/test_strategy_two_sided_momentum.py::test_constructor_validates_params PASSED [ 25%]
tests/test_strategy_two_sided_momentum.py::test_bias_len_accepts_zero_as_disabled PASSED [ 50%]
tests/test_strategy_two_sided_momentum.py::test_from_params_rejects_unknown PASSED [ 75%]
tests/test_strategy_two_sided_momentum.py::test_warmup_exposes_channel_only_when_both_windows_are_full PASSED [100%]
============================== 4 passed in 0.15s ==============================
```

### Full suite + gates

- `pytest` → **376 passed, 3 skipped** (baseline 372 + 4 new; matches expectation).
- `mypy strategylab` → `Success: no issues found in 34 source files`.
- `ruff check .` → `All checks passed!` (one E741 on generator variable `l` was fixed by renaming to `hi`/`lo`).

### Behavior sanity (not committed, run ad-hoc)

`_rising_1m_bars` × 15 closed bars, `donch_len=3`, `bias_len=2`: `_hh = 102.10` (high of 15m bar #14), `_ll = 101.65` (low of bar #12 — exactly the last 3 closed bars before signal bar #15), `_atr_value = 0.15` (includes B), `_bias_value` non-None; all per-bar order lists empty; partial window (`_bars(30)` fresh strategy) raises nothing.

## Files changed

- Created: `strategylab/strategy/two_sided_momentum.py`
- Created: `tests/test_strategy_two_sided_momentum.py`
- Commit `828380d` contains exactly these two files; nothing else touched.

## Self-review findings

1. **Interface contract** — re-verified attribute-by-attribute against the brief: class attrs, defaults, `from_params` signature, `on_bar` signature, `bias_len` int property, both module constants. OK.
2. **All five helpers present** and typed for reuse; `_flat_state` takes an optional `bar_index` (default 0) so the warmup test can call `_flat_state()` while `_run_strategy` passes real indices.
3. **No dead order logic** — `on_bar` unconditionally returns `[]`; nothing about sizing/brackets smuggled in. `_RISK_FRAC`/`_atr_mult` are read by nobody yet — mandated contract surface, not logic.
4. **Decimal-only** arithmetic and literals; time as int. `atr_len` is not constructible through `from_params`.
5. Fixed during review: ruff E741 (`l` → `lo`), two Cyrillic docstring wrap/typo issues, `bias_len` branch pre-annotated for mypy strict.

## Concerns (warmup gating — read this if reviewing the boundary)

The brief's Step-5 test pins: after **14** closed 15m bars everything is `None`; exposure appears on **bar 15** (`donch_len=3`). But the codebase's shared `Atr(14)` actually produces its first non-None value already on the **14th** update (verified empirically; `Rma` seeds at count==n, first TR uses bar 1's own range). So a naive gate `atr is not None and len(window) == maxlen` would expose state on bar 14 and fail the pinned test.

Implementation choice: an explicit closed-bar counter, `self._closed_15m > max(self._donch_len, _ATR_LEN)` — state becomes visible strictly **after** the boundary `max(donch_len, _ATR_LEN)` closed 15m bars, on the next bar. This matches the controller's stated rule (boundary `max(donch_len, _ATR_LEN)`, first signal on the 15th closed bar when `donch_len=3`, "ATR gets its first value on the 15th closed bar" — classically true, one bar later than this repo's `Atr` computes). Consequences:

- **Grid-neutral:** all spec-grid `donch_len ∈ {24, 48, 96} ≥ 14`, so there the boundary equals `donch_len` and exposure starts on bar `donch_len+1` — exactly the Donchian definition (hh/ll need `donch_len` bars closed BEFORE the signal bar). No behavioral difference for Task 11.
- The ATR value computed on the 14th closed bar is fed into the RMA (indicator state advances every closed bar, nothing is skipped), only its *exposure* starts one bar later. `Atr` was consumed, not reinvented.
- The docstring of `_update_15m` documents this honestly (Atr seeds at 14, pinned boundary is 15) so Tasks 7–10 readers see the rule: `None` ⇔ warmup not passed ⇔ no signal possible.
- **Risk if the controller intended the naive gate:** the pinned test would then be off-by-one against itself; the test as given is self-consistent only with the `> max(...)` gate, so I treated the test as referee per instructions.

Tasks 7–10 note: first exposable signal bar for a strategy built with small `donch_len` is closed 15m bar #15 regardless of `donch_len` — synthetic breakouts must come after that (the helpers make it easy: `_bars(n)` warmup plateau has TR 0.5 per 15m bar, so `atr_mult=2` ⇒ stop distance 1.0, qty 100 per the plan's fixed numbers).
