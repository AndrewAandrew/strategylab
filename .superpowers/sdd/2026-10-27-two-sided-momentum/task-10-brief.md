### Task 10: Трейлинг стоп после входа

**Files:**
- Modify: `strategylab/strategy/two_sided_momentum.py`
- Test: `tests/test_strategy_two_sided_momentum.py`, `tests/test_broker_sim.py` (направление гэпа)

**Interfaces:**
- Produces: на каждом закрытом 15m-баре при открытой позиции `candidate = (max_high_since_entry - atr_mult * atr_now)` для лонга (для шорта — `min_low_since_entry + atr_mult * atr_now`); если `candidate` лучше текущего уровня, стратегия подаёт `reduce_only` STOP_MARKET на весь объём позиции; иначе — ничего

- [ ] **Step 1: Написать падающий тест на трейлинг лонга**

```python
def test_long_stop_trails_up_only() -> None:
    # вход 100, stop 99; следующий закрытый 15m бар: high 105, atr 0.5 → candidate 104 > 99
    assert orders == [Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("104"), True)]
    # бар ниже: candidate не лучше — повторной заявки нет
    assert _next_bar_orders(...) == []
```

«Только вверх» — буквальное требование спеки; тест на отсутствие заявки при ухудшении кандидата обязан быть, иначе стратегия будет перезаписывать стоп вниз (это же и ломает `_apply_exits`).

- [ ] **Step 2: Написать падающий тест на трейлинг шорта**

```python
def test_short_stop_trails_down_only() -> None:
    # вход 100, stop 101; бар low 95, atr 0.5 → candidate 96 < 101 → заявка BUY STOP на 96
    ...
```

- [ ] **Step 3: Написать тест на гэп над трейлинг-стопом шорта (Review Focus 1)**

```python
def test_short_trailing_stop_fills_on_gap_up() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    # шорт, зеркальный стоп 96; бар open 99, high 101 → стоп выбит по open
    fills = b.apply_bar(_bar((1, "99", "101", "94", "100")))
    assert fills[0].reason == "stop" and fills[0].price > Decimal("96")
```

- [ ] **Step 4: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py tests/test_broker_sim.py -v`
Expected: FAIL — пустой список вместо обновлённого стопа

- [ ] **Step 5: Реализовать трейлинг**

Экстремумы позиции (`_since_high`, `_since_low`) заводятся на баре исполнения входа и обновляются на каждом закрытом 15m-баре; ATR для кандидата берётся **текущий**, а замороженный ATR остаётся только для ширины стопа на входе (спека: «ATR Wilder заморожен на баре-сигнале» про вход, трейлинг использует живое значение).

- [ ] **Step 6: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py tests/test_broker_sim.py -v`
Expected: PASS

- [ ] **Step 7: Проверить, что брокер не копит заявки**

Run: `.venv\Scripts\python.exe -m pytest tests/test_broker_sim.py -v`
Expected: PASS — тест задачи 5 и новый тест «после десяти обновлений стопа в `pending_orders` одна STOP_MARKET заявка».

- [ ] **Step 8: Коммит**

```bash
git add strategylab/strategy/two_sided_momentum.py tests
git commit -m "feat: trailing atr stop on both sides"
```

---

