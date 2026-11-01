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

