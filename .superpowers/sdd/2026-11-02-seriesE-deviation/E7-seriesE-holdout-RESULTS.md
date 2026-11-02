# E-7. Series E — одноразовый holdout-тест 3 выживших клеток (R-next-1)

- **Публикует:** замерщик серии E (R-next-1: числа holdout публикует замерщик первым, до аудита лида).
- **Дата:** 2026-11-03
- **HEAD (замер):** `f24b2e5c4d5dcb0d08ad9bd1efc35fecdecc2a1d`, ветка `calibration-suite`; на старте `git status` чистый. Замер не менял tracked-код: `strategylab/deviation.py` и `scripts/deviation/grid.py` не тронуты.
- **Закон (дословно до прогона):** `.superpowers/sdd/2026-11-02-seriesE-deviation/seriesE-deviation-preregistration-DRAFT.md`, секция «E-7. Одноразовый holdout-тест выживших — FROZEN 2026-11-03(g), владелец „1“»; окно `[1790294400, 1793577600)` (38 суток), критерии H1/H2 заморожены до чисел.
- **Python:** `.venv\Scripts\python.exe` (Python 3.14.6), `PYTHONUTF8=1`; каждый прогон — ровно одно исполнение `scripts/deviation/grid.py` через одноразовый runner `meas/holdout/run_once.py` (полный raw stdout пишется в лог; параметры и sim/CLI не изменяются).

## Прогоны (ровно 4: 3 вооружённые клетки + побайтный rerun; все exit=0, `HOLDOUT_ARMED` напечатан)

| # | log | exit | ключевые строки stdout |
|---|-----|-----:|------------------------|
| 1 | `meas/holdout/E-a3.log` | 0 | `HOLDOUT_ARMED: end_ts 1793577600 > 1790294400 — чтение холдаута вооружено явным --holdout-ok (E-7, одноразово)`; `deviation_version: b19a5e09f75f`; `net_pnl: -1037.527327571256407017600809 annualized_pct: -99.65723014829173383195376192 rounds: 5 events: 26 flat_fold: 0` |
| 2 | `meas/holdout/E-a5.log` | 0 | `HOLDOUT_ARMED: …`; `deviation_version: 36a23dcff8c0`; `net_pnl: -1310.346297535123879112232850 annualized_pct: -125.8622101579790041778855237 rounds: 4 events: 26 flat_fold: 0` |
| 3 | `meas/holdout/E-b5.log` | 0 | `HOLDOUT_ARMED: …`; `deviation_version: d97aeb71a4e9`; `net_pnl: -1217.450082637258047351039816 annualized_pct: -116.9392842533155756008235613 rounds: 7 events: 26 flat_fold: 0` |
| 4 | `meas/holdout/rerun.log` | 0 | повтор прогона 1 с `--out data/calib/holdout_E-a3-rerun`: stdout числа совпадают с прогоном 1 (`b19a5e09f75f`, `-1037.527327571256407017600809`, `-99.65723014829173383195376192`, `rounds: 5 events: 26`) |

Точные команды запуска (дословно, как записано в заголовках логов; обёртка runner — `run_once.py <log> -- <строка>` из корня репо):

```
# 1) E-a3
.venv/Scripts/python.exe scripts/deviation/grid.py --db data/ethusdt.db --symbol ETHUSDT --start 1790294400 --end 1793577600 --cell E-a3 --tol 0.020 --depth 0.003 --conf 30 --vol-filter 2 --exit-mode V1_48 --ctx-hours 960 --out data/calib/holdout_E-a3 --holdout-ok

# 2) E-a5 (та же строка E-7 с --cell E-a5 --conf 60 --out data/calib/holdout_E-a5;
#    порядок флагов при исполнении: --cell, --conf выставлены сразу после --end — argparse-нейтрально,
#    набор флагов и значений ровно как в команде протокола)
.venv/Scripts/python.exe scripts/deviation/grid.py --db data/ethusdt.db --symbol ETHUSDT --start 1790294400 --end 1793577600 --cell E-a5 --conf 60 --tol 0.020 --depth 0.003 --vol-filter 2 --exit-mode V1_48 --ctx-hours 960 --out data/calib/holdout_E-a5 --holdout-ok

# 3) E-b5
.venv/Scripts/python.exe scripts/deviation/grid.py --db data/ethusdt.db --symbol ETHUSDT --start 1790294400 --end 1793577600 --cell E-b5 --tol 0.020 --depth 0.005 --conf 60 --vol-filter 1 --exit-mode V1_48 --ctx-hours 960 --out data/calib/holdout_E-b5 --holdout-ok

# 4) детерминизм-rerun (повтор команды 1, только --out)
.venv/Scripts/python.exe scripts/deviation/grid.py --db data/ethusdt.db --symbol ETHUSDT --start 1790294400 --end 1793577600 --cell E-a3 --tol 0.020 --depth 0.003 --conf 30 --vol-filter 2 --exit-mode V1_48 --ctx-hours 960 --out data/calib/holdout_E-a3-rerun --holdout-ok
```

Других прогонов с `--holdout-ok` на боевых базах не было; `end_ts` везде = 1793577600 (потолок E-7 не превышен, `WARN` не печатался).

## Таблица 3 клеток (точные значения из csv артефактов, без округления)

| cell | zones | events | rounds | stops/takes/tb/forced | price_gross | friction | net_pnl | equity | annualized_pct | deviation_version |
|------|------:|-------:|-------:|-----------------------|-------------|---------:|--------:|-------:|---------------:|-------------------|
| E-a3 | 18 | 26 | 5 | 0/0/5/0 | -977.5273275712564070176008090 | 60.0000 | -1037.527327571256407017600809 | 8962.472672428743592982399191 | -99.65723014829173383195376192 | `b19a5e09f75f` |
| E-a5 | 18 | 26 | 4 | 0/0/4/0 | -1262.346297535123879112232850 | 48.0000 | -1310.346297535123879112232850 | 8689.653702464876120887767150 | -125.8622101579790041778855237 | `36a23dcff8c0` |
| E-b5 | 18 | 26 | 7 | 0/0/7/0 | -1133.450082637258047351039816 | 84.0000 | -1217.450082637258047351039816 | 8782.549917362741952648960184 | -116.9392842533155756008235613 | `d97aeb71a4e9` |

Параметры строк csv (сверка с E-7/E-2-F, факт): все три — ETHUSDT, `[1790294400, 1793577600)`, ctx 960, zone_tf 14400s, touches 2, tol 0.020, capital 10000, fee 0.0006, slip 0.0005, exit V1_48; E-a3 depth 0.003 conf 30 k2, E-a5 depth 0.003 conf 60 k2, E-b5 depth 0.005 conf 60 k1.

## H1 — механический подсчёт (net ≥ 0 у скольких из 3 клеток)

- E-a3: `net_pnl = -1037.527327571256407017600809` ≥ 0 → False
- E-a5: `net_pnl = -1310.346297535123879112232850` ≥ 0 → False
- E-b5: `net_pnl = -1217.450082637258047351039816` ≥ 0 → False

**H1: 0 из 3 клеток имеют net ≥ 0** (планка H1 по E-7: минимум 2 из 3).

## H2 — механический подсчёт (Decimal, скрипт `meas/holdout/h1h2.py`, без float)

Формула E-7: средняя годовая = (Σ net_pnl) / 3 / 10000 × 365/38 × 100, сравнение ≥ 3.0:

```
Σ net_pnl            = -3565.323707743638333480873475
(Σ / 3)              = -1188.4412359145461111602911583333333333333333333333
((Σ / 3) / 10000)    = -0.11884412359145461111602911583333333333333333333333
× 365/38 × 100       = -114.15290818652877120355428231359649122807017543859
```

**H2: средняя годовая = -114.15290818652877120355428231359649122807017543859; сравнение `≥ 3.0` → False.**

## Детерминизм-контроль (sha256: `holdout_E-a3` vs `holdout_E-a3-rerun`)

| файл | sha256 оригинал | sha256 rerun | equal |
|------|-----------------|--------------|:-----:|
| `dev_E-a3_ETHUSDT_1790294400_1793577600.csv` | `1a42f01b3fc31b6abbe4ad8c2152df057e85dc6464bd9914e048c61a9570d981` | `1a42f01b3fc31b6abbe4ad8c2152df057e85dc6464bd9914e048c61a9570d981` | True |
| `dev_E-a3_ETHUSDT_1790294400_1793577600_journal_p.csv` | `2478c2f7a47749b0d9b00be64846feefe35f0fd42b7be526659e8a4debf65d5f` | `2478c2f7a47749b0d9b00be64846feefe35f0fd42b7be526659e8a4debf65d5f` | True |
| `dev_E-a3_ETHUSDT_1790294400_1793577600_report_p.txt` | `4f3015c071ac0d6ca530e38deebe62fab557b7724e9b7f53b720a92098b0b6d5` | `4f3015c071ac0d6ca530e38deebe62fab557b7724e9b7f53b720a92098b0b6d5` | True |

**BYTE_IDENTICAL: True (3/3 файла).**

## Артефакты и инциденты

- Сырые логи (полный stdout) 4 прогонов: `.superpowers/sdd/2026-11-02-seriesE-deviation/meas/holdout/E-a3.log`, `E-a5.log`, `E-b5.log`, `rerun.log`.
- csv/journal/report каждого прогона: `data/calib/holdout_<cell>/` — по 3 файла во всех трёх папках (проверено), в git не идут (`data/` ignored).
- Скрипты: `meas/holdout/run_once.py` (однократный прогон + raw-лог), `meas/holdout/extract_artifacts.py` (csv-числа + sha256), `meas/holdout/h1h2.py` (H1/H2, Decimal).
- Инциденты прогонов: нет — 4/4 exit=0, `HOLDOUT_GUARD`/`WINDOW_GUARD`/`PARAM_GUARD` не срабатывали. Инцидент инструмента замера: первый запуск `h1h2.py` упал на кодировке cp1251 консоли при печати «Σ» (не прогон grid.py; числа прогона не затронуты), скрипт исправлен (stdout → utf-8) и подсчёт повторён — результат приведён выше.

Числа успешных прогонов финальны (E-7 one-shot). GO/NO-GO замерщик не интерпретирует — вердикт за лидом после независимого аудита (`meas/audit_E7_holdout_lead.py`).
