### Task 7: Лонг-вход и стоп-брекет

**Files:**
- Modify: `strategylab/strategy/two_sided_momentum.py`
- Test: `tests/test_strategy_two_sided_momentum.py`

**Interfaces:**
- Consumes: `Order`, `OrderKind`, `Side`, `StrategyState`
- Produces: сигнал LONG при `Close(B) > hh` (и, если фильтр включён, `Close(B) > bias`); `Order(Side.BUY, OrderKind.MARKET, qty, None, None, False)` с `qty = state.equity * _RISK_FRAC / (atr_mult * atr)`; на баре исполнения входа — `Order(Side.SELL, OrderKind.STOP_MARKET, position_qty, None, stop, True)` с `stop = bar.close - atr_mult * atr_frozen`; фиксированной цели нет

- [ ] **Step 1: Написать падающий тест на пробой вверх**

```python
def test_breakout_above_donchian_opens_long_market_order() -> None:
    s = TwoSidedMomentumStrategy(donch_len=Decimal("2"), atr_mult=Decimal("2"), bias_len=Decimal("0"))
    bars = _bars_then_breakout()
    orders = _run_strategy(s, bars)
    assert orders[-1] == [Order(Side.BUY, OrderKind.MARKET, Decimal("100"), None, None, False)]
```

`Decimal("100")` — это `10000 · 0.01 / (2 · 0.5)`: капитал 10 000, риск 1 % = 100, дистанция до стопа `atr_mult · ATR` = 2 · 0.5 = 1.0, значит объём 100. Числа подобраны так, чтобы деление было точным в `Decimal` (3 и 7 в знаменатель не ставить: периодическая дробь заставит тест сравнивать строки после округления). Эти же 100 переиспользуются в задачах 8, 9 и 10 как объём позиции — если менять ATR или `atr_mult`, менять надо все четыре задачи разом, иначе `reduce_only`-брекет перестанет равняться объёму позиции и брокер бросит «заявку больше позиции».

- [ ] **Step 2: Написать падающий тест на брекет на баре после входа**

```python
def test_stop_bracket_is_placed_on_the_fill_bar() -> None:
    # вход исполнен на баре с close=100, ATR frozen=0.5, atr_mult=2 → stop=99
    assert orders == [Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("100"), None, Decimal("99"), True)]
```

Одновременно — тест, что **никакого LIMIT-ордера стратегия не выдаёт вовсе** (цели в спеке нет): `assert all(o.kind is not OrderKind.LIMIT for ...)`.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py -v`
Expected: FAIL — пустые списки вместо ордеров

- [ ] **Step 4: Реализовать**

`self._pending_entry: Side | None` и `self._pending_atr: Decimal | None` — состояние «сигнал дан, ждём исполнения». Вход разрешён при `state.position == 0 and self._pending_entry is None` (спека: «с ограничением не в позиции»). Брекет ставится, когда `self._pending_entry is Side.BUY and state.position > 0`, по образцу `FlatKeltnerStrategy._bracket_orders`, с оценкой `entry = bar.close` (решение 4Flat-Кельтнера: движок не отдаёт цену исполнения, ошибка оценки — одно проскальзывание).

- [ ] **Step 5: Запустить тесты**

Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add strategylab/strategy/two_sided_momentum.py tests/test_strategy_two_sided_momentum.py
git commit -m "feat: long breakout entry with atr stop bracket"
```

---

