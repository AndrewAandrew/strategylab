# Ревью ветки feat/fundability-filter (fresh eyes, брат-ревьюер)

Дата: **2026-11-01**. Ревьюер: брат-ревьюер (fresh eyes), код не менял.
Диапазон: **b880bf9..822555c** (f277c82, fd89733, 822555c поверх b880bf9).
Прогон: worktree `…/Temp/opencode/wt_fundability` @ 822555c (чистый, совпадает с
`origin/feat/fundability-filter`; merge-base с HEAD основного репо — 822555c не предок, ветка не слита).
Контракты: round5-preregistration.md (§1, §3, §4/§5, §9.2, §11) и round4-preregistration.md (§4, §6 + аменд свежести) прочитаны до кода.

## A. Правило skip (§1, §11)

**PASS.**
- J (`jaguar_ema.py:167–171`): `risk = close − EMA34(t)` на триггерном баре, `rho = risk / bar.close` —
  close триггерного (подающего) бара. Сравнение строгое `<` (равенство торгуется). `return []` стоит
  ДО `self._entry_pending = True` / `self._stop = slow` / сайзинга — при skip не проставляется ни одно
  поле сделки; тест ассертит все пять полей `(False, None, None, False, False)`. `_entry_signal` —
  чистое чтение (p1/p2 EMA), история сдвигается в конце `on_bar` как обычно.
- S (`smc_choch_fvg.py:491–492`): `rho = fvg.risk / bar.close`, где `bar` — тот самый бар S, с которым
  `on_bar` вызывает `_entry_order` (fresh-pair матчится по `index == max(c,f)+2` на этом баре).
  НЕ mid, НЕ база FVG-бара. `return None` ДО `_entry_pending/_signal_bar/_mid/_sl/_risk`.
- S skip ≡ обычный отказ пары: пара CHoCH+FVG сгорает (clear `_*_choch_bar/_*_fvg`, строки 483–488)
  ДО всех проверок отказа — и до skip, и до `R<=0/equity<=0`. Это дословный контракт round 4
  («пара сгорает на баре S»), не баг; закреплено тестом (`bull_choch_bar is None`, `bull_fvg is None`,
  `in_trade is False`). Следующая пара обрабатывается как обычно (лента `_TIGHT_THEN_WIDE`: skip 19 →
  сделка 26).
- База = close подающего бара у ОБОИХ классов = та цена, по которой `_guard_submit` (engine.py:138)
  считает notional → алгебра «краш на входе исключён» (§3) держится именно на этой базе.
- Decimal без float-загрязнения: оба деления — Decimal/Decimal; дефолт `Decimal("0.00025")`;
  в тестах только Decimal-литералы; ни одного `float` в диффе.

## B. Дефолт и валидация параметра

**PASS.**
- `fund_floor: Decimal = Decimal("0.00025")` в обоих конструкторах (= r/lev при 0.005/20, к5-4);
  добавлен в `PARAM_NAMES` обоих; `from_params`/`build` пропускают, неизвестные — ValueError (прежний тест).
- `positive(fund_floor, "fund_floor")` → ValueError при ≤0, покрыто тестами обоих классов.
- Строка `--param` — общая механика репо (`_parse_param` cli.py / `_parse_grid` sweep.py):
  `Decimal(raw)` с ловушкой `InvalidOperation` → `argparse.ArgumentTypeError`; значение дальше
  валидируется конструктором (ValueError). Харнесс `scripts/calib/harness.py` берёт defaults из
  сигнатуры (`_defaults`), поэтому `--param fund_floor=0.0015` и `--leverage 20` для раунда 5 работают
  без доработок (проверено чтением кода). См. минор m-4 про формулировку «невалидное → ValueError».

## C. Поверхность изменений

**PASS.** `git log --name-only b880bf9..822555c` (по-коммитно, чтобы поймать touch-then-revert):
- f277c82: `strategylab/strategy/jaguar_ema.py`, `tests/test_strategy_jaguar.py`
- fd89733: `strategylab/strategy/smc_choch_fvg.py`
- 822555c: `tests/test_strategy_jaguar.py`, `tests/test_strategy_smc.py`

Ровно 4 файла. Ядро (`core/*`), `broker/sim.py`, харнесс (`scripts/calib/harness.py`, `scripts/sweep.py`),
`strategy/examples.py`, `cli.py` — **ноль изменений**.

## D. Особые точки отчёта имплементатора

1. **PASS.** `test_defaults_match_preregistration_k1`: снимок остался exact-equality по всему словарю
   дефолтов (добавлен ровно `"fund_floor": FUND_FLOOR`), `PARAM_NAMES` — точный кортеж. Ассерты не
   ослаблены (никакого subset/`in`). У S — новый `test_fund_floor_registered_with_frozen_default`:
   default == `Decimal("0.00025")` и полный кортеж PARAM_NAMES. Старые S-тесты не тронуты (diff —
   только additions).
2. **PASS.** Смена сигнатуры `_entry_order(state)` → `_entry_order(bar, state)`: единственный
   продакшн-вызов (smc_choch_fvg.py:360) и все тестовые вызовы обновлены (grep по репо — битых
   вызовов нет). Метод приватный. Конвенция пакета подтверждена: `trend_pullback._entry_order(bar15, state)`
   и `sweep_liquidity._entry_order(bar_h1, state)` — та же форма `(bar, state)`; `flat_keltner` остался
   `(state)`, но вне скоупа раунда (нет fund_floor).
3. **PASS с оговоркой (→ m-2).** Structural-находка подтверждена алгеброй по коду: long —
   `mid = (H(t−2)+L(t))/2 ≥ min_low`, `SL = min_low·0.9995` ⇒ `R ≥ 0.0005·min_low`; short зеркально
   `R ≥ 0.0005·max_high`. Тогда `rho < 0.00025` требует `close_S > 2×extreme` окна FVG за ≤2 бара —
   на 1м BTC/ETH физически недостижимо. Следствие: при замороженном дефолте S-skip практически
   мёртв. Это НЕ делает S-тесты фикциями: предикт и wiring покрыты честно — predicate на одном и том
   же коде при любом значении floor, wiring (bar = бар S) — engine-тестом на баре 19 через
   `Engine().run`, а используемые floors санкционированы контрактом/математикой (0.0015 = к5 S-wide;
   0.001 = r/lev при r=0.02, lev=20; 0.0004 — «ниже rho» контроль). Но читать это надо так: в
   первичных ячейках S фильтр — формальность (в точности то, что говорит к5-4 «торгуем всё
   проходимое»); реально S-skip работает в широкополых диагностиках.
4. **PASS.** `_RecordingBroker(intrabar=PESSIMISTIC, costs=COSTS)` — costs optional с прежним
   дефолтом `COSTS`; класс живёт только в tests/, пакет не тронут. Нужен только для левериджа 20 в
   контрфактических MarginError-тестах J.

## E. Тесты против §9.2 (все шесть групп asserted)

**PASS.** (+9 тестов: 473 = 464 базовых + 9; 3 skipped без изменений.)
1. **skip → ноль заявок + поздний wide торгуется.** J: unit-лента (skip 60, торги нет, все поля
   сброшены; wide на 111 торгуется BUY) + engine-блок (fills == [], `pending_orders == []`,
   позиция флэт). S: engine-level `_run` через `Engine`: `orders[19] == []`, `in_trade[19] is False`,
   `broker.log == []`, единственный торгуемый бар — 26 (лимитка ровно 109.25, qty = CAP·0.01/14.2975).
2. **Бескрашовость при floor=r/lev + контрфактический MarginError.** Оба класса — реальные
   `Engine().run`. Контрфактик не имитирован: `MarginError` определён в sim.py, но `raise MarginError`
   в sim.py отсутствует — поднимает ТОЛЬКО `_guard_submit` (engine.py:133–142), т.е. тест проходит
   настоящий гейт. J: rho tight 0.0002422 → floor 0.00025 чисто; тот же сигнал при floor 0.00024 →
   MarginError (required ≈ 1.03·equity); wide rho 0.0002611 → маркет исполняется. S: r 0.02,
   floor 0.00025 → MarginError (required ≈ 2.02·equity); r 0.02 при floor 0.001 = r/lev →
   пара skipнута, прогон чист; r 0.005 при дефолте → филл на 20 по цене лимитки (maker).
3. **Дефолт/строка.** J: exact-snapshot дефолтов (к1+к5-4), registry, `from_params`, `build`,
   ValueError на 0/отрицательное, проводка поведения (floor 0.001 skip-ит wide-сигнал, дефолт —
   торгует). S: frozen default + кортеж PARAM_NAMES + ValueError. CLI-строка — общая механика (m-4).
4. **База rho.** J: `R/close(100.001) < floor` → skip, хотя `R/mid(99.8) > floor` торговался бы —
   знаменатель зафиксирован. S (лучший тест): `R/mid = 0.1/100 = 0.001 == floor` (на mid-базе
   торговалась бы), `R/close_S = 0.1/100.05 < floor` → skip; зеркально close 99.95 → лимитка в mid.
   Ровно нужная дискриминация «R/mid≥floor, R/close_S<floor ⇒ skip».
5. **Равенство торгуется.** J: `0.025/100 == 0.00025` → заявка; `0.025/99.97501` → skip (строгое `<`).
   S: `0.115/115 == 0.001` → лимитка; `0.115/115.01` → skip + пара израсходована + `in_trade is False`.
   Что равенство required=marked проходит сам гейт — покрыто существующими fix1-тестами
   (test_engine_margin_gate.py:97, test_calib_harness.py:346); §11 опирается именно на fix1 — новых
   engine-тестов равенства ветка не добавляла, достаточно.
6. **Прежние зелёные.** База (2bc51ce = код b880bf9): 464 passed / 3 skipped. Ветка: 473/3.
   Ни один прежний тест не удалён и не ослаблен (по диффам: изменены только snapshot-тест дефолтов
   J — усилен — и init `_RecordingBroker` — параметр добавлен с прежним дефолтом).

Тесты не тавтологичны: oracle-EMA считаются независимым прогоном `Ema(13)/Ema(34)` вне стратегии;
контрфактические MarginError идут через Engine/`_guard_submit`; «сигнал вообще жив на этой ленте»
доказан парным контрфактическим прогоном (тот же тейп, отличается только floor).

## F. Гейты (мои прогоны, не цифры имплементатора)

| Гейт | Команда (cwd = wt_fundability, venv основного репо) | Результат | Exit |
|---|---|---|---|
| pytest | `python -m pytest -q` (PYTHONUTF8=1) | **473 passed, 3 skipped** (5.65s) | 0 |
| pytest базы | там же в основном репо @ 2bc51ce (код = b880bf9) | 464 passed, 3 skipped | 0 |
| ruff (всё) | `python -m ruff check .` | 21 ошибка — **все в `.superpowers/**`** (cmp_cell.py, cmp_fix2.py, meas_funding_round3.py, replay_dca_round4.py — ledger-скрипты). Pre-existing, не блокер по решению | 1 |
| ruff (скоуп) | `python -m ruff check strategylab tests scripts` | **All checks passed!** | 0 |
| mypy (пакет) | `python -m mypy strategylab` | **Success: no issues found in 37 source files** | 0 |
| mypy (полный гейт репо) | `python -m mypy strategylab scripts` | **Success: no issues found in 45 source files** | 0 |
| mypy (для сведения) | `python -m mypy strategylab tests` | 45 errors in 11 files (база: 44) → +1, см. m-1 | 1 |

**Разбор «45 против 37».** Это скоупы, а не расхождение: записи state.md («гейты 464/3+0+0 (45 файлов)»,
«mypy 0 (44 файла)» и т.п.) — это документированный полный гейт `mypy strategylab scripts`
(37 файлов пакета + 8 scripts = 45). Отчёт имплементатора «37» — `mypy strategylab` (только пакет).
Оба скоупа зелёные (0 ошибок) и на базе b880bf9/2bc51ce, и на ветке 822555c. `tests/` вне гейта mypy —
закреплено ещё review-funding.md (FIND-3), поэтому «45 errors в tests» — не регрессия гейта, но одна
ошибка там новая этой веткой (m-1).

## Блокеры

**Блокеров нет.** Merge не запрещён ничем из A–F.

## Миноры (не блокируют merge)

- **m-1.** Ветка добавляет одну mypy-ошибку вне гейта: `tests/test_strategy_jaguar.py:667` —
  `build(...)` возвращает `Strategy`, аргумент передан в `_drive(JaguarEmaStrategy)` (arg-type).
  `tests/` не входит в гейт `mypy strategylab scripts`, поэтому не блокирует; починить по желанию
  (`JaguarEmaStrategy(fund_floor=Decimal("0.001"))` вместо `build` или narrow через isinstance).
- **m-2.** При замороженном дефолте 0.00025 S-skip структурно недостижим (`R ≥ 0.0005·extreme` окна
  FVG ⇒ `rho < 0.00025` требует `close_S > 2×extreme` за ≤2 бара). Первичные ячейки S effectively
  без skip — это ровно к5-4 («торгуем всё проходимое»), противоречия контракту нет; реально фильтр
  для S работает в широкополых диагностиках (0.0015). Записать в методологию брата, чтобы числа S
  не читались как «фильтр спас».
- **m-3.** Новое деление на `bar.close` без guard от нуля: при гипотетическом close=0 у шорт-ветки J
  (`slow − 0 > 0`) — `Decimal DivisionByZero` до skip-проверки (лонг упирается в `risk <= 0` раньше).
  Реальные бары (bybit 1m/агрегаты) всегда с положительным close; при close=0 прогон мёртв и без
  этого. Теоретический нано-минор, фикса не требуется.
- **m-4.** Формулировка «невалидная строка --param → ValueError» верна на уровне стратегии
  (`positive()` → ValueError, покрыто тестами); не-Decimal строка отклоняется ещё в argparse как
  `ArgumentTypeError` (exit 2). Это pre-existing общая механика `_parse_param`/`_parse_grid`, ветка
  её не трогала — зафиксировано ради точности отчёта.

## Итог

**VERDICT: PASS** — merge `feat/fundability-filter` (b880bf9..822555c) разрешён; блокеров нет, 4 минора
не блокируют.
