### Task 1: Калибровочный харнесс `scripts/calib/harness.py`

**Files:**
- Create: `scripts/calib/harness.py`
- Test: `tests/test_calib_harness.py`

**Interfaces:**
- Consumes: `strategylab.broker.sim.BrokerSimulator`, `strategylab.broker.costs.CostModel/IntrabarOrder`, `strategylab.broker.fork.run_fork` (только в тесте эквивалентности), `strategylab.core.engine.Engine`, `strategylab.core.metrics.Metrics`, `strategylab.core.trades.TradeLog/Trade`, `strategylab.core.bars.Interval`, `strategylab.drivers.replay.ReplayDriver`, `strategylab.data.schema.connect`, `strategylab.data.store.CandleStore`, `strategylab.data.zones.zones_touched/Zone`, `strategylab.spec.spec.StrategySpec`, `strategylab.strategy.examples.STRATEGY_CLASSES/build`; grid-хелперы `_parse_grid/_merge_grid/_combinations/_defaults/_required/_plain/_plain_or_null` из `scripts/sweep.py` — загрузка модуля по пути файла, приём как `tests/test_sweep.py::_load_sweep` (~строка 39).
- Produces:
  - `class OutsideFitZone(Exception)`
  - `@dataclass(frozen=True, slots=True) class ForkSide`: поля `intrabar: IntrabarOrder`, `metrics: Metrics`, `trades: tuple[Trade, ...]`, `pending_after: tuple[Order, ...]`
  - `@dataclass(frozen=True, slots=True) class CellOutcome`: `spec_version: str`, `zones: tuple[Zone, ...]`, `sides: tuple[ForkSide, ForkSide]` (порядок: PESSIMISTIC, OPTIMISTIC)
  - `run_cell(db: Path, symbol: str, strategy: str, params: Mapping[str, Decimal], start_ts: int, end_ts: int, capital: Decimal = Decimal("10000"), interval: Interval = Interval.HOUR, costs: CostModel | None = None) -> CellOutcome` — собирает ReplayDriver/BrokerSimulator/TradeLog/Metrics как `fork._run_once`, дважды (обе вилки); `ensure_fit` до прогонов; `ValueError` стратегии — наружу.
  - `cell_rows(outcome: CellOutcome, combination: Mapping[str, Decimal], names: Sequence[str]) -> tuple[str, str]` — две CSV-строки (pess, opt) по замороженной схеме design §10: `fork, spec_version, <params...>, trades, wins, win_rate, net_pnl, equity, max_drawdown, profit_factor, sharpe, pending_limit_buy, pending_other` (fork — `pessimistic`/`optimistic`; `win_rate` = wins/trades, `"null"` при 0 сделках; `pending_limit_buy` = не reduce-only LIMIT из `pending_after`, `pending_other` = всё остальное; числа как в sweep, `format(Decimal, "f")`, null-маркер `"null"`).
  - `journal_lines(side: ForkSide) -> list[str]` — заголовок `entry_ts,entry_price,exit_ts,exit_price,pnl,exit_reason` + по строке на сделку, Decimal через `format(v,"f")`.
  - `write_csv(path: Path, lines: Sequence[str]) -> None` — `open("w", encoding="utf-8", newline="")`.
  - CLI `main(argv)`: позиционные `DB SYMBOL START END STRATEGY`; `--param ИМЯ=в1,в2` (повторяемо, синтаксис sweep); `--capital` (default 10000); `--interval hour|15m|5m|1m` (default hour); `--double-costs` (CostModel(0.0012, 0.0004, 0.0010, 1)); `--out DIR` (default `data/calib`). Файлы: `{strategy}_{symbol}_{start}_{end}[_dc].csv`, `..._journal_{p|o}.csv` и `..._report_p.txt` (текст `build_report` пессимистичной стороны — сборка отчёта как в `fork._run_once`; дамится и в ячейках с n=1/n=0 — вырожденный рендер проверяется на реальных данных). Шапка как у sweep (touches печатает `holdout=0, live=0` — харнесс их не пишет и обязан подтвердить fit-only окном). Падение ячейки (`ValueError`, `strategylab.broker.sim.LiquidationError`) ловится per-cell: строка `ОТКЛОНЕНО <комбинация>: <класс>: <msg>` в stderr + пометка в шапке, прогон сетки продолжается.

- [ ] **Step 1: Написать падающие тесты** `tests/test_calib_harness.py` (fixture-база — как в `tests/test_sweep.py`: tmp SQLite `SCHEMA/connect`, запись минутных баров `CandleStore` (имя метода записи посмотреть в `data/store.py`), `assign_zone` FIT; для `test_outside_fit_raises` — HOLDOUT):
```python
def test_run_cell_matches_run_fork(tmp_db): ...
    # buy_hold на фикстуре: net_pnl/equity/trades/max_drawdown pessimistic и
    # tuple(trades-журнала) равны run_fork(record_trades=True) на том же spec
def test_outside_fit_raises(tmp_db_holdout, tmp_db_unzoned): ...
    # HOLDOUT-окно и НЕРАЗМЕЧЕННОЕ окно (zones_touched == ()) — оба OutsideFitZone
def test_metric_row_null_markers(tmp_db): ...
    # окно на 2 бара: sharpe=="null", win_rate=="0", PF "0" при убытке; колонки
    # equity/win_rate/pending_limit_buy/pending_other на месте
def test_metric_row_negative_equity(tmp_db): ...
    # строго падающее окно: net_pnl и equity строки начинаются с "-", без abs
def test_pending_snapshot_visible(tmp_db): ...
    # strategy-заглушка из теста шлёт far-away LIMIT: sides[0].pending_after не пуст
def test_write_csv_utf8(tmp_path): ...
    # bytes == expected utf-8, без BOM
```
- [ ] **Step 2:** `.venv\Scripts\python.exe -m pytest tests/test_calib_harness.py -q` → FAIL (модуля нет).
- [ ] **Step 3: Реализовать `scripts/calib/harness.py`** строго по Interfaces. `run_cell`: зоны один раз (`zones_touched`), `ensure_fit` (всё, что не `== (Zone.FIT,)`, — `OutsideFitZone`; пустое множество тоже); для каждого `intrabar` из (PESSIMISTIC, OPTIMISTIC): свой `build(strategy, params)`, свой брокер/журнал/метрики `Metrics(equity=capital)`, `Engine().run(...)`; после прогона `pending_after = tuple(broker.pending_orders)`, counters — из него. Sweep-хелперы — через импорт модуля sweep по пути (не копировать).
- [ ] **Step 4:** pytest файла → PASS; весь `pytest tests -q` зелёный; `ruff check .`; `mypy strategylab scripts` чисто.
- [ ] **Step 5: Commit:** `git add scripts/calib/harness.py tests/test_calib_harness.py; git commit -m "feat(calib): run harness with trade journal, pending snapshot, fit-only guard"`

---

