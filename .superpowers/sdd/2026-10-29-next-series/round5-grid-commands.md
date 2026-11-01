# Раунд 5 — сетка прогонов: транскрипция команд (до чисел)

Статус: транскрибировано ведущим из замороженного `round5-preregistration.md`
(§3–§6) 2026-11-01, ДО слияния ветки и ДО любых чисел. Цифр здесь нет — только
параметры из спека. **Брат-замерщик перед первым прогоном сверяет этот список со
спеком слово в слово; расхождение спека и списка = блок, выигрывает спек.**
Прогоны по этому списку запускает брат (R-next-1), не ведущий.

## Дисциплина запуска

- Работать из корня основного репо `C:/Users/egoro/Documents/GitHub/strategylab`
  на calibration-suite ПОСЛЕ merge ветки `feat/fundability-filter` (хэш merge-коммита
  записать в шапку ответа до первого прогона).
- `$env:PYTHONUTF8="1"`; python: `.venv\Scripts\python.exe`.
- ОДИН вызов харнесса = один процесс = одна ячейка-фолд (27 отдельных запусков,
  никаких сеток `в1,в2` внутри одного --param).
- Данные: `data/btcusdt.db` (BTCUSDT), `data/ethusdt.db` (ETHUSDT), bars_1m.
- Фолды: f1 `1761523200 1771113600`, f2 `1771113600 1780704000`,
  f3 `1780704000 1790294400`. Holdout ≥ 1790294400 не вызывается ни в одной команде.
- `--out` уникален на запуск (неперетираемые артефакты), суффиксы: p=первичная,
  w=wide-диагностика, t2=J-tp2; символы btc/eth; фолды f1..f3.
- Общий префикс (не повторён в командах ради читаемости):

```
$env:PYTHONUTF8="1"
$PY="C:\Users\egoro\Documents\GitHub\strategylab\.venv\Scripts\python.exe"
$H="scripts/calib/harness.py"
```

Команды (27), разделитель PowerShell «;»:

## Первичные — 12

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_j_p_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_j_p_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_j_p_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_j_p_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_j_p_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_j_p_eth_f3
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_s_p_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_s_p_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_s_p_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_s_p_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_s_p_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --out data/calib/r5_s_p_eth_f3
```

## Диагностические — 15 (в первичный вердикт клетки не входят)

J-wide (`fund_floor=0.001`), 6:

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.001 --out data/calib/r5_j_w_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.001 --out data/calib/r5_j_w_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.001 --out data/calib/r5_j_w_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.001 --out data/calib/r5_j_w_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.001 --out data/calib/r5_j_w_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.001 --out data/calib/r5_j_w_eth_f3
```

S-wide (`fund_floor=0.0015`), 6:

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.0015 --out data/calib/r5_s_w_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.0015 --out data/calib/r5_s_w_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.0015 --out data/calib/r5_s_w_btc_f3
& $PY $H data/ethusdt.db ETHUSDT 1761523200 1771113600 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.0015 --out data/calib/r5_s_w_eth_f1
& $PY $H data/ethusdt.db ETHUSDT 1771113600 1780704000 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.0015 --out data/calib/r5_s_w_eth_f2
& $PY $H data/ethusdt.db ETHUSDT 1780704000 1790294400 smc_choch_fvg --interval 1m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.0015 --out data/calib/r5_s_w_eth_f3
```

J-tp2 (`tp_mult=2.0`, первичный floor, только BTC), 3:

```
& $PY $H data/btcusdt.db BTCUSDT 1761523200 1771113600 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.0 --out data/calib/r5_j_t2_btc_f1
& $PY $H data/btcusdt.db BTCUSDT 1771113600 1780704000 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.0 --out data/calib/r5_j_t2_btc_f2
& $PY $H data/btcusdt.db BTCUSDT 1780704000 1790294400 jaguar_ema --interval 5m --leverage 20 --capital 10000 --param risk_frac=0.005 --param fund_floor=0.00025 --param tp_mult=2.0 --out data/calib/r5_j_t2_btc_f3
```

Итого: 12 + 6 + 6 + 3 = 27 запуска (бюджет к5-5: ≤27 из 36). `fund_floor` и
`tp_mult` передаются строками, харнесс конвертирует через `from_params` → Decimal.

## Особые случаи (зарегистрированы до чисел)

- Смерть прогона (`SystemExit`, трейсбек LIQUIDATED_RUN_DEAD) — фиксировать строкой
  «ОТКЛОНЕНО: LIQUIDATED_RUN_DEAD (правила 1/3 FAIL)» + имя ячейки; артефактов может
  не быть, это ожидаемо (§3).
- Ноль сделок в живой ячейке — тоже провал правила 1, отличается от смерти прогона
  наличием CSV с нулевым trades.
- B-колонка S (maker) — вторичная арифметическая колонка тех же исполненных сделок,
  не отдельный прогон (§5 round 4 к5); формулировку арифметики брат пишет первой
  строкой методологии ДО таблиц.
