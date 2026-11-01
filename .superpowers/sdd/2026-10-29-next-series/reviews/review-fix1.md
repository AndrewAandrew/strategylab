VERDICT: PASS

Ревью Fix 1 (B/E: капитал-гейт), ветка fix/broker-capital-gate @ 6efc4c9,
worktree: C:/Users/egoro/AppData/Local/Temp/opencode/wt_fix1.
Критерии сняты ДО чтения кода: kernel-fixes-design-draft.md (Фикс 1, пункты 1–4)
+ реестр дефектов strategydocs/калибровка_набор_итог_20261028.md:30–64 (B, E).
Diff: 1 commit, 9 файлов (+355/−34): источник — только sim.py (+4) и engine.py (+56),
остальное — тесты. Baseline-diff НЕ перезапускался (вне зоны по инструкции).

## Проверка семантики против дизайна (шаг 4, лично в коде)

1. **marked equity** — ВЫПОЛНЕНО. `engine.py::_guard_submit`:
   `marked = metrics.equity; if signed_qty != 0: marked += signed_qty ×
   (bar.close − position.entry_price)`; signed_qty через существующий
   `_signed_position` (знак +long/−short/0). Flat-guard явный: при signed_qty = 0
   слагаемое не вычисляется — entry_price (0/Infinity) в арифметику не попадает
   (подтверждено тестом `test_flat_position_entry_price_garbage_does_not_affect_the_gate`).
   `metrics.equity` — только реализованный + стартовый капитал (metrics.py:42
   `self.equity += pnl`), гейт НЕ на «сыром equity стратегии» — как в дизайне.
   Порядок в цикле run(): `apply_bar` → `_record_realized` → on_bar → guard → submit,
   т.е. marked учитывает реализацию текущего бара.
2. **reduce-only всегда проходит** — ВЫПОЛНЕНО. Первый же оператор `_guard_submit`:
   `if order.reduce_only: return` — до каких-либо проверок. Тест
   `test_reduce_only_passes_and_closes_position_when_marked_equity_is_not_positive`
   закрывает тонущий счёт выходом и проверяет `metrics.equity < 0`.
3. **MarginError — отдельный Exception, не перехватывается** — ВЫПОЛНЕНО.
   `class MarginError(Exception)` в sim.py сразу после LiquidationError (дословно
   по дизайну, русский докстринг). НЕ наследует ValueError → харнесс
   `scripts/calib/harness.py:378  except (ValueError, LiquidationError)` его не
   ловит (проверено). Сквозные `except` в verify_spec02/03/04/05.py
   `(ArithmeticError, LookupError, TypeError, ValueError, csv.Error, OSError,
   sqlite3.Error)` — тоже не ловят. Широких `except Exception` в прод-путях нет
   (единственный — тестовый helper `_number_or_text`).
4. **Гейт перед broker.submit** — ВЫПОЛНЕНО. `engine.py:84–85`: `_guard_submit(...)`
   непосредственно перед `broker.submit(order)`; единственный прод-путь подачи в
   репо (grep `.submit(`: только engine.py:85 + юнит-тесты брокера).
   `_close_window` и ликвидация подачу не используют — закрытие гейт не проходит,
   «счёт обязан иметь возможность закрыться» соблюдено.
5. **StrategyState.equity не изменён** — ВЫПОЛНЕНО. `strategy/base.py` в diff не
   входит; конструирование состояния осталось `equity=metrics.equity`
   (engine.py:80). Реализовано-контракт для стратегий не тронут (minimal blast
   radius, п.3 дизайна).
6. **Адаптации старых тестов не маскируют дефекты** — ВЫПОЛНЕНО (diff тестов
   прочитан выборочно, все 7 файлов):
   - адаптации — только подъём капитала (test_calib_harness, test_cli, test_engine,
     test_engine_trades, test_fork, test_strategy_limit_pullback): net_pnl /
     max_drawdown не зависят от капитала, утверждения не ослаблены;
     в test_cli equity 9872.916986540 → 99872.916986540 — арифметически ровно
     100000−127.083013460 (капитал 10000→100000), сам net_pnl не менялся;
   - `test_metric_row_negative_equity` переписан честным путём (капитал 100,
     заявка ровно = маржа → равенство проходит, филл ×1.5, крах цены, закрытие
     окна даёт realized ≈ −149.17): исходные assertions (net_pnl со знаком минус
     без abs/клиппинга, equity = капитал + net, drawdown > 0) СОХРАНЕНЫ —
     назначение теста (честная отчётность отрицательного equity) не потеряно;
   - дефектные утверждения («oversized принимается») в старых тестах не было;
     покрытие B/E вынесено в новый `tests/test_engine_margin_gate.py` (6 тестов:
     ×11.5 → MarginError; marked ≤ 0 → отказ; reduce-only проходит и закрывает;
     регрессия в пределах маржи; граница равенства; flat-guard Infinity).

## Ответы на вопросы ведущего

(а) **Накопление notional нескольких заявок одного бара.** В дизайне — ДА, так:
формула «( |position.qty| × close + order.qty × close ) / leverage » по-ордерная
и берёт текущую позицию; позиция меняется только на следующем баре (заявки лишь
кладутся в `_pending`, `submit` позицию не трогает), поэтому серия заявок одного
бара и уже лежащая в очереди заявка в required margin НЕ входят. Реализация
буквально следует формуле дизайна. Нюанс: сам дизайн накопление явно не
обсуждает — см. nit №3.

(б) **Notional по close подающего бара для всех типов ордеров в докстринге?** ДА.
`_guard_submit` docstring: «Notional считается по close ПОДАЮЩЕГО бара для всех
видов ордеров единообразно: для LIMIT это сознательная консервативная
аппроксимация — лимитная цена дала бы меньший notional, но заявка обязана быть
обеспечена при любой цене исполнения». Плюс объяснён flat-guard и «равенство
ПРОХОДИТ — отклоняем только строгое превышение».

## Находки

1. **minor — дрейф документации после фикса.** `strategylab/strategy/dca_grid.py:11`
   утверждает «гейта уровня счёта в ядре нет — finding №0 из spec_02» — после фикса
   это неверно (гейт в engine есть). Аналогично `scripts/calib/verify_spec05.py:15`
   («self-гейта equity<=0 нет намеренно») и `:354` («модель торгует на минусе») —
   верны теперь только для слоя стратегии. Документ плана
   `docs/superpowers/plans/2026-10-28-lab-calibration-suite.md:70` обещает:
   «падение ячейки (ValueError, LiquidationError) ловится per-cell … прогон сетки
   продолжается» — MarginError в этот контракт не попал (см. №2). Код не затронут;
   предлагаю owner'у решить, что правим при merge.
2. **minor — операционное следствие не-перехвата (осознанное, но требует
   решения).** Критерий дизайна «MarginError не перехватывается существующими
   except» ВЫПОЛНЕН, но значит: первая же tripped-ячейка в `harness.main`/sweep
   обрушивает ВЕСЬ прогон сетки traceback'ом, а не помечается «ОТКЛОНЕНО» и не
   продолжает прогон, как для ValueError/LiquidationError (план-док line 70).
   Для будущих сеток с дефолтным капиталом 10 000 и oversized-стратегиями это
   изменение контракта. Это соответствует п.4 дизайна («падают MarginError»), но
   либо tuple харнесса надо расширить отдельным решением owner'а, либо
   задокументировать новый контракт. Не блокер: критерий ревью — satisfied.
3. **nit — не-накопление не отражено в докстринге.** Про close-для-всех-типов
   сказано (вопрос б ✓), но то, что серия заявок одного бара и pending-очередь
   в required margin не входят, зафиксировано только в комментарии теста
   `test_calib_harness.py:398` («очередь заявок её не копит») — стоит добавить
   строку в `_guard_submit` docstring.
4. **nit — over-count для уменьшающих заявок.** Не-reduce-only SELL против длинной
   позиции получит required = (|позиция| + qty) × close — строже, чем нужно
   (заявка позицию уменьшает, а не увеличивает). Дословно соответствует формуле
   дизайна, поэтому не дефект исполнения — но ограничение стоит иметь в виду.
5. **nit — `run_fork(..., capital=Decimal("0"))` по умолчанию.** С гейтом любой
   входящий ордер при дефолте теперь падает MarginError (раньше торговал с нулём).
   Все прод-вызовы (cli.py:121, sweep.py:303, export_flat_keltner.py:576,
   harness) capital передают; тесты обновлены; в дизайн не входил — не правил.
   Формально это корректное поведение (торговать с капиталом 0 нельзя), отмечаю
   как ножку для будущих вызовов API.

Блокеров и major-находок нет: семантика гейта совпадает с design-draft Фикс 1
п.1–4 побайтово по формулам, reduce-only/flat-guard/место вызова/типы исключений
проверены лично.

## Прогнанные гейты (exit-коды)

| Гейт | Команда | Exit | Результат |
|---|---|---|---|
| pytest | `PYTHONPATH=wt_fix1 <venv>/python -m pytest wt_fix1/tests -q` | 0 | **398 passed, 3 skipped** (база 392+6 новых) ✓ ожидаемой базе |
| ruff | `<venv>/ruff.exe check wt_fix1` | 0 | All checks passed! |
| mypy, изменённые файлы (9 шт., strict из pyproject worktree, cwd=wt_fix1) | `<venv>/python -m mypy strategylab/{broker/sim,core/engine}.py tests/{test_engine_margin_gate,test_calib_harness,test_cli,test_engine,test_engine_trades,test_fork,test_strategy_limit_pullback}.py` | 1 | 9 errors in 3 files — **те же 9 ошибок на origin/calibration-suite** (контрольный прогон базы на извлечённом archive: exit 1, идентичный набор, сдвинутые только номера строк) → новых 0, все на не-trоганных строках |
| mypy, исходники пакета | `<venv>/python -m mypy strategylab` (cwd=wt_fix1) | 0 | Success: no issues found in 35 source files |
| baseline-diff | — | — | не перезапускался (инструкция: уже зелёный 122/122, 0 регрессий) |
| мутационный тест (протокол п.2) | — | — | в репо не сохранён, извне не верифицируем — вне запрошенной гейт-набора |
