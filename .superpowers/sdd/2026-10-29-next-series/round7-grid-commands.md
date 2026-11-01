# Раунд 7 — сетка прогонов: транскрипция команд (до чисел)

Статус: транскрибировано ведущим из замороженного `round7-preregistration.md`
(§1–§5) 2026-11-01, ДО чисел раунда. Цифр здесь нет — только параметры спека.
**Брат-замерщик перед первым прогоном сверяет этот список со спеком слово в
слово; расхождение = блок, выигрывает спек.** Прогоны запускает замерщик (не
ведущий).

Merge-freeze HEAD раунда: `1d404c5964cf4d682a18b784068147c104f0f351`
(branch calibration-suite, ревью свежими глазами APPROVE; ветка `r7-be-trail`
смёржена `--no-ff`).

Регрессионный smoke к7-6(ii) выполнен ведущим на этом HEAD ДО сетки: дефолтный
`j_h_btc_f1` (без be_mult) — все четыре файла артефакта побайтно равны
`data/calib/r6_j_h_btc_f1` (SHA256 SAME ×4). В сетку ниже smoke-ячейка не
входит — контроль tp1.5/be-off = r6-артефакты на диске (к7-3).

## Дисциплина запуска

- Работать из корня `C:/Users/egoro/Documents/GitHub/strategylab` на
  calibration-suite с HEAD = merge-freeze коммита (хэш записать в шапку ответа
  до первого прогона).
- `$env:PYTHONUTF8="1"`; python: `.venv\Scripts\python.exe`; PowerShell «;».
- ОДИН вызов харнесса = один процесс = одна ячейка-фолд. 24 запуска, сеток
  значений внутри одного `--param` нет.
- Данные: `data/btcusdt.db` (BTCUSDT), `data/ethusdt.db` (ETHUSDT).
- Фолды: f1 `1761523200 1771113600`, f2 `1771113600 1780704000`,
  f3 `1780704000 1790294400`. Holdout ≥ 1790294400 ни в одной команде
  (R-next-2).
- `--out` уникален на запуск. Суффиксы ячеек: j_b1_h = J tp1.5+be1.0 (1h),
  j_b25_h = J tp2.5+be1.0 (1h), s_tp25_15 = S tp2.5 (15m),
  s_tp60_15 = S tp6.0 (15m); btc/eth; f1..f3.
- Резерв: при сбое ячейки повтор с суффиксом `_retryN` (≤3 повторов на раунд,
  к7-5), пометка в лог.
- Все четыре параметра J перечислять явно (tp_mult=1.5 = дефолт класса, но
  явная запись документирует клетку; spec_version от явности не меняется —
  значения канонизируются по сути).
- Префикс:

```
$env:PYTHONUTF8="1"
$PY="C:\Users\egoro\Documents\GitHub\strategylab\.venv\Scripts\python.exe"
$H="scripts/calib/harness.py"
```

## Первичные JAGUAR — 12 (2 клетки × 2 символа × 3 фолда)

j_b1_h (tp 1.5 + безубыточный рэтчет на 1R):

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=1.5 --param be_mult=1.0 --out data/calib/r7_j_b1_h_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=1.5 --param be_mult=1.0 --out data/calib/r7_j_b1_h_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=1.5 --param be_mult=1.0 --out data/calib/r7_j_b1_h_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=1.5 --param be_mult=1.0 --out data/calib/r7_j_b1_h_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=1.5 --param be_mult=1.0 --out data/calib/r7_j_b1_h_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=1.5 --param be_mult=1.0 --out data/calib/r7_j_b1_h_eth_f3
```

j_b25_h (tp 2.5 + рэтчет на 1R):

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.5 --param be_mult=1.0 --out data/calib/r7_j_b25_h_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.5 --param be_mult=1.0 --out data/calib/r7_j_b25_h_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.5 --param be_mult=1.0 --out data/calib/r7_j_b25_h_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.5 --param be_mult=1.0 --out data/calib/r7_j_b25_h_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.5 --param be_mult=1.0 --out data/calib/r7_j_b25_h_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 jaguar_ema --interval hour --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.5 --param be_mult=1.0 --out data/calib/r7_j_b25_h_eth_f3
```

## Первичные SMC — 12 (2 клетки × 2 символа × 3 фолда)

s_tp25_15 (tp 2.5, окна ttl 4 / hold 16):

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=2.5 --out data/calib/r7_s_tp25_15_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=2.5 --out data/calib/r7_s_tp25_15_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=2.5 --out data/calib/r7_s_tp25_15_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=2.5 --out data/calib/r7_s_tp25_15_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=2.5 --out data/calib/r7_s_tp25_15_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=2.5 --out data/calib/r7_s_tp25_15_eth_f3
```

s_tp60_15 (tp 6.0):

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=6.0 --out data/calib/r7_s_tp60_15_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=6.0 --out data/calib/r7_s_tp60_15_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=6.0 --out data/calib/r7_s_tp60_15_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=6.0 --out data/calib/r7_s_tp60_15_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=6.0 --out data/calib/r7_s_tp60_15_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 smc_choch_fvg --interval 15m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param ttl_bars=4 --param max_hold=16 --param tp_mult=6.0 --out data/calib/r7_s_tp60_15_eth_f3
```

Диагностических клеток в раунде 7 нет (к7-1: ось не TF). Правила выживания:
§8 раунда 4 п.1–5 дословно + правило 6 (долларовое, аменд 1) — впервые в бою;
в письме с числами проценты и доллары — рядом в каждой таблице (к7-6(i)).
