# Task 7 report: Лонг-вход и стоп-брекет

Commit: `5086523 feat: long breakout entry with atr stop bracket` (branch `spec01-two-sided-momentum`, не запушен).

## Что реализовано

`strategylab/strategy/two_sided_momentum.py`:

- **Сигнал/вход (MARKET).** На закрытии 15m-бара B, когда `_hh`/`_atr_value`
  готовы (после границы warmup), `state.position == 0` и `self._pending_entry is None`:
  если `B.close > hh` — эмитится `Order(Side.BUY, OrderKind.MARKET, qty, None, None, False)`
  с `qty = state.equity * _RISK_FRAC / (atr_mult * atr)`, atr — `_atr_value` на баре
  сигнала, заморожен в `self._pending_atr`, `self._pending_entry = Side.BUY`.
  Фильтр уклона добавляет `B.close > bias` **только когда включён**: проверка читает
  `self._bias is not None` (эквивалент `self.bias_len == 0` — выключен), а не `_bias_value`.
  Добавлен паритетный flat-keltner guard `atr <= 0` (защита от деления на нулевой ATR
  на полностью плоской ленте) и `state.equity <= 0` — «входить нечем».
- **Стоп-брекет на баре исполнения.** Пока `_pending_entry is Side.BUY and state.position > 0`:
  `Order(Side.SELL, OrderKind.STOP_MARKET, state.position, None, stop, True)`,
  `stop = bar.close - atr_mult * self._pending_atr` (оценка `entry = bar.close`, решение 4
  Flat-Кельтнера — ошибка в одно проскальзывание). Оба pending-поля снимаются на баре
  брекета. LIMIT не эмитится никогда — цели в спеке нет.
- **Атрибуты** `self._pending_entry: Side | None`, `self._pending_atr: Decimal | None` —
  мандатные имена (задачи 8–10 расширяют), объявлены в `__init__` с комментарием.
- **R9-fold (docstring).** Class docstring + docstring `_entry_order`: при
  `bias_len == 0` `_bias_value` остаётся `None` **навсегда по замыслу** (фильтр выключен,
  не «не готово»); выключенность проверяется по `self.bias_len == 0` / `self._bias is None`,
  и только при включённом фильтре `None` в `_bias_value` означает «ещё не посчитан».
  Готовность сигнала решается по `_hh`/`_atr_value`, `_bias_value is None` никогда не
  гейтит вход при выключенном фильтре.
- Модульный docstring: абзац «Каркас ... пока `on_bar` не выдаёт ордеров» заменён на
  описание реализованного лонг-входа/брекета; шорт (8), реверс (9), трейлинг (10) — дальше.

`tests/test_strategy_two_sided_momentum.py`:

- Новый хелпер `_bars_then_breakout()` (единственный билдер сценария, без моков —
  реальный Aggregator/ATR): 17 плато 15m-баров + бар пробоя = закрытый 15m-бар **№18**
  (граница warmup для donch_len=2 — бар №15, пробой значительно после).
  - Плато-бар: high=100.25, low=99.75 (коридор ±FLAT_HALF), close = 100 + 0.01·index —
    выше предыдущего close, но строго ниже hh=100.25 (двухбарный Дончиан не пробивается,
    ready-бары №15–17 с close 100.14–100.16). Истинный диапазон каждого ровно
    `Decimal("0.5")` (термы |high−prev_close| ≤ 0.25 и |low−prev_close| ≤ 0.4 не
    выигрывают) → сид Rma = 7.0/14 = 0.5, дальше (0.5·13+0.5)/14 = **ATR ровно 0.5**
    на баре сигнала.
  - Бар пробоя: 14 1m-баров плоские 100/100/100/100 (low ≥ 100 — иначе 15m low уехал бы
    к 99.75 и TR сломался), финальный 1m: open=low=100, high=close=100.5 → 15m:
    high=close=100.5, low=100, TR = max(0.5, 0.34, 0.16) = 0.5, close 100.5 > hh 100.25.
  - 1m-тайминги идут из `len(bars)`, окна 15m бьются ровно по 15 подряд.
- Тест 1 (pinned verbatim по значениям): qty 100 = 10000·0.01/(2·0.5), деление точное
  (`100.00/1.0 == Decimal("100")`). Строка конструктора из брифа 102 символа — перенесена
  в 3-line call (значения/порядок дословно), т.к. ruff E (line-length 100) — binding gate.
- Тест 2: тот же прогон плоским состоянием до сигнала, затем следующий 1m-бар
  (`_bars(1, first=270)` — старт нового 15m-окна, агрегат не закрывается, на сигнал
  не влияет) с `position=100, equity=10000` → bracket `STOP_MARKET 100 @ stop=99`
  (99 = 100 − 2·0.5). Плюс: pending израсходован (следующий бар с той же позицией → `[]`),
  плюс глобальная проверка `all(o.kind is not OrderKind.LIMIT ...)` по всем ордерам прогона.
- Хелперы не ломались: сигнатуры `_bars`/`_rising_1m_bars`/`_flat_state`/`_orders`/
  `_run_strategy` не менялись (у `_flat_state` уже был `equity=Decimal("10000")` —
  расширять keyword-дефолты не понадобилось); импорт расширен `OrderKind, Side`.
  Тестовый docstring обновлён (каркасная фраза «ордеров не выдаёт вовсе» заменена).

## TDD evidence

- **RED** (`.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py -v`
  до реализации): `2 failed, 4 passed` — ровно ожидаемое `AssertionError: assert [] == [Order(...)]`
  на `orders[-1]` в обоих новых тестах (пустые списки вместо ордеров).
- **GREEN** (фокусированные): `6 passed in 0.11s` (шаг 5, после реализации; перезапуск
  подтверждён и после финальной правки guard).
- **Full suite**: `378 passed, 3 skipped` (baseline 376+3, +2 новых) — `.venv\Scripts\python.exe -m pytest -q`.
- **mypy**: `.venv\Scripts\python.exe -m mypy strategylab` → `Success: no issues found in 34 source files`.
- **ruff**: `.venv\Scripts\python.exe -m ruff check .` → `All checks passed!`.

## Files changed (commit 5086523)

- `strategylab/strategy/two_sided_momentum.py`
- `tests/test_strategy_two_sided_momentum.py`

## Self-review findings

1. Lifecycle pending: set только в `_entry_order` одновременно с BUY; consumed в `on_bar`
   на первом баре с `position > 0`, оба поля снимаются до эмита брекета; unfilled-signal
   случай (position остаётся 0) держит pending — дублей входа нет (гейт
   `self._pending_entry is not None`).
2. Гейт `_bias_value is None` отсутствует в readiness-проверке; форма `self._bias is not None`
   — по R9. Существующий warmup-тест (donch_len=3, rising bars) теперь тоже порождает
   сигналы на ready-барах, но проверяет только состояние — зелёный, проверено прогонами.
3. Шорт/реверс/трейлинг не провезены: `_ll` не читается, `Side.SELL` — только reduce_only
   стоп; `entry_atr` передаётся в `_bracket_orders` параметром — трейлинг (10) расширит сам.
4. Тесты детерминировны через реальные Aggregator/Atr, без моков; все арифметические
   literals точные в Decimal (сид 0.5, qty 100, stop 99).
5. Поправлено по ходу ревью: паритетный guard `atr <= 0` (был в flat_keltner, у входа
   деление — иначе crash на нулевом ATR); потерянный пробел в комментарии теста.

## Concerns

- Строка конструктора из Step 1 завернута (102 > 100 символов) — отклонение от «verbatim»
  на уровне форматирования, значения ровно pinned; при ревью ожидаем так же в Task 8–10.
- Двойной «flat до сигнала» assert в тесте 2 дублирует тест 1 (осознанно: тест 2 обязан
  знать, что перед fill-bar вход точно выдан).
- `strategylab.egg-info/` — untracked мусор окружения, не коммитировался.
