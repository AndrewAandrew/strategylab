# Раунд 6 — сетка прогонов: транскрипция команд (до чисел)

Статус: транскрибировано ведущим из замороженного `round6-preregistration.md`
(§4–§6) 2026-11-01, ДО любых чисел раунда. Цифр здесь нет — только параметры спека.
**Брат-замерщик перед первым прогоном сверяет этот список со спеком слово в слово;
расхождение = блок, выигрывает спек.** Прогоны запускает замерщик (не ведущий).

## Дисциплина запуска

- Работать из корня `C:/Users/egoro/Documents/GitHub/strategylab` на calibration-suite
  с HEAD = merge-freeze коммита раунда 6 (хэш записать в шапку ответа до первого прогона).
- `$env:PYTHONUTF8="1"`; python: `.venv\Scripts\python.exe`; PowerShell «;».
- ОДИН вызов харнесса = один процесс = одна ячейка-фолд. 24 запуска, сеток `в1,в2`
  внутри одного `--param` нет.
- Данные: `data/btcusdt.db` (BTCUSDT), `data/ethusdt.db` (ETHUSDT).
- Фолды: f1 `1761523200 1771113600`, f2 `1771113600 1780704000`,
  f3 `1780704000 1790294400`. Holdout ≥ 1790294400 ни в одной команде (R-next-2).
- `--out` уникален на запуск. Суффиксы ячеек: j_h = J первичный (1h), j_15 = J диаг
  (15m), s_15 = S первичный (15m), s_5 = S диаг (5m); btc/eth; f1..f3.
- Резерв: при сбое ячейки повтор с суффиксом `_retryN` (≤3 повторов на раунд, к6-5),
  пометка в лог.
- Префикс:

```
$env:PYTHONUTF8="1"
$PY="C:\Users\egoro\Documents\GitHub\strategylab\.venv\Scripts\python.exe"
$H="scripts/calib/harness.py"
```

## Первичные — 12 (J 1h × 6, S 15m × 6)

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_h_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_h_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_h_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_h_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_h_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_h_eth_f3
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --out data/calib/r6_s_15_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --out data/calib/r6_s_15_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --out data/calib/r6_s_15_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --out data/calib/r6_s_15_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --out data/calib/r6_s_15_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --out data/calib/r6_s_15_eth_f3
```

tp_mult НЕ передаётся: дефолты классов (J 1.5, S 4.0) — к6-4. ttl/hold НЕ передавать
J (нет таких PARAM). `hour`/`15m` — валидные ключи `_INTERVALS` харнесса.

## Диагностические — 12 (не для спецификации: J 15m × 6, S 5m × 6)

J 15m:

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 jaguar_ema --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_15_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 jaguar_ema --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_15_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 jaguar_ema --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_15_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 jaguar_ema --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_15_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 jaguar_ema --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_15_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 jaguar_ema --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r6_j_15_eth_f3
```

S 5m (окна 12/48 баров = 60/240 мин реального времени, к6-3):

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 smc_choch_fvg --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=12 --param max_hold=48 --out data/calib/r6_s_5_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 smc_choch_fvg --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=12 --param max_hold=48 --out data/calib/r6_s_5_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 smc_choch_fvg --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=12 --param max_hold=48 --out data/calib/r6_s_5_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 smc_choch_fvg --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=12 --param max_hold=48 --out data/calib/r6_s_5_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 smc_choch_fvg --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=12 --param max_hold=48 --out data/calib/r6_s_5_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 smc_choch_fvg --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=12 --param max_hold=48 --out data/calib/r6_s_5_eth_f3
```

Итого 12 + 12 = 24 запуска (бюджет к6-5, резерв ≤3).

## Особые случаи (зарегистрированы до чисел)

- Смерть прогона (`SystemExit`/LiquidationError-трейсбек) = **LIQUIDATED_RUN_DEAD =
  провал правил 1/3** (наследование §3 round 5); отсутствие артефактов ожидаемо.
- Ноль исполненных в живой ячейке — провал правила 1; на wide-TF сигналов меньше по
  природе (k6-6), но это не основание менять порог.
- Колонка B S (maker) — вторичная арифметика тех же исполненных сделок; формулу
  брат пишет первой строкой методологии ДО таблиц (как round 4/5).
- Skip-фильтр на floor 0.00025 на этих TF структурно пассивен (медианный rho в
  1.5–3 раза выше floor) — обязательная строка методологии (к6-6).
