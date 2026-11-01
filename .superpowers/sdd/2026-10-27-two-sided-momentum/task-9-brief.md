### Task 9: Переворот встречным сигналом

**Files:**
- Modify: `strategylab/strategy/two_sided_momentum.py`
- Test: `tests/test_strategy_two_sided_momentum.py`
- Test: `tests/test_engine_trades.py` (проверка money-инварианта переворота)

**Interfaces:**
- Produces: встречный сигнал при открытой позиции → **один** MARKET-ордер противоположной стороны с `qty = abs(state.position) + new_qty`; после исполнения — брекет новой ноги

- [ ] **Step 1: Написать падающий тест на один переворачивающий ордер**

```python
def test_opposite_signal_reverses_with_a_single_order() -> None:
    s = TwoSidedMomentumStrategy(donch_len=Decimal("2"), bias_len=Decimal("0"))
    orders = _signals_while_long(s)
    assert len(orders) == 1
    assert orders[0].side is Side.SELL and orders[0].qty > Decimal("100")
```

`len(orders) == 1` — Review Focus 2: два ордера вместо одного изменили бы distribution входной комиссии и разошлись бы с `net_pnl`.

- [ ] **Step 2: Написать падающий тест на сквозной переворот через `Engine`**

```python
def test_reversal_bucket_split_and_sum() -> None:
    # бар 0: лонг 1; бар 2: SELL 2 = закрыть лонг и открыть шорт; дальше шорт живёт
    # до конца ленты, и окно сводит движок.
    metrics = _run({0: [_market(Side.BUY, "1")], 2: [_market(Side.SELL, "2")]}, TradeLog())
    assert metrics.long.trades == 1  # закрытая часть переворота
    assert metrics.short.trades == 1  # шорт, закрытый сведением окна
    assert metrics.trades == 2
    assert metrics.long.net_pnl + metrics.short.net_pnl == metrics.net_pnl
```

Бары обязаны быть настоящими (лента `_bars(5)` этого файла, цены идут из `ts`), а не смонированными метриками: тест, подставляющий готовые `Metrics`, зелёный при сломанном перевороте — запрет на это унаследован из плана 1, задача 13.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py tests/test_engine_trades.py -v`
Expected: FAIL — пустой список вместо переворачивающего ордера и `assert 0 == 1` на `metrics.short.trades`

- [ ] **Step 4: Реализовать переворот**

При встречном сигнале и `state.position != 0` сайзинг новой ноги идёт по тому же риску (`new_qty = equity * _RISK_FRAC / (atr_mult * atr)`), а ордер несёт `abs(position) + new_qty`. `_pending_entry` переводится на сторону сигнала; трейлинг-состояние (экстремумы и уровень стопа) сбрасывается — `Position.apply_fill` в ветке переворота обнуляет `stop_price` сам.

- [ ] **Step 5: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py tests/test_engine_trades.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add strategylab/strategy/two_sided_momentum.py tests/test_strategy_two_sided_momentum.py tests/test_engine_trades.py
git commit -m "feat: reversing breakout on opposite signal"
```

---

