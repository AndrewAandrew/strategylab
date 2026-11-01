### Task 8: Шорт-вход и зеркальный стоп

**Files:**
- Modify: `strategylab/strategy/two_sided_momentum.py`
- Test: `tests/test_strategy_two_sided_momentum.py`

**Interfaces:**
- Produces: сигнал SHORT при `Close(B) < ll` (и `Close(B) < bias` при включённом фильтре); `Order(Side.SELL, OrderKind.MARKET, qty, None, None, False)`; брекет `Order(Side.BUY, OrderKind.STOP_MARKET, position_qty, None, entry + atr_mult * atr, True)`

- [ ] **Step 1: Написать падающий тест на пробой вниз**

```python
def test_breakdown_below_donchian_opens_short() -> None:
    ...
    assert orders[-1] == [Order(Side.SELL, OrderKind.MARKET, Decimal("100"), None, None, False)]
```

- [ ] **Step 2: Написать падающий тест на зеркальный стоп**

```python
def test_short_bracket_stop_is_above_entry() -> None:
    # close=100, atr=0.5, atr_mult=2 → stop = 101, заявка BUY side
    assert orders == [Order(Side.BUY, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("101"), True)]
```

- [ ] **Step 3: Написать тест, что фильтр уклона режет противоположные входы**

```python
def test_bias_filter_blocks_short_above_ema() -> None:
    s = TwoSidedMomentumStrategy(donch_len=Decimal("2"), bias_len=Decimal("2"))
    assert _signals(s, _downward_breakout(closes_above_bias)) == []
```

 LONG требует `close > bias`, SHORT требует `close < bias`; при нулевом `bias_len` оба условия сняты (уже проверено в задаче 6).

- [ ] **Step 4: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py -v`
Expected: FAIL — пустые списки вместо ордеров на пробое вниз

- [ ] **Step 5: Реализовать шорт и зеркальный стоп**

Сторона сигнала читается из сравнения `close` с `ll`; остальное (сайзинг, `_pending_entry`, брекет на баре исполнения) зеркально лонгу: `stop = bar.close + atr_mult * atr_frozen`, заявка `Side.BUY`.

- [ ] **Step 6: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py -v`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add strategylab/strategy/two_sided_momentum.py tests/test_strategy_two_sided_momentum.py
git commit -m "feat: short side and mirrored atr stop"
```

---

