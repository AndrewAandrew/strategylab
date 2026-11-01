### Task 5: Брокер вытесняет устаревший зеркальный стоп

Без этой задачи трейлинг неработающ производительнее: каждая повторная `reduce_only` STOP_MARKET заявка остаётся в `_pending` до закрытия позиции (`_apply_stops` их пропускает, `_cancel_reduced` снимает только совпадающие по стороне с позицией), а `apply_bar` проходит по `_pending` пять раз за бар. На 518 400 барах и сделке с сотнями обновлений стопа это квадратичная деградация прогона.

**Files:**
- Modify: `strategylab/broker/sim.py:48-60` (`_mirror_reduced`)
- Test: `tests/test_broker_sim.py`

**Interfaces:**
- Consumes: `Order`, `OrderKind`, `Side`
- Produces: тот же `submit`; после второй постановки `reduce_only` STOP_MARKET той же стороны в `pending_orders` остаётся ровно одна такая заявка

- [ ] **Step 1: Написать падающий тест**

```python
def test_second_stop_submit_replaces_the_resting_mirror() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False))
    b.apply_bar(_bar((1, "100", "101", "99", "100")))
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("95"), True))
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("98"), True))
    stops = [o for o in b.pending_orders if o.kind is OrderKind.STOP_MARKET]
    assert len(stops) == 1 and stops[0].stop_price == Decimal("98")
    assert b.position.stop_price == Decimal("98")
```

- [ ] **Step 2: Написать тест, что частичная цель не вытесняется (не путать с вытеснением стопа)**

```python
def test_limit_mirror_is_not_deduplicated() -> None:
    # Две reduce_only LIMIT разных объёмов — разные цели: движок обязан оставить обе.
    ...
    assert len([o for o in b.pending_orders if o.kind is OrderKind.LIMIT]) == 2
```

Ветка `sweep_liquidity` live-периода полагается на то, что resting-цели доживают до своего век; дедупликация вводится только для STOP_MARKET и только по стороне.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_broker_sim.py -v`
Expected: FAIL — `assert 2 == 1`

- [ ] **Step 4: Реализовать**

В `_mirror_reduced` по ветке STOP_MARKET отфильтровать `self._pending`, убрав заявки с `reduce_only and kind is OrderKind.STOP_MARKET and side == order.side`. Вызов идёт до `append`, поэтому новая заявка остаётся. Смысл в шапке метода: зеркало уже перезаписано в позицию, устаревшая resting-заявка не может исполниться (`_apply_stops` пропускает `reduce_only`) и только оплачивает обход.

- [ ] **Step 5: Запустить тесты и весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — 363 + новые; в частности `test_broker_sim.py` и `test_strategy_sweep_liquidity.py` (частичные тейки) остаются зелёными.

- [ ] **Step 6: Коммит**

```bash
git add strategylab/broker/sim.py tests/test_broker_sim.py
git commit -m "fix: superseded mirrored stops leave the pending queue"
```

---

