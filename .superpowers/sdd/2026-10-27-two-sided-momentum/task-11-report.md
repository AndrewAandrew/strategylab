# Task 11 Report: Регистрация, CLI и колонки развёртки

Branch `spec01-two-sided-momentum`, commit `f2cc03e` — `feat: register two_sided_momentum and publish sides in sweep`.

## What was implemented

1. **`strategylab/strategy/examples.py`** — `"two_sided_momentum": TwoSidedMomentumStrategy` added to
   `STRATEGY_CLASSES`; in `build` the branch
   `if name == "two_sided_momentum": return TwoSidedMomentumStrategy.from_params(params)`
   added immediately before `raise UnknownStrategy(name)` — mirrors the existing three `from_params`
   strategies exactly (import added in the same sorted block).
2. **`scripts/sweep.py`** — six names appended to `METRIC_COLUMNS` after `sharpe`; six cells appended
   to the end of `_row`'s cell list in the same order, serialized to mirror `report.py`:
   `str(long.trades)`, `_plain(long.net_pnl)`, `_plain_or_null(long.profit_factor())`, same for `short`.
   Header print derives from `METRIC_COLUMNS` (`*METRIC_COLUMNS`), so header/row parity is
   structural — the integration test additionally pins the full header string and 18-cell rows.
   File-writing encoding untouched (Global Constraint 11).
3. **`tests/test_examples.py`** — Step 1 test `test_build_returns_two_sided_momentum` verbatim.
   `UnknownStrategy` message is not pinned anywhere (message is just the name), so no expectation update.
4. **`tests/test_sweep.py`** — Step 2 `test_sweep_defaults_take_constructor_values` (helper confirmed
   as `_defaults`), Step 3 `test_sweep_row_includes_side_columns` verbatim with `report_with(metrics)`
   wrapper over `build_report` (the test_report.py idiom), imports `make_fill` from
   `tests/test_metrics.py` and `Side` from `strategylab.broker.orders`. Existing pins updated ONLY for
   the new tail: `CSV_HEADER` extended with the six names, `len(row) == 12` → `== 18`. Pre-tail cells
   and their index pins (`row[6]` trades, `row[8]` net_pnl, `EXIT_MULT_COLUMN = 5`) untouched and green.
5. **`pyproject.toml`** — NOT changed (Step 7 confirmation: `two_sided_momentum.py` lives in the
   existing `strategylab/strategy` package; `test_layering.py` passed). **`tests/test_cli.py`** — NOT
   changed (nothing there pins the registry contents).

### Header after the change

    spec_version,<names>,trades,wins,net_pnl,max_drawdown,profit_factor,sharpe,long_trades,long_net_pnl,long_profit_factor,short_trades,short_net_pnl,short_profit_factor

(pasted literal from the flat_keltner test: `spec_version,adx_len,ema_len,atr_len_regime,atr_len_exit,exit_mult,trades,wins,net_pnl,max_drawdown,profit_factor,sharpe,long_trades,long_net_pnl,long_profit_factor,short_trades,short_net_pnl,short_profit_factor`)

### `_row` cells after the change (in order)

    [report.spec_version, *params..., metrics.trades, metrics.wins, net_pnl, max_drawdown,
     profit_factor(), sharpe(), long.trades, _plain(long.net_pnl), long.profit_factor(),
     short.trades, _plain(short.net_pnl), short.profit_factor()]

## TDD evidence

**RED** (before implementation): 4 failed —
- `test_build_returns_two_sided_momentum` → `strategylab.strategy.examples.UnknownStrategy: two_sided_momentum`
  at examples.py:98 — the module's own type, NOT KeyError, exactly as pinned (brief line 45).
- `test_sweep_row_includes_side_columns` → tail was `['3','1','60','40','2.5','-0.622...']` — old metric
  cells in the tail, six side columns absent.
- both existing integration sweep tests (updated `CSV_HEADER` not found in output).
- (Step 2 `_defaults` test passed immediately — the constructor already had exactly the three defaults;
  it is a mechanical gate, not a failing test.)

**GREEN** (after implementation): `tests/test_examples.py tests/test_sweep.py -v` → 22 passed.
`tests/test_layering.py` → 2 passed. Full suite → **397 passed, 3 skipped** (= baseline 394 + 3 new;
the 3 skips are the network-marked tests).

**Network run**: `$env:STRATEGYLAB_NETWORK_TESTS="1"; pytest -q -m network` →
**3 passed, 397 deselected** (gating is `skipif(not os.environ.get("STRATEGYLAB_NETWORK_TESTS"))` in
test_aggregate.py/test_sync.py; these 3 are exactly the suite's baseline skips).

## Step 9 checks

- `ruff check .` → All checks passed! (line-length 100, scripts covered)
- `mypy strategylab` → Success: no issues found in 34 source files (repo scope).
  Also ran `mypy scripts\sweep.py` out of curiosity: clean, 1 source file, no issues.
- `pytest -q` → 397 passed, 3 skipped.

## Step 8 smoke run (real data, fit zone)

    $env:PYTHONUTF8="1"
    .\.venv\Scripts\python.exe -m strategylab.cli run data\btcusdt.db BTCUSDT MINUTE 1761523200 1762128000 two_sided_momentum --capital 10000

Exit code 0; 41 lines (full output in `%TEMP%\opencode\task11-smoke.txt`). Tail:

    ...PESSIMISTIC fork...
    long_trades: 8
    long_net_pnl: -590.0063006728563032241034901
    long_profit_factor: 0.2314865250329872886201572510
    short_trades: 11
    short_net_pnl: -930.7515004485943278376564980
    short_profit_factor: 0.1457329565237220535962139164
    spec_version: be3e284ba59e
    zones_used: fit
    costs: taker_fee=0.0006 maker_fee=0.0002 slippage=0.0005 leverage=1
    trials: 1
    intrabar: OPTIMISTIC
    trades: 19
    wins: 3
    losses: 16
    net_pnl: -1520.757801121450631061759988
    equity: 8479.242198878549368938240018
    max_drawdown: 1644.150427065732229479092582
    profit_factor: 0.1811804487021323777883432867
    win_rate: 0.1578947368421052631578947368
    sharpe: -0.8738218662348507327279123880
    long_trades: 8
    long_net_pnl: -590.0063006728563032241034901
    long_profit_factor: 0.2314865250329872886201572510
    short_trades: 11
    short_net_pnl: -930.7515004485943278376564980
    short_profit_factor: 0.1457329565237220535962139164
    ВЕРДИКТ: измерима

- Two fork reports + `ВЕРДИКТ: измерима` — as expected.
- **trades: 19** (not an empty run — the 7-day fit window did produce trades; long 8 + short 11 = 19,
  side nets sum to total net_pnl, PF arithmetic consistent).
- Side lines present in BOTH forks (Task 4 serialization working through CLI).
- `zones_used: fit` → **`data/touch_registry.json` remains ABSENT after the run** — verified with
  `Test-Path` immediately after the command: `False`. Fit touches are not registered; no side effects.

## Files changed (commit f2cc03e)

- `strategylab/strategy/examples.py` (+4)
- `scripts/sweep.py` (+15)
- `tests/test_examples.py` (+5)
- `tests/test_sweep.py` (+58 −3)

Exactly the expected four files; `test_cli.py`/`pyproject.toml` untouched. `strategylab.egg-info/` left
untracked (build artifact, not mine to add).

## Self-review findings

- Registry branch mirrors the three existing `from_params` strategies line-for-line; dict entry in the
  same tail position as its `build` branch — consistent with the existing ordering convention.
- Header/row parity: header is constructed from `METRIC_COLUMNS`, row cells hard-coded in the same
  order; the six names match the gates.py contract verbatim, appended after `sharpe`; pre-existing
  column order/content untouched (only appended).
- Serialization mirrors `report.py`: ints via `str`, Decimal via `_plain` (no exponent), None PF via
  `_plain_or_null` → `null`. Step 3 pinned tail assertion passes verbatim.
- No touch_registry side effects (verified above).

## Concerns

- None blocking. Minor note for Task 12: existing CSV consumers must expect 18-cell rows now;
  the flat_keltner integration test already pins the full new header string, so drift is test-caught.
- The strategy trades both sides on the 7-day real window, so a fit-window sweep will produce non-zero
  side columns — good news for gate wiring, no "always-empty tail" trap.
