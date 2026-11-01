# Design-draft: трек фиксов ядра B/E/C-3 (черновик для owner-approve)

Статус: ЧЕРНОВИК, ничего не строим до approve (R-next-4). Ветка-условие R-next-3
соблюдено: серия не startet (round 3 — только замеры/данные, кода стратегий нет).
Источник дефектов: свод `strategydocs/калибровка_набор_итог_20261028.md`, реестры B/C/E.

## Дефекты (дословно из реестра)

- **B** — `sim.py::submit` (44–55) валидирует только структуру заявки: entry-notional
  115 289.92 при капитале 10 000 (×11.5) принят без вопросов; `_apply_liquidation`
  (208–229) при leverage=1 требует `bar.low ≤ 0` — недостижима.
- **E** — при marked equity < 0 движок продолжает исполнять НОВЫЕ ордера: spec_05,
  4/4 BTC-ячеек — после пробоя нуля ещё 2/6/3/7 слоёв лесенки; marked-дно −42×
  от капитала; `LiquidationError` — 0 срабатываний. Финальные equity −122k…−187k
  опубликованы как есть.
- **C-3** — конец окна: pending-ордера переживают окно в 13/24 ячейках (ETH fold1:
  pending_limit_buy=1); `cancel_all` в `_close_window` нет; поведение не определено
  спеками; TIF-арифметика опирается на «pending» как заглушку.

## Фикс 1 — ветка `fix/broker-capital-gate` (B+E: один семантический объект — защита счёта)

Семантика ОКОНЧАТЕЛЬНАЯ (после код-проверки ведущего 2026-10-29, вариантов нет):
1. **`MarginError(Exception)`** — рядом с `LiquidationError` (`broker/sim.py:12`),
   тот же стиль, русский докстринг. Форма отказа явная, не тихая (урок C-4).
2. **Гейт в `engine.run()`** в цикле подачи ордеров, перед `broker.submit(order)`:
   - **marked equity** (НЕ metrics.equity!): `marked = metrics.equity +
     signed_qty × (bar.close − position.entry_price)`, signed_qty через
     существующий `_signed_position(broker)`; flat → marked = metrics.equity.
     Основание: metrics.equity — ТОЛЬКО реализованный PnL (metrics.py:16–26);
     гейт на нём был бы беззубым для dca-лесенки (дефект E: счёт тонет в
     нереализованной просадке, а realised положителен до закрытий).
   - **Account-equity гейт**: marked ≤ 0 и ордер НЕ reduce_only → MarginError.
     Reduce-only проходят ВСЕГДА (счёт обязан иметь возможность закрыться).
   - **Margin/notional гейт** (только НЕ reduce_only): требуемая маржа =
     (|position.qty| × bar.close + order.qty × bar.close) / leverage; если
     требуемая маржа > marked → MarginError. Идиома margin = notional/leverage
     как в `_apply_liquidation` (sim.py:213). leverage = `broker.costs.leverage`
     (CostModel, costs.py:22, Decimal("1") по умолчанию).
3. **`StrategyState.equity` НЕ меняется** (остаётся realised-only): смена
   контракта, видимого стратегиям, — отдельное решение вне этого фикса
   (регистрируем как известное ограничение; минимальный blast radius).
4. **Ожидаемое изменение поведения**: spec_05 BTC-ячейки (дефект E срабатывал)
   после фикса падают `MarginError` вместо публикации −122k…−187k — это ЦЕЛЬ
   фикса, в baseline-diff фиксируется как intended change. Ячейки, где дефект
   не срабатывал (spec_02/03/04, ETH spec_05), — побайтово без изменений.

## Код-проверка (шпаргалка имплементатору, ведущий, 2026-10-29)

- `LiquidationError(Exception)` — `strategylab/broker/sim.py:12`.
- `leverage: Decimal = Decimal("1")` — `strategylab/broker/costs.py:22`
  (CostModel); доступ брокера: `self.costs.leverage` (sim.py:213).
- `Metrics.equity` — realised-only: `self.equity += pnl` в record()
  (metrics.py:42); докстринг metrics.py:16–18 это подтверждает.
- `_signed_position(broker)` уже есть в engine.py (знаковая qty: +long/−short/0).
- Цикл run(): engine.py:56–79 (apply_bar:61 → _record_realized:62 →
  StrategyState:71 → on_bar/submit:76–77 → _close_window на последнем баре:78–79).
- Гейт-формулы нуждаются в guard для flat-позиции (entry_price плоской позиции
  не участвовать в арифметике — signed_qty = 0 обнуляет слагаемое).

## Фикс 2 — ветка `fix/window-close-cancel-all` (C-3: гигиена конца окна)

1. `_close_window` (engine.py ~140): СНАЧАЛА cancel_all pending (очередь не
   переживает окно), ПОТОМ закрытие позиции по bar.close (текущая логика).
2. Артефакт: число снятых заявок → строка journal/report; инвариант учёта becomes
   `submits − fills − expired − window_cancelled == 0` (pending-заглушка уходит).
3. Baseline-diff: в 13/24 ячейках с пережившей очередью МЕТРИКИ csv не меняются
   (заявки и так не исполнялись после окна); меняется только учётная строка
   report/journal — задокументировать как intended accounting diff.

## Протокол верификации (обе ветки, паттерн TIF)

1. Unit-тесты фикса: notional ×11.5 → MarginError; equity ≤ 0 → не-reduce-only
   отклонён, reduce-only принят; cancel_all → pending после окна = 0; учёт сходится.
2. Мутационные тесты (гейт отключён → старое поведение воспроизводится).
3. Baseline-diff: контрольный прогон сеток spec_02/03/04 (+ETH spec_05) на
   до-фиксовом и после-фиксовом ядре: csv побайтово равны там, где дефекты не
   срабатывали; расхождения только в задокументированных intended-ячейках.
4. Брат-ревью (mimo, fresh-eyes: критерии из design ДО чтения машины) + полный
   гейт-набор 392/3 + ruff + mypy.
5. Merge target: `calibration-suite` (текущая рабочая ветка), каждый фикс —
   отдельный commit; main не трогаем.

## Порядок исполнения (дешёвые субагенты)

1. Субагент-имплементатор: ветка fix/broker-capital-gate (тесты → код → гейты).
2. Субагент-имплементатор: ветка fix/window-close-cancel-all (параллельно, независимо).
3. Baseline-diff субагент: контрольные прогоны, таблица расхождений.
4. Брат-ревью обеих веток; замечания → фиксы → merge.
