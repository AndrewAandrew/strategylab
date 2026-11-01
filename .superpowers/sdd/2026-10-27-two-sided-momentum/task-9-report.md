# Task 9 report: Переворот встречным сигналом

Branch `spec01-two-sided-momentum`. Commit `3dce9d3 feat: reversing breakout on opposite signal` — ровно три файла, как Step 6.

## Что реализовано

**`strategylab/strategy/two_sided_momentum.py`**

1. `_entry_order`: гейт `state.position != 0` снят с ранней позиции и перенесён
   после резолва стороны (раньше был `if state.position != 0 or self._pending_entry is not
   None: return None` — теперь pending-гейт остался ранним, а позиция смотрит только после
   определения сайда и фильтра). Встречный сигнал при `position != 0`:
   `reversing = (position > 0 and side is Side.SELL) or (position < 0 and side is Side.BUY)`;
   если не reversing — `return None` (однонаправленный lockout прежний); если reversing —
   `qty += abs(position)` к тому же риск-сайзингу `equity · _RISK_FRAC / (atr_mult · atr)`
   на ATR этого сигнального бара. Ордер — ровно один
   `Order(opp_side, OrderKind.MARKET, abs(position) + qty, None, None, False)`.
   `_pending_entry`/`_pending_atr` переводятся на сторону/ATR сигнала общим финальным
   кодом — никакой отдельной ветки pending для реверса нет.
2. Брекет новой ноги — существующий путь `on_bar` (`filled = pending-сторона ∧ знак
   позиции`, `on_bar:142–152`): после исполнения переворота позиция меняет знак —
   условие исполняется на баре филла и вешает reduce-only стоп новой ноги. Ноль нового
   кода брекета.
3. **m16 (carry-over из ревью T8):** `_bracket_orders` теперь `if position > 0: ... elif
   position < 0: ... else: return []` — без implicit else; вызову с `position == 0`
   соответствует явное «ничего» (из `on_bar` недостижим, но рассогласование pending с
   движком больше не прячется).
4. Docstrings (модуль, `on_bar`, `_entry_order`) обновлены под «задачи 7–9»; трейлинг (10)
   остался описанием будущего — ничего трейл-состояния не заведено (R2).

## Точный сценарий Step 1

`_signals_while_long(strategy)` — новый helper рядом с `_flat_state`/`_signals`
(плюс `_long_state`: `StrategyState(position=Decimal("100"), equity=10000)` с бара 0,
заявки стратегии нет → ни брекета, ни pending-истории). Лента `_bars_then_breakdown()`:
17 закрытых плато-баров + пробой вниз; переворачивающий сигнал — **закрытый 15m-бар №18**
(close 99.5 < ll 99.75; граница warmup для donch_len=2 — бар №15, R8 соблюдён).
Параметры пиннованного теста: donch_len=2, bias_len=0, atr_mult по умолчанию 1.5;
замороженный ATR = 0.5 → `new_qty = 10000·0.01/(1.5·0.5) = 133⅓`, ордер
**SELL MARKET qty = 233⅓… (= 100 + 133⅓)** — сумма не пиннится, только `> 100`.
Ровно один ордер за весь прогон.

Доп. тесты (sketches beyond pinned, для чек-листа само-ревью):
- `test_same_direction_signal_while_position_is_blocked` — вверх-пробой при лонге и вниз-пробой при шорте: `[]` с обеих сторон;
- `test_reversal_lifecycle_brackets_new_leg_on_fill_bar` — atr_mult=2 → ровно `[SELL MARKET 200]` на баре №18; на баре филла (позиция −100) существующий путь даёт `[BUY STOP_MARKET 100 @ 101]`; следующий бар — `[]` (pending израсходован, дублей нет);
- `test_second_reversal_signal_while_pending_is_blocked` — довес 15m-группы №19 с close 99 (живой встречный сигнал при ещё неисполненном pending) → второй ордер не появляется;
- `test_bias_filter_blocks_reversal_as_it_blocks_entry` — лента T8 (`110/110/108`, bias=107.75) при лонге: фильтр режет реверс (0 ордеров), с выключенным фильтром — ровно 1 SELL;
- `test_bracket_flat_call_emits_nothing` — m16: прямой вызов `_bracket_orders` с flat-состоянием → `[]`.

## Position.stop_price на флипе: проверено

`strategylab/core/position.py` — ветка переворота `apply_fill` (филл противоположной
стороны с `fill.qty > self.qty`), строки 73–82: **строка 80 `self.stop_price = None`**
(и 81–82 обнуляют take_profit-зеркало). Движок сам снимает стоп-уровень на перевёрнутой
ноге — утверждение брифа подтверждено; движок/брокер не тронуты.

## Step 2: честный статус — GREEN с самого начала

`test_reversal_bucket_split_and_sum` (verbatim, в `tests/test_engine_trades.py` на своих
`_run`/`_market`/`_bars(5)` — реальные бары, цены из ts) **прошёл ещё до изменения
стратегии** — ядро уже реализует флип (`position.py:73–82`), а T3 уже бакетирует по
стороне позиции. Это pinning-by-design движкового money-инварианта (как LIMIT-пин T5),
а не vacuous test. **Non-vacuity по коду** (без поломки ядра, как и велено):
`snap = broker.position.side` до `apply_bar` — `engine.py:57`; `_record_realized` получает
`before_side` (`engine.py:65`) и пишет `metrics.record(fill, realized, before_side)` —
`engine.py:132` с явным комментарием «Сторона закрываемой позиции, а не филла»; знак pnl —
из `before_side` (`engine.py:120`); сведение окна — `side_before_close` (`engine.py:165,170`).
`Metrics.record` маршрутизирует бакет по этому аргументу (`metrics.py:103–105`). Если бы
атрибуция шла по `fill.side`, филл переворота (SELL) и BUY-сведение оба попали бы в short:
первый же assert (`metrics.long.trades == 1`) упал бы как `1 == 0`, и снова на
`long.net_pnl + short.net_pnl == net_pnl` — тест краснеет по правильной причине.
Сведение окна после флипа реально закрывает шорт (позиция −1 после филла на баре 3 живёт
до бара 4). Ожидаемого брифом `assert 0 == 1` здесь нет — репортую фактическую правду.

Старый reduce-only брекет при флипе: в юнит-ленте его нет физически (позиция смонирована,
pending пуст), чистить нечего; в проде lifecycle — за брокером (`_cancel_reduced`,
sim.py:89–102, и фикс 8b303a7 «superseded mirrored stops leave the pending queue»).
Брокер не тронут. Step 2 ордера-стопы не использует.

## TDD Evidence

1. **RED:** `pytest tests/test_strategy_two_sided_momentum.py tests/test_engine_trades.py`
   → `1 failed, 18 passed`: `test_opposite_signal_reverses_with_a_single_order` —
   `assert 0 == 1 + where 0 = len([])` (пустой список, ровно как ждал бриф);
   `test_reversal_bucket_split_and_sum` PASSED (см. выше).
2. **Реализация** (п. 1–4) → тот же запуск: `19 passed`.
3. Доп. тесты написаны после GREEN пиннов (скичи — моя ответственность, пинны не правились) → `24 passed`.
4. **Full suite:** `388 passed, 3 skipped` (baseline 381+3 → +7 новых тестов).
5. `mypy strategylab` — `Success: no issues found in 34 source files`;
   `ruff check .` — `All checks passed!` (line-length 100, E/F/I/UP/B).
6. Коммит `3dce9d3`: `3 files changed, 216 insertions(+), 15 deletions(-)`.

## Self-review findings

- Реверс не стреляет при pending — гейт `self._pending_entry is not None` ранний и
  независимый от позиции (test). Однонаправленный lockout цел (test, обе стороны).
- Брекет новой ноги — переиспользование, копий логики нет; LIMIT не выдаётся (все прогоны
  проверяют только MARKET/STOP_MARKET).
- Единый order на реверс (Review Focus 2) — pin `len(orders) == 1` + lifecycle-тест с
  точным списком списков.
- Трейлинг не просочился: новых атрибутов нет, `_update_15m`/агрегация не тронуты;
  shared literals (qty 100, стоп 99/101, ATR 0.5) не изменены — только использованы.
- Decimal везде (`abs(position)`, `qty += abs(position)`); mypy strict чист.
- **Найдено и исправлено по ходу:** мои же засоры строк в комментах (английские/иероглиф
  вставки «applies and to fresh entries», «信号», «breket», «nonzero», «count ордеров») —
  вычищены, финальный regex-sweep по трём файлам + grep токенов чистые до коммита.

## Concerns

1. **388 ≠ ожидавшихся 383**: я добавил 6 тестов к стратегии вместо минимальных 2 —
   каждый закрывает пункт обязательного чек-листа само-ревью (pending-lockout, same-dir,
   lifecycle-брекет, bias-на-реверсе, m16). Поведения это не касается; если контроллер
   хочет ровно 383 — лишние тесты отсекаются одним коммитом без правок кода.
2. Step 2 зелёный сразу — честно запинено и аргументировано выше; если ревью сочтёт это
   vacuous, единственный честный способ «покраснеть» — сломать атрибуцию в ядре, что
   запрещено инструкцией.
3. Размер реверсной ноги считается от `state.equity` сигнального бара — то же правило, что
   у свежего входа (single-sourced сайзинг сохранён); после филла old-leg P&L уже в equity,
   как и должно.
