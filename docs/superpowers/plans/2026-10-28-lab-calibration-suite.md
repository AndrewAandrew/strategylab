# Lab Calibration Suite (spec_02 … spec_05) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Провести четыре pre-registered калибровочные спеки (buy&hold, MA-crossover, limit-pullback, DCA-adversarial) через лабораторию strategylab, верифицируя конвейер механическими сверками, и опубликовать отчёты в репозиторий Андрея.

**Architecture:** Общий харнесс `scripts/calib/harness.py` собирает `Engine + BrokerSimulator + TradeLog` напрямую (видит `pending_orders`, пишет журнал сделок), CSV-конвенция унаследована от `scripts/sweep.py`. Каждая спека = verify-скрипт (пин, коммитится до прогона) + прогон сетки + отчёт в `strategydocs/`. Ядро трогает ровно одна отдельная фича (`feat/limit-order-expiry`) — обратная совместимость доказывается полным зелёным pytest без правки старых assert'ов и побайтовым diff артефактов spec_02/03 после merge.

**Tech Stack:** Python 3.13 (см. `.python-version`), Decimal (без float), sqlite3, pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-10-28-lab-calibration-suite-design.md` (коммит `396cea7` — включает помеченные поправки брат-ревью A1–A10, owner-approved 2026-10-28; все параметры/критерии заморожены там — план их не пересматривает)

**Execution (консенсус с брат-ревью, 2026-10-28): Native** (`superpowers:executing-plans`),
с обязательными fresh-eyes прерываниями: перед каждым коммитом verify-скрипта
(Tasks 2/3/6/8) и перед interface-freeze (Tasks 1, 4, 6) независимый subagent-ревьюер
пересчитывает критерии сверок только по design+коду (артефактов прогона ещё не
существует) — расхождения класса A1–A5 разрешаются там, до встречи с данными. После
первого прогона спеки verify не редактируется (иначе = подгонка).

## Global Constraints

- Ветка работы: `calibration-suite` (база `origin/main` 5d94e97 + виш-пик фикса журнала f2d6119). `main` друга не трогать.
- Окно fit: `[1761523200, 1790294400)`; фолды (3×111d): `[1761523200, 1771113600)`, `[1771113600, 1780704000)`, `[1780704000, 1790294400)`.
- Инструменты: `data/btcusdt.db` символ `BTCUSDT`; `data/ethusdt.db` символ `ETHUSDT`. Интервал спеки: `Interval.HOUR`.
- Капитал: `Decimal("10000")`. Издержки: дефолтный `CostModel()` (taker 0.0006, maker 0.0002, slip 0.0005, leverage 1.0000).
- Харнесс исполняет окно только при `zones_touched == (Zone.FIT,)`; пустое множество зон
  (окно без разметки) и любые HOLDOUT/LIVE — `OutsideFitZone` (A9: в БД реально есть
  holdout/live-бары за окном и не-зонная pre-fit история — строгость нагрузочная).
  Реестр касаний харнессом не пишется никогда. Итог для отчётов: touches: holdout=0,
  live=0, файла `data/touch_registry.json` нет.
- Verify-скрипты: только stdlib (`sqlite3`, `decimal`, `collections`) — ноль импортов
  `strategylab` (A8: иначе сверка наследует баги агрегации/зон, которую верифицирует).
  Часовой bucketing свой (`ts // 3600`, полные группы по 60 минут). Sanity-константы из
  брат-замеров: 7 992 полных часа в окне на обеих БД, 0 гэпов.
- Все артефакты прогонов — `data/calib/` (gitignore на `data/`), явная кодировка `utf-8`, `newline=""`; PowerShell-редиректы для данных запрещены (`>` пишет UTF-16).
- Decimal-арифметика везде; float запрещён и в verify-скриптах. Сериализация Decimal — `format(value, "f")`, null-маркер `"null"` (как `_plain_or_null` в sweep).
- Правила внутрибара (пин из кода): ордер с сигнала бара N исполняется на `apply_bar` бара N+1; MARKET — `close[N+1]·(1±slip)`, taker, reason `"market"`; LIMIT — по цене лимита при касании (BUY: `low ≤ limit`), maker, reason `"limit"` (для non-reduce-only); сведение окна — close последнего бара ±slip, taker, reason `"market"`.
- На каждой вехе: `.venv\Scripts\python.exe -m pytest tests -q` зелёный (база 363 passed / 3 skipped), `ruff check .` чисто, `mypy strategylab scripts` чисто.
- Публикация: `git push origin calibration-suite` и `git push origin feat/limit-order-expiry` в `AndrewAandrew/strategylab`. PowerShell: `&&` не использовать — `;`; git пишет в stderr → ложный Exit code 1, успех проверять текстом вывода.
- Ядро не чинить по ходу прогонов (design §2, правило E): дефекты только регистрируются в отчётах.

## Review Focus

Входы/состояния, которые подразумеваются спекой, но не покрыты гейтами отдельных задач — каждая строка пиннится тестом указанной задачи:

1. `Order` без `expires_in_bars` (None) ведёт себя побайтово как до фичи: весь существующий брокерский набор проходит **без правок assert'ов** — Task 4 (новые тесты отдельным файлом, `tests/test_broker_sim.py` не трогать).
2. Вырожденная статистика n=1 (`profit_factor == 0` при одной убыточной, `sharpe == null`, `win_rate` числовая) сериализуется харнессом в CSV и не роняет рендер — Task 1 `test_metric_row_null_markers`, подтверждается Task 2 (V2.3).
3. Класс без внутрибарных уровней идентичен на PESSIMISTIC/OPTIMISTIC: метрики и журнал равны построчно — Task 3 (V3.4), для maker-вилок — Task 6 (V4.4).
4. Отрицательная equity проходит артефакты без abs/клиппинга/NaN: `net_pnl`/`equity` со знаком `-`, `max_drawdown` может превышать капитал — Task 1 `test_metric_row_negative_equity`, подтверждается Task 8 (V5.4).
5. Entry-лимитка не наращивает позицию и не плодит лестницу: при открытой позиции новых BUY нет, выход `reduce_only`; на границе волны в очереди транзенторно две заявки, но **исполнимой** на баре — не более одной (двойного филла не существует; owner-решение 2026-10-28 по A3) — Task 5 `test_no_new_limits_while_position_open` + `test_only_one_fillable_entry_limit`, подтверждается Task 6 (V4.2/V4.3).

---

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

### Task 2: spec_02 buy & hold — verify + прогоны + отчёт (B)

**Files:**
- Create: `scripts/calib/verify_spec02.py`
- Create: `strategydocs/калибровка_spec_02_buy_hold_20261028.md`
- Артефакты: `data/calib/...` (в git не входят)

**Interfaces:**
- Consumes: CLI харнесса Task 1; БД — **сырой `sqlite3`** (`SELECT ts, close, ... FROM`
  по таблице минутных баров; имя таблицы/колонок подсмотреть в `strategylab/data/schema.py`,
  но verify-скрипт содержит только SQL-литералы и свой bucketing часов `ts // 3600`;
  sanity: 7 992 полных часовых группы, 0 гэпов).
- Produces: `data/calib/baseline/` — копии full-window csv+journal spec_02 и spec_03 (контракт diff для Task 4); конвенция: verify читает csv по заголовку колонок, символы/окна — модульные константы скрипта (совпадают с Global Constraints).

- [ ] **Step 1: Написать `scripts/calib/verify_spec02.py`** (до артефактов падает — это норма). Независимый пересчёт только из баров и публичных констант (свои `Decimal("0.0006")` и т.д., без импортов из пакетных модулей, кроме sqlite-чтения баров):
  - `h` — часовые бары из raw sqlite (свой bucketing по 60-минуткам, полные группы); `entry = h[1].close*(1+slip)`, `fee_in = taker*entry`; `exit = h[-1].close*(1-slip)`, `fee_out = taker*exit` (qty=1; вход — bar 1 по правилу «ордер с бара 0 исполняется на баре 1»);
  - V2.1: journal ровно 1 строка, `exit_reason == "market"`;
  - V2.2: `net_pnl == exit - entry - fee_in - fee_out` (допуск 1E-20); journal `entry_price/exit_price` равны `entry/exit`; колонка `equity` == `10000 + net_pnl`; колонка `max_drawdown` == `-net_pnl`;
  - V2.3: колонка `profit_factor` == `"0"` при убытке (не null!), `sharpe` == `"null"`, `win_rate` == `"0"`; отдельной строкой PASS — `..._report_p.txt` непустой и содержит null-маркеры рендера; всё парсится без падения;
  - V2.4: знак по фактическим исполняемым барам: `exit < entry ⟹ net_pnl < 0`, иначе FAIL (прежний «и наоборот» относительно close[0] арифметически не следует — A10(i));
  - вывод `PASS/FAIL V2.x <числа>` построчно по 2 символам; ненулевой exit code при FAIL.
- [ ] **Step 2: Commit verify ДО прогона:** `git add scripts/calib/verify_spec02.py; git commit -m "test(calib): spec_02 verify pinned before run"`
- [ ] **Step 3: Прогон full-window, обе ноги:**
```powershell
.venv\Scripts\python.exe scripts/calib/harness.py data\btcusdt.db BTCUSDT 1761523200 1790294400 buy_hold --out data\calib
.venv\Scripts\python.exe scripts/calib/harness.py data\ethusdt.db ETHUSDT 1761523200 1790294400 buy_hold --out data\calib
```
- [ ] **Step 4: `.venv\Scripts\python.exe scripts/calib/verify_spec02.py`** → ожидаем 4×PASS. FAIL = дефект лаборатории: цифры в отчёт частью 2, ядро не трогать.
- [ ] **Step 5: Прогон фолдов (3 окна × 2 символа) и `--double-costs` (× 2 символа)** — команды как Step 3 с фолд-границами из Global Constraints и флагом `--double-costs`.
- [ ] **Step 6: Baseline-копии:** `New-Item -ItemType Directory data\calib\baseline -Force; Copy-Item data\calib\buy_hold_* data\calib\baseline\` (csv + journal обеих ног + report_p — полный набор файлов ячейки).
- [ ] **Step 7: Отчёт `strategydocs/калибровка_spec_02_buy_hold_20261028.md`**: шапка (условия, `spec_version` обеих строк, costs, `touches: holdout=0, live=0` + «реестр отсутствует», «калибровочная, не alpha-претензия»); часть 1 (гейты): G1 ❌ (1 сделка — pre-registered), G2 N/A (0 параметров), G3 N/A (одна ячейка), G4 по double-costs цифре, G5 по 3 фолдам; часть 2: V2.1–V2.4 с числами; секция **finding №0**: отсутствие margin-check у брокера (`broker/sim.py::submit` против env-design §9.3) — вывод о лаборатории.
- [ ] **Step 8:** pytest/ruff/mypy чисто; `git add strategydocs\калибровка_spec_02_buy_hold_20261028.md; git commit -m "docs(spec_02): buy&hold calibration run - pipeline null-test results"`
- [ ] **Step 9: Первый push ветки:** `git push -u origin calibration-suite` (успех — текстом `new branch`, не exit code).

---

### Task 3: spec_03 MA crossover — verify + сетка + отчёт (A)

**Files:**
- Create: `scripts/calib/verify_spec03.py`
- Create: `strategydocs/калибровка_spec_03_ma_cross_20261028.md`

**Interfaces:**
- Consumes: харнесс CLI (сетка `--param fast=20,50 --param slow=100,200`); стратегия `sma_cross` из реестра — **класс long/flat** (поправка design §6: равновеликий противоположный филл обнуляет позицию без флипа, `position.py:65-72`; сторона всех эпизодов = сторона первого кросса; семантику `strategy/examples.py:38-55` перечитать перед verify).
- Produces: артефакты `sma_cross_*` (csv 8 строк = 4 ячейки × 2 вилки + journals); пополнение `data/calib/baseline/` вместе с spec_02 (общий контракт Task 4).

- [ ] **Step 1: Написать `scripts/calib/verify_spec03.py`** (raw sqlite, свои `deque`-SMA по часовым закрытиям, ноль импортов пакета; warmup перечитать с `examples.py:38-55` и переписать независимо: SMA считается с бара `slow-1`, сравнение с предыдущими значениями — с бара `slow`; переходы `prev_fast <= prev_slow & fast > slow` = golden, `prev_fast >= prev_slow & fast < slow` = death):
  - K = переходы на всех барах, КРОМЕ последнего (сигнал последнего бара не исполняется — «ордер с бара N живёт с N+1»); V3.1: `len(journal) == ceil(K/2)` — нечётный кросс открывает эпизод, следующий чётный закрывает обнулением (без флипа), висящий сводит окно; допуск 0; K=0 ⟹ 0 строк, net=0;
  - V3.2: все `exit_reason == "market"` (ни `stop`/`take_profit`/`limit`);
  - V3.3: полный по-сделочный реплей: сторона всех эпизодов = сторона первого кросса; для эпизода entry = close[кросс_бар+1]·(1±slip) по направлению входа, exit = close[кросс_бар+1] закрывающего чётного кросса (для висящего — close последнего бара окна), taker-комиссии с обоих концов; сверка entry_ts/entry_price/exit_ts/exit_price/pnl каждой journal-строки (допуск 1E-20) и суммы против `net_pnl` из CSV;
  - V3.4: journal pess == journal opt побайтово; метрики обеих вилок равны всеми колонками;
  - таблица вывода: `ячейка | K(независ.) | trades(журнал) | V3.1..V3.4 PASS/FAIL` по обеим ногам.
- [ ] **Step 2: Commit verify до прогона:** `-m "test(calib): spec_03 verify pinned before run"`.
- [ ] **Step 3: Сетка full-window × 2 символа** (2 команды harness с `--param fast=20,50 --param slow=100,200`, окна/пути как Task 2).
- [ ] **Step 4: verify** → таблицы в отчёт; провал V3.x = дефект лаборатории (регистрируется, не чинится).
- [ ] **Step 5: Фолды (3 × 2 символа, та же сетка) + `--double-costs` full × 2 символа.**
- [ ] **Step 6: Обновить `data/calib/baseline/`** — добавить sma_cross csv+journal (обе ноги).
- [ ] **Step 7: Отчёт `калибровка_spec_03_ma_cross_20261028.md`**: части 1/2 (G1 ❌ ожидаемо — с **измеренным** K (док-замер: 51–110) и ceil(K/2) vs порог 100; G5 по фолдам центральной ячейки fast=20/slow=100); секция «частотная предвзятость G1» — главный калибровочный результат (design §6); секция kernel-finding «двустороннего flip-класса в лаборатории нет» (`position.py:65-72`) + самокритика «pre-registered 10–40 было ошибкой оценки в 2.5–3×, помечено в design §6».
- [ ] **Step 8:** pytest/ruff/mypy; `git add scripts/calib/verify_spec03.py strategydocs\калибровка_spec_03_ma_cross_20261028.md; git commit -m "docs(spec_03): MA crossover run - slow class vs sampling gate measurement"`; `git push origin calibration-suite`.

---

### Task 4: Фича `feat/limit-order-expiry` — TIF в ядре (отдельная ревью-ветка)

**Files:**
- Branch: `feat/limit-order-expiry` от HEAD `calibration-suite` (после Task 3)
- Modify: `strategylab/broker/orders.py` (+1 поле), `strategylab/broker/sim.py` (списание)
- Create: `tests/test_broker_expiry.py` (существующие брокерские тесты НЕ трогать)
- Create: `strategydocs/заметка_фича_expiry_лимиток_20261028.md`

**Interfaces:**
- Consumes: ничего из calib-артефактов — чистое ядро.
- Produces: `Order(side, kind, qty, limit_price, stop_price, reduce_only, expires_in_bars: int | None = None)` — седьмое поле со значением по умолчанию. Семантика: ордер подан после `apply_bar` с внутренним счётчиком `seq=S` → участвует в `apply_bar` S+1 … S+e → на входе первого `apply_bar` с `seq > S+e` молча списывается (без Fill). `expires_in_bars=None` — неограниченно (старое поведение, побайтовое). Внутренне: `_pending: list[_Rest]`, модульный `@dataclass(frozen=True, slots=True) class _Rest: order: Order; born: int`; счётчик — **поле slotted-датакласса** `_seq: int = field(default=0, repr=False)` (не ad-hoc атрибут: `BrokerSimulator` остаётся датаклассом, позиционный контракт `BrokerSimulator(costs, intrabar)` неизменен; A10(ii)); инкремент в начале `apply_bar`; `submit` пишет `born=self._seq`; все фильтры/циклы (`_cancel_reduced`, `_apply_stops`, `_apply_exits`, `_apply_limits`, `_apply_markets`) сохраняют объекты `_Rest`; свойство `pending_orders` наружу — прежний `list[Order]` (`[r.order for r in self._pending]`).

- [ ] **Step 1:** `git checkout -b feat/limit-order-expiry` (от HEAD calibration-suite).
- [ ] **Step 2: Падающие тесты** `tests/test_broker_expiry.py` (бары-синтетика в стиле `tests/test_broker_sim.py`; читаемый broker через `pending_orders`):
```python
def test_limit_expires_after_ttl_without_fill():   # low не касается; e=2: снят, pending пуст, fills нет
def test_limit_fills_before_expiry_maker():        # касание на S+1: Fill price==limit, fee==maker, reason "limit"
def test_expiry_none_rests_forever():              # 50 баров, pending жив
def test_expired_limit_does_not_fill_late():       # касание на S+e+1: НЕ исполняется
def test_position_untouched_by_expiry():           # ни pnl, ни позиции
def test_legacy_positional_construction():         # 6 позиционных аргументов валидны, expiry=None
```
- [ ] **Step 3:** `.venv\Scripts\python.exe -m pytest tests/test_broker_expiry.py -q` → FAIL.
- [ ] **Step 4: Реализовать** по Interfaces (orders.py — одно поле; sim.py — `_Rest`/`_seq`/фильтр списания в начале `apply_bar`: `self._pending = [r for r in self._pending if r.order.expires_in_bars is None or self._seq <= r.born + r.order.expires_in_bars]` до вызовов `_apply_*`).
- [ ] **Step 5:** pytest файла → PASS; **весь** `pytest tests -q` зелёный без единого изменённого assert в старых файлах; ruff; mypy.
- [ ] **Step 6: Записка Андрею** `strategydocs/заметка_фича_expiry_лимиток_20261028.md`: контекст (design §7, finding «maker-механику нельзя было проверить»), семантика (born/seq/окно жизни, молчаливое списание без Fill), совместимость (None-дефолт; старый набор тестов нетронутым зелёным; baseline-diff шага 7), объём diff, «мержить/отклонять — твоё решение; calibration-набор использует ветку merge'ом только для spec_04».
- [ ] **Step 7: Gate совместимости (baseline diff):** перепрогнать харнессом spec_02 (2 команды Task 2 Step 3) и spec_03 (2 команды Task 3 Step 3) с `--out data\calib\after_feat`; для каждого файла baseline (csv, journal p|o, report_p) сравнить `Get-FileHash` с одноимённым файлом after_feat — равен **хеш файла целиком**, не «мультимножество строк» (`Compare-Object` пропускает перестановки — A10(iii)). Любой несовпавший хеш → фича битая: merge НЕ делать, дефект в свод Task 9.
- [ ] **Step 8:** `git add -A; git commit -m "feat(broker): optional per-order TIF expiry for resting limit orders"`; `git push -u origin feat/limit-order-expiry`.
- [ ] **Step 9:** `git checkout calibration-suite; git merge --no-ff feat/limit-order-expiry -m "Merge feat/limit-order-expiry: kernel TIF for spec_04 runs"`; pytest ещё раз зелёный.

---

### Task 5: Стратегия `limit_pullback` (волны maker-лимиток) + тесты

**Files:**
- Create: `strategylab/strategy/limit_pullback.py`
- Modify: `strategylab/strategy/examples.py` (import + `STRATEGY_CLASSES["limit_pullback"]` + ветка `build`)
- Test: `tests/test_strategy_limit_pullback.py`

**Interfaces:**
- Consumes: Task 4 `Order(..., expires_in_bars=...)`; `StrategyState(position, equity, bar_index)`.
- Produces: `LimitPullbackStrategy(depth: Decimal, ttl_bars: Decimal, hold_bars: Decimal)` — ttl/hold через `int()` (стиль `SmaCrossStrategy:31-32`), валидация `0 < depth < 1`, `ttl_bars >= 1`, `hold_bars >= 1` (ValueError); `from_params(Mapping[str, Decimal])` (стиль flat_keltner); `name = "limit_pullback"`. Поведение (design §7 волны): если `state.position == 0` и `state.bar_index % ttl == 0` → одна `[Order(Side.BUY, OrderKind.LIMIT, Decimal(1), limit_price=bar.close*(1-depth), None, False, expires_in_bars=ttl)]`; при открытой позиции — никаких BUY; на `bar_index - entry_bar >= hold` (entry_bar — бар перехода позиции из 0) → `[Order(Side.SELL, OrderKind.MARKET, Decimal(1), None, None, True)]` один раз (флаг `_exit_submitted` до возврата во флэт).

- [ ] **Step 1: Тесты** (юнит — вызов `on_bar` на списке баров; интеграционные 3 — Engine+BrokerSimulator на маленьких базах как в `tests/test_engine.py`):
```python
def test_wave_entry_limit_maker_fill():        # касание low -> Fill reason "limit", price == лимит ровно
def test_ttl_expiry_cancels_entry():           # нет касания за e баров -> pending пуст на S+e+1
def test_no_new_limits_while_position_open():  # лонг жив: ни одного BUY-ордера
def test_exit_market_reduce_only_after_hold(): # через hold баров -> MARKET SELL reduce_only, ровно один
def test_only_one_fillable_entry_limit():      # на границе волны в очереди 2 (транзенторно), но касание даёт ровно один entry-филл: на баре S+e старая ещё исполнима (новая подана после обработки бара), на S+e+1 уже мертва — двойного филла не существует (owner-решение A3)
def test_params_validation():                  # depth/ttl/hold границы
```
- [ ] **Step 2:** pytest файла → FAIL.
- [ ] **Step 3: Реализовать** модуль + регистрация (STRATEGY_CLASSES, build, импорт в examples.py — как остальные).
- [ ] **Step 4:** PASS файла; полный pytest/ruff/mypy чисто.
- [ ] **Step 5:** `git add strategylab/strategy/limit_pullback.py strategylab/strategy/examples.py tests/test_strategy_limit_pullback.py; git commit -m "feat(strategy): limit_pullback wave-scheduled maker entries with TIF"` (на calibration-suite).

---

### Task 6: spec_04 limit-pullback — verify + сетка 12 ячеек + отчёт (C)

**Files:**
- Create: `scripts/calib/verify_spec04.py`
- Create: `strategydocs/калибровка_spec_04_limit_pullback_20261028.md`

**Interfaces:**
- Consumes: харнесс CLI (12 ячеек `--param depth=0.003,0.005 --param ttl_bars=12,24,48 --param hold_bars=24,48`); стратегия Task 5; колонки `pending_limit_buy`/`pending_other` csv.
- Produces: отчёт; секция maker-находок для свода Task 9.

- [ ] **Step 1: `verify_spec04.py`** — независимая машина состояний по часам БД (своя реализация, без импортов стратегии; правила — текст design §7 и Interfaces Task 5): волны `i*ttl` пока флэт; лимит `close·(1−d)`; живёт бары S+1…S+ttl; исполнение при первом `low ≤ limit`; после филла hold баров, выход MARKET по close·(1−slip) тейкер; выход окна — если позиция открыта, сведение движком (journal это покажет):
  - V4.1: для каждой journal-строки `pnl == (exit − entry) − maker_fee·entry − taker_fee·exit` при qty=1 (допуск 1E-20; entry обязан быть ценой лимита БЕЗ slip — maker-вход); анти-формула с taker на входе обязана давать расхождение (защита от вырождения проверки);
  - V4.2: по колонкам csv: `pending_limit_buy` == число волн, поданных на барах, чей ttl-хвост уходит за границу окна (пересчёт машины), И `pending_other` == 1 ровно когда MARKET-выход подан на последнем баре (иначе 0); расхождение — утечка/дефект. Сами пережившие окно заявки — finding движка в отчёт («конец окна очередь не чистит», поправка design V4.2);
  - V4.3: `fills == строк journal минус висящее window-close (если позиция дожила)`, `expired == submits − fills − pending_limit_buy` — допуск 0; машина обязана моделировать транзенторное перекрытие волн (подача новой на баре S+e при живой старой) — иначе тождество разъедется на каждом e-м баре (owner-решение A3);
  - V4.4: journal pess == journal opt побайтово; метрики вилк равны (внутрибарных споров нет — расхождение = дефект).
- [ ] **Step 2: Commit verify до прогона** (`-m "test(calib): spec_04 verify pinned before run"`).
- [ ] **Step 3: Сетка × 2 символа:**
```powershell
.venv\Scripts\python.exe scripts/calib/harness.py data\btcusdt.db BTCUSDT 1761523200 1790294400 limit_pullback --param depth=0.003,0.005 --param ttl_bars=12,24,48 --param hold_bars=24,48 --out data\calib
.venv\Scripts\python.exe scripts/calib/harness.py data\ethusdt.db ETHUSDT 1761523200 1790294400 limit_pullback --param depth=0.003,0.005 --param ttl_bars=12,24,48 --param hold_bars=24,48 --out data\calib
```
(12 ячеек × 2 вилки × 2 символа ≈ 5–8 мин.)
- [ ] **Step 4:** verify → таблицы; провал V4.x = дефект лаборатории: цифры в отчёт, ядро не трогать.
- [ ] **Step 5:** фолды центральной ячейки (d=0.005, ttl=24, hold=48) × 2 символа × 3 окна + `--double-costs` full × 2 символа (одна ячейка).
- [ ] **Step 6: Отчёт `калибровка_spec_04_limit_pullback_20261028.md`**: части 1/2; G1 измерением (сделки на ячейку); раздел «что показала maker-механика» (pending/expiry/fees расхождения если есть); ссылка на `feat/limit-order-expiry`.
- [ ] **Step 7:** pytest/ruff/mypy; commit `docs(spec_04): limit-pullback maker calibration run`; `git push origin calibration-suite`.

---

### Task 7: Стратегия `dca_grid` (адверсариальная, без self-гейта) + тесты

**Files:**
- Create: `strategylab/strategy/dca_grid.py`
- Modify: `strategylab/strategy/examples.py` (реестр + `build`)
- Test: `tests/test_strategy_dca_grid.py`

**Interfaces:**
- Consumes: `Order(BUY, MARKET, Decimal(1), None, None, False)` (expiry не нужен).
- Produces: `DcaGridStrategy(step: Decimal, layers: Decimal)` — `layers=int()` конвертация, валидация `0 < step < 1`, `layers >= 1`; `from_params`; `name = "dca_grid"`. Логика (design §8 с owner-пометкой A5: геометрия вместо «цены последнего филла» — невыполнима в API, `StrategyState` не отдаёт цены филлов): опорная цена = `close` первого сигнала (bar_index==0); `bought` — счётчик выпущенных лотов; на каждом баре при `position != 0` и `bought < layers + 1` и `bar.close <= опорная·(1−step)**bought` → `[BUY MARKET 1]`. СТОПОВ/ТЕЙКОВ/ВЫХОДОВ/ГЕЙТА `equity<=0` нет — намеренно (design §8: house-гейт «починил» бы измеряемое).

- [ ] **Step 1: Тесты:**
```python
def test_first_buy_on_bar_zero():                 # bar 0 -> BUY; нет само-гейта при отрицательном equity state
def test_layers_at_geometric_levels():            # падение на step пороги -> ровно один BUY на уровень
def test_stops_at_layer_cap():                    # bought == layers+1 -> больше ни одного ордера
def test_no_equity_self_gate():                   # equity=Decimal("-1000"), под уровнем -> BUY ВЫДАН (пин отсутствия гейта)
def test_no_exit_orders_ever():                   # SELL отсутствует на растущем и падающем path
def test_params_validation():
```
- [ ] **Step 2:** FAIL → **Step 3: Реализовать + регистрация** → **Step 4:** PASS; полный pytest/ruff/mypy → **Step 5:** commit `-m "feat(strategy): dca_grid adversarial ladder without account-level gate"`.

---

### Task 8: spec_05 DCA adversarial — verify + сетка + отчёт (E)

**Files:**
- Create: `scripts/calib/verify_spec05.py`
- Create: `strategydocs/калибровка_spec_05_dca_adversarial_20261028.md`

**Interfaces:**
- Consumes: харнесс CLI (`--param step=0.02,0.05 --param layers=4,8`); journal+csv ячеек; перепрогон в `--out data\calib\redo`.
- Produces: боевые цифры для findings записки по ядру (account-stop отсутствует, ликвидация при leverage=1 недостижима) в свод Task 9.

- [ ] **Step 1: `verify_spec05.py`** (4 ячейки × 2 символа):
  - V5.1: у каждой ячейки есть csv-строка (не «ОТКЛОНЕНО»); все Decimal-колонки парсятся; regex-скан файла `(NaN|inf|-inf|Infinity)` пусто;
  - V5.2 (переписан по A4: журнал dca — одна строка на ячейку, cumsum по нему слеп): независимая машина реплея лесенки (подача на баре при `close ≤ close_0·(1−s)^k`, исполнение close[+1]·(1+0.0005), taker) считает по-баровую marked equity `10000 + (close_t − avg_t)·qty_t`; `t_neg` — первый бар с marked ≤ 0 (обязан существовать при −26%/qty=1; если нет — крупное «опровержение pre-registered ожидания» в вывод); **ожидание: слои после t_neg подаются и исполняются, >0** (гейта уровня счёта нет). Сверка с артефактом: из единственной journal-строки выводятся `q = pnl / ((exit − avg) − taker·(exit + avg))` (следует из pnl = q·(exit−avg) − taker·avg·q − taker·exit·q, где avg·q = Σ цен филлов) и сверяется `entry_price`-средник с предсказанием машины: q целое и равно числу предсказанных слоёв (допуск 0), avg == volume-weighted средник предсказанных филлов (1E-20). Совпадение avg пиннит весь набор филлов, не только сумму;
  - V5.3: ни одной строки `ОТКЛОНЕНО ... LiquidationError`; в journal нет `exit_reason == "stop"` (неблагоприятный ход < 100% маржи на весь объём); появление = находка в модель;
  - V5.4: csv-строки `net_pnl`/`equity`/`win_rate` со знаком минус где формула даёт минус (без abs/клиппинга); артефактный `max_drawdown` сверяется с реализованной реконструкцией (при одной сделке == |net|, допуск 1E-20), а **marked-дно** из машины репортится ОТДЕЛЬНЫМ числом; расхождение артефакт vs marked — finding «метрика max_drawdown слепа к нереализованной просадке открытой позиции»;
  - V5.5: повторный прогон ячейки в `data\calib\redo\` → csv/journal/report_p побайтово равны (`Get-FileHash`, не Compare-Object).
- [ ] **Step 2:** commit verify до прогона.
- [ ] **Step 3: Прогон:**
```powershell
.venv\Scripts\python.exe scripts/calib/harness.py data\btcusdt.db BTCUSDT 1761523200 1790294400 dca_grid --param step=0.02,0.05 --param layers=4,8 --out data\calib
.venv\Scripts\python.exe scripts/calib/harness.py data\ethusdt.db ETHUSDT 1761523200 1790294400 dca_grid --param step=0.02,0.05 --param layers=4,8 --out data\calib
```
- [ ] **Step 3a: Прогоны для гейтов (поправка A6, design §8 правило E):** те же команды для каждого из 3 фолд-окон (G5) + `--double-costs` на полном окне × 2 символа (G4) — без этих прогонов публиковать G4/G5 нечем, а N/A без причины design не разрешает.
- [ ] **Step 4:** verify + V5.5 перепрогон → цифры в отчёт.
- [ ] **Step 5: Отчёт `калибровка_spec_05_dca_adversarial_20261028.md`**: баннер «анти-стратегия, никогда не торговать живьём»; часть 1 (гейты красные — публикуем); часть 2 таблица V5.1–V5.5; секция «поведение ядра при equity<0: боевое подтверждение замеченного в записке 2026-10-28» (файл/строки `sim.py::submit`, `engine.py` — отсутствие account-гейта); «ядро по ходу E не изменялось» — подтвердить `git log` диапазоном.
- [ ] **Step 6:** pytest/ruff/mypy; commit `docs(spec_05): dca adversarial kernel stress results`; `git push origin calibration-suite`.

---

### Task 9: Свод набора, финальная публикация, ledger

**Files:**
- Create: `strategydocs/калибровка_набор_итог_20261028.md`
- Локальный ledger: `.superpowers/sdd/2026-10-27-two-sided-momentum/progress.md` (в git не коммитить, если уже игнорируется — проверить `git check-ignore .superpowers`; если НЕ игнорируется — коммитить как есть, это рабочая заметка серии)

- [ ] **Step 1: Сводный отчёт**: таблица 4 спек × {часть 1 вердикт, часть 2 PASS/FAIL, findings}; реестр дефектов лаборатории (B: margin-check отсутствует; A: измеренная предвзятость G1 к частоте; C: всё, что нашли maker-сверки; E: поведение при отрицательной equity + подтверждение/опровержение записки по ядру); реестр подтверждений («конвейер честен в N точках»); вердикт пригодности лаборатории; перекрёстная ссылка на диагностику spec_01.
- [ ] **Step 2: Финальные гейты:** `pytest tests -q` зелёный (≥363 + новые); `ruff check .`; `mypy strategylab scripts`; `git status` чистый; `Test-Path data\touch_registry.json` == False (существует — плохо: только read-only проверка содержимого, не удалять); `git ls-remote --heads origin` → main по-прежнему `5d94e97`.
- [ ] **Step 3:** commit `docs(calib): suite summary - what the lab learned`; `git push origin calibration-suite`.
- [ ] **Step 4:** Ledger-запись: серия завершена, коммиты, ветки на ремооте, список дефектов, «следующая сессия резюмит отсюда».

---

## Self-review чек-лист (перед финишным пушем)

- Каждый критерий V спеки §5–§8 имеет скрипт и строку результата в отчёте — ничего «разберём потом».
- Все прогоны в окнах ⊆ `[1761523200, 1790294400)`; `OutsideFitZone` не срабатывал ни разу (иначе — инцидент в свод).
- Коммиты ядра — только в `feat/limit-order-expiry`; на calibration-suite ядро меняется лишь merge'ом этой ветки.
- Все отчёты содержат «калибровочная, не alpha-претензия» и `touches: holdout=0, live=0`.
- Имена файлов/колонок в verify совпадают с тем, что пишет харнесс (проверить на spec_02 артефактах до написания verify_spec03+).
- Ни один `scripts/calib/verify_*.py` не импортирует `strategylab` (grep `import strategylab` пуст; только сырой `sqlite3` + парсинг csv-артефактов).
- Схема артефактов (equity/win_rate/pending_limit_buy/pending_other/report_p) залита коммитом харнесса ДО первого push (Task 2 Step 9); после заморозки колонки не трогать — иначе baseline-diff Task 4 мусорит.
