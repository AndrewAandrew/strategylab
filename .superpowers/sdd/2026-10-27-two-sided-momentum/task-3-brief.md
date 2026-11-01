### Task 3: Движок передаёт сторону закрываемой позиции

**Files:**
- Modify: `strategylab/core/engine.py:120-138` (`_record_realized`), `strategylab/core/engine.py:154-169` (`_close_window`)
- Test: `tests/test_engine_trades.py`

**Interfaces:**
- Consumes: `Metrics.record(fill, pnl, side)` (задача 2), `Side`
- Produces: тот же `Engine.run`; у возвращаемых `Metrics` заполнены `long` и `short`

- [ ] **Step 1: Написать падающий тест на шорт через реальный прогон**

```python
def test_short_trade_lands_in_short_bucket() -> None:
    metrics = _run({0: [_market(Side.SELL, "1")], 2: [_market(Side.BUY, "1")]}, None)
    assert metrics.short.trades == 1
    assert metrics.long.trades == 0
    assert metrics.short.net_pnl == metrics.net_pnl
    assert metrics.long.net_pnl + metrics.short.net_pnl == metrics.net_pnl
```

Помощники этого файла уже делают всё нужное: `_run(script, trade_log)` гоняет `ScriptedStrategy` по `ListFeed(_bars(5))` с `PESSIMISTIC`-вилкой, `_market(side, qty)` строит маркет-ордер. Своего `_feed` здесь нет и изобретать его не надо.

- [ ] **Step 2: Написать падающий тест на сведение окна (Review Focus 5)**

```python
def test_window_close_is_bucketed_by_position_side() -> None:
    # Стратегия открывает шорт и больше ничего: движок сводит окно на последнем баре.
    metrics = _run({0: [_market(Side.SELL, "1")]}, None)
    assert metrics.short.trades == 1 and metrics.long.trades == 0
    assert metrics.short.net_pnl == metrics.net_pnl
```

Смысл теста именно в том, что `_close_window` записывает сделку в bucket **стороны позиции**: закрывающий филл короткой позиции — `Side.BUY`, и если взять сторону филла, последний шорт отчёта станет долгом.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine_trades.py -v`
Expected: FAIL — `assert 0 == 1` на `metrics.short.trades`

- [ ] **Step 4: Реализовать**

`_record_realized` уже получает `before_side: Side` — передать его в `metrics.record(fill, realized, before_side)`. В `_close_window` взять сторону позиции **до** `position.apply_fill(fill)` и передать в `metrics.record(fill, realized, side_before_close)`. Никаких других изменений в движке нет: порядок вызовов и pnl-формулы не тронуты.

- [ ] **Step 5: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine_trades.py tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 6: Проверить инвариант суммы**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, включая существующий `test_engine_trades.py::...` про совпадение суммы pnl журнала и `net_pnl`: `long.net_pnl + short.net_pnl == net_pnl` обязан выполняться во всех прогонах — добавить это утверждение в тест шага 1.

- [ ] **Step 7: Коммит**

```bash
git add strategylab/core/engine.py tests/test_engine_trades.py
git commit -m "feat: engine attributes realized pnl to position side"
```

---

