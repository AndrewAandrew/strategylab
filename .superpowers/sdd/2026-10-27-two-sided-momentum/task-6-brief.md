### Task 6: Стратегия — каркас, параметры, Дончиан и ATR

**Files:**
- Create: `strategylab/strategy/two_sided_momentum.py`
- Test: `tests/test_strategy_two_sided_momentum.py`

**Interfaces:**
- Consumes: `Bar`, `StrategyState`, `Aggregator`, `Atr`, `Rma`, `FIFTEEN_MIN_WIDTH`, `FIFTEEN_MIN_COUNT`, `positive`, `positive_int` (`strategylab.strategy.indicators`)
- Produces: класс `TwoSidedMomentumStrategy` с `name = "two_sided_momentum"`, `PARAM_NAMES = ("donch_len", "atr_mult", "bias_len")`, конструктором `(donch_len: Decimal = Decimal("48"), atr_mult: Decimal = Decimal("1.5"), bias_len: Decimal = Decimal("48"))`, `from_params(cls, params: Mapping[str, Decimal]) -> TwoSidedMomentumStrategy`, `on_bar(self, bar: Bar, state: StrategyState) -> list[Order]`, публичным свойством `bias_len -> int` (0 — фильтр выключен); константы модуля `_ATR_LEN = 14` и `_RISK_FRAC = Decimal("0.01")`

Помощники теста `_bars(count)`, `_rising_1m_bars(count)`, `_flat_state()`, `_orders(strategy, bar, state)` и `_run_strategy(strategy, bars) -> list[list[Order]]` объявляются в этом тестовом файле в задаче 6 и переиспользуются в задачах 7–10: своя стратегия с тестами уже не обязана изобретать свой способ накормить её барами. `_rising_1m_bars` строит монотонно растущие 1m-бары (каждый следующий close выше максимума предыдущих), чтобы пробой вверх был фактом ленты, а не настройкой.

Правила — дословно из спеки §Правила: `HH = max High` и `LL = min Low` последних `donch_len` **завершённых** 15m-баров; LONG при `Close(B) > HH`, SHORT при `Close(B) < LL`; фильтр уклона — `bias_len`-сглаживание Уайлдера по `Close` 15m.

- [ ] **Step 1: Написать падающий тест на валидацию параметров**

```python
def test_constructor_validates_params() -> None:
    with pytest.raises(ValueError, match="donch_len"):
        TwoSidedMomentumStrategy(donch_len=Decimal("0"))
    with pytest.raises(ValueError, match="atr_mult"):
        TwoSidedMomentumStrategy(atr_mult=Decimal("-1"))

def test_bias_len_accepts_zero_as_disabled() -> None:
    s = TwoSidedMomentumStrategy(bias_len=Decimal("0"))
    assert s.bias_len == 0
```

Второй тест — Review Focus 3: `positive_int` на нуле бросит `ValueError`, а ноль — легальное значение сетки.

- [ ] **Step 2: Написать падающий тест на `from_params`**

```python
def test_from_params_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="two_sided_momentum"):
        TwoSidedMomentumStrategy.from_params({"atr_len": Decimal("14")})
```

`atr_len` обязан быть неизвестным параметром: он константа (Global Constraint 10).

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.strategy.two_sided_momentum'`

- [ ] **Step 4: Реализовать каркас**

`bias_len`: `0` — фильтр выключен (`self._bias is None`), иначе `positive_int(...)`. `bias_len` — `int`-свойство (не Decimal), `atr_mult` и `donch_len` валидируются общими `positive`/`positive_int`. Окно Дончиана — `deque[tuple[Decimal, Decimal]]` с `maxlen=donch_len` (по `high` и `low` завершённого 15m-бара); `hh`/`ll` считаются по содержимому до добавления текущего бара.

- [ ] **Step 5: Написать тест на warmup: граница `max(donch_len, atr_len)`**

```python
def test_warmup_exposes_channel_only_when_both_windows_are_full() -> None:
    s = TwoSidedMomentumStrategy(donch_len=Decimal("3"), bias_len=Decimal("0"))
    bars = _rising_1m_bars(count=15 * 15)  # монотонно растущие 1m-бары: 15 завершённых 15m
    for bar in bars[: 14 * 15]:
        s.on_bar(bar, _flat_state())
    assert s._hh is None and s._ll is None and s._atr_value is None  # на баре 13 окно ещё неполно
    for bar in bars[14 * 15 :]:
        s.on_bar(bar, _flat_state())
    assert s._hh is not None and s._atr_value is not None  # на баре 15 полны оба окна
```

Граница warmup — `max(donch_len, _ATR_LEN)`, а не `donch_len`: `atr_len = 14` заморожен константой (Global Constraint 10), поэтому при `donch_len = 3` первый сигнал физически возможен только на 15-м закрытом 15m-баре. Тест смотрит на внутреннее состояние, а не на списки ордеров: на этой задаче стратегия ордера ещё не выдаёт вовсе (это задача 7), и «пустые списки на первых двух барах» были бы зелёными при полностью сломанной логике. Приватные атрибуты стратегии в тестах — принятый стиль пакета (так же читают `_lower`/`_adx` у `flat_keltner`).

Неполное окно не имеет права бросать исключение: «<30 сделок — расширять спек, а не тестировать» из гейта 1 предполагает, что warmup просто молча ничего не торгует.

- [ ] **Step 6: Реализовать обновление 15m**

Порядок на закрытом 15m-баре B: `atr = self._atr.update(B.high, B.low, B.close)`; `hh = max(h for h, _ in self._window)`, `ll = min(l for _, l in self._window)` **до** добавления B; `bias = self._bias.update(B.close)` если фильтр включён; затем `self._window.append((B.high, B.low))`. ATR и bias считаются включая бар B — спецификация замораживает «ATR на баре-сигнале», а сигнальный бар это B.

Имена состояния — `self._hh`, `self._ll`, `self._atr_value`, `self._bias_value` (`Decimal | None`), как `_atr15_value` у `FlatKeltnerStrategy`: тест warmup читает их напрямую, а задачи 7–10 опираются на то, что `None` означает «окно ещё не полно».

- [ ] **Step 7: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_two_sided_momentum.py -v`
Expected: PASS

- [ ] **Step 8: Коммит**

```bash
git add strategylab/strategy/two_sided_momentum.py tests/test_strategy_two_sided_momentum.py
git commit -m "feat: donchian and atr core of two-sided momentum strategy"
```

---

