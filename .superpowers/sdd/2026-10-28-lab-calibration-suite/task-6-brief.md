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

