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
  - V5.4: csv-строки `net_pnl`/`equity`/`sharpe` со знаком минус где формула даёт минус (без abs/клиппинга; win_rate не может быть отрицательным — wins/trades ∈ [0,1], поправка fresh-eyes Task 1 №7, в design §8 требует owner-отметки); артефактный `max_drawdown` сверяется с реализованной реконструкцией (при одной сделке == |net|, допуск 1E-20), а **marked-дно** из машины репортится ОТДЕЛЬНЫМ числом; расхождение артефакт vs marked — finding «метрика max_drawdown слепа к нереализованной просадке открытой позиции»;
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
