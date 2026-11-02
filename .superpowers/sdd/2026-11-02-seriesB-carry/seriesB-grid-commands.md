# Series B carry — сетка прогонов: транскрипция команд (до чисел)

Статус: транскрибировано ведущим из замороженного
`.superpowers/sdd/2026-11-02-seriesB-carry/seriesB-carry-preregistration.md`
(B-1..B-3) 2026-11-02, ДО чисел серии. Цифр здесь нет — только параметры спека.
**Брат-замерщик перед первым прогоном сверяет этот список со спеком слово в
слово; расхождение = блок, выигрывает спек.** Прогоны запускает замерщик (не
ведущий) — R-next-1: числа публикует брат первым.

Merge-freeze HEAD серии: `922791b6e488997680d4747ca471d09c2b7d29a6`
(branch calibration-suite; ветка `seriesB-carry` смёржена `--no-ff`; ревью
свежими глазами: 0 blockers, 3 MINOR исправлены до merge; гейты на merge HEAD:
504 passed/3 skipped, ruff 0, mypy 47 файлов 0).

Детерминизм-smoke к7-6/B-6 выполнен ведущим на этом HEAD ДО сетки: ячейка
`t05_btc_f1` прогнана дважды независимыми процессами — все три файла артефакта
побайтно равны (SHA256 SAME ×3). В сетку ниже smoke-ячейка не входит.

## Дисциплина запуска

- Работать из корня `C:/Users/egoro/Documents/GitHub/strategylab` на
  calibration-suite с HEAD = коммит листа команд (хэш записать в шапку ответа
  до первого прогона; обязательное условие: `git diff 922791b6 HEAD --
  strategylab scripts tests` пуст — код идентичен merge-freeze).
- `$env:PYTHONUTF8="1"`; python: `.venv\Scripts\python.exe`; PowerShell «;».
- ОДИН вызов = один процесс = одна клетка-фолд. 18 запусков.
- Данные: `data/btcusdt.db` (BTCUSDT), `data/ethusdt.db` (ETHUSDT).
- Фолды: f1 `1761523200 1771113600`, f2 `1771113600 1780704000`,
  f3 `1780704000 1790294400` (end exclusive). Holdout ≥ 1790294400 ни в одной
  команде (R-next-2); раннер имеет HARD-guard: end > 1790294400 → `HOLDOUT_GUARD`
  и exit 2.
- `--out` уникален на запуск. Суффиксы клеток: t05 = θ(0.00005/0.00003),
  t10 = θ(0.00010/0.00005), t20 = θ(0.00020/0.00010); btc/eth; f1..f3.
- Резерв: при сбое ячейки повтор с суффиксом `_retryN` (≤3 повтора на серию,
  к7-5), пометка в лог.
- Коды выхода: `0` — успех, stdout содержит сводную строку (net/annualized/
  n_settles/holds/margin_call/flat_fold) — переносить в ранлог дословно;
  `2` — guard (HOLDOUT_GUARD / PARAM_GUARD / WINDOW_GUARD) = команда против
  спека, это НЕ ретрай, а остановка и вопрос ведущему;
  `4` — `LIQUIDATED_RUN_DEAD`: маржа короткой ноги съедена (≥100% N0),
  артефактов нет — клетка мертва по правилам, сетку продолжать, зафиксировать
  в письме отдельной строкой.
- Все параметры перечислять явно (lev=1, capital=10000, taker 0.0006,
  slip 0.0005 — дефолты, но явная запись документирует клетку; carry_version
  от явности не меняется — значения канонизируются Decimal-строкой).
- Префикс:

```
$env:PYTHONUTF8="1"
$PY="C:\Users\egoro\Documents\GitHub\strategylab\.venv\Scripts\python.exe"
$G="scripts/carry/grid.py"
```

## t05 — θ_entry 0.00005 / θ_exit 0.00003 (6 прогонов)

```
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1761523200 --end 1771113600 --theta-entry 0.00005 --theta-exit 0.00003 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t05_btc_f1
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1771113600 --end 1780704000 --theta-entry 0.00005 --theta-exit 0.00003 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t05_btc_f2
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1780704000 --end 1790294400 --theta-entry 0.00005 --theta-exit 0.00003 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t05_btc_f3
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1761523200 --end 1771113600 --theta-entry 0.00005 --theta-exit 0.00003 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t05_eth_f1
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1771113600 --end 1780704000 --theta-entry 0.00005 --theta-exit 0.00003 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t05_eth_f2
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1780704000 --end 1790294400 --theta-entry 0.00005 --theta-exit 0.00003 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t05_eth_f3
```

## t10 — θ_entry 0.00010 / θ_exit 0.00005 (6 прогонов)

```
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1761523200 --end 1771113600 --theta-entry 0.00010 --theta-exit 0.00005 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t10_btc_f1
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1771113600 --end 1780704000 --theta-entry 0.00010 --theta-exit 0.00005 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t10_btc_f2
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1780704000 --end 1790294400 --theta-entry 0.00010 --theta-exit 0.00005 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t10_btc_f3
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1761523200 --end 1771113600 --theta-entry 0.00010 --theta-exit 0.00005 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t10_eth_f1
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1771113600 --end 1780704000 --theta-entry 0.00010 --theta-exit 0.00005 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t10_eth_f2
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1780704000 --end 1790294400 --theta-entry 0.00010 --theta-exit 0.00005 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t10_eth_f3
```

## t20 — θ_entry 0.00020 / θ_exit 0.00010 (6 прогонов)

```
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1761523200 --end 1771113600 --theta-entry 0.00020 --theta-exit 0.00010 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t20_btc_f1
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1771113600 --end 1780704000 --theta-entry 0.00020 --theta-exit 0.00010 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t20_btc_f2
& $PY $G --db data/btcusdt.db --symbol BTCUSDT --start 1780704000 --end 1790294400 --theta-entry 0.00020 --theta-exit 0.00010 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t20_btc_f3
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1761523200 --end 1771113600 --theta-entry 0.00020 --theta-exit 0.00010 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t20_eth_f1
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1771113600 --end 1780704000 --theta-entry 0.00020 --theta-exit 0.00010 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t20_eth_f2
& $PY $G --db data/ethusdt.db --symbol ETHUSDT --start 1780704000 --end 1790294400 --theta-entry 0.00020 --theta-exit 0.00010 --lev 1 --capital 10000 --taker-fee 0.0006 --slippage 0.0005 --out data/calib/rB_t20_eth_f3
```

## Что требует письмо замерщика (итого — 18 клеток-строк + фолдовые числа)

Для каждой клетки: carry_version, по каждому фолду — net_pnl ($), annualized
(%/год), n_settles (засчитанные), holds, funding_gross ($), friction ($),
max_leg_dd_frac (доля маржи), margin_call (0/1), flat_fold (0/1); затем
агрегат клетки (сумма net трёх фолдов, суммарные дни → годовая). Правило 1-
аналог: n < 30 засчитанных на агрегат клетки — пометить отдельно. Никаких
вердиктов по B2 — только числа; вердикты считает ведущий независимо из
артефактов (B-3).
