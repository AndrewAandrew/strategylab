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

