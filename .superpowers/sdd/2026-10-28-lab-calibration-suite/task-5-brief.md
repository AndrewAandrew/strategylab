### Task 5: Стратегия `limit_pullback` (волны maker-лимиток) + тесты

**Files:**
- Create: `strategylab/strategy/limit_pullback.py`
- Modify: `strategylab/strategy/examples.py` (import + `STRATEGY_CLASSES["limit_pullback"]` + ветка `build`)
- Test: `tests/test_strategy_limit_pullback.py`

**Interfaces:**
- Consumes: Task 4 `Order(..., expires_in_bars=...)`; `StrategyState(position, equity, bar_index)`.
- Produces: `LimitPullbackStrategy(depth: Decimal, ttl_bars: Decimal, hold_bars: Decimal)` — ttl/hold через `int()` (стиль `SmaCrossStrategy:31-32`), валидация `0 < depth < 1`, `ttl_bars >= 1`, `hold_bars >= 1` (ValueError); `from_params(Mapping[str, Decimal])` (стиль flat_keltner); `name = "limit_pullback"`. Поведение (design §7 волны): если `state.position == 0` и `state.bar_index % ttl == 0` → одна `[Order(Side.BUY, OrderKind.LIMIT, Decimal(1), limit_price=bar.close*(1-depth), None, False, expires_in_bars=ttl)]`; при открытой позиции — никаких BUY; на `bar_index - entry_bar >= hold` (entry_bar — бар перехода позиции из 0) → `[Order(Side.SELL, OrderKind.MARKET, Decimal(1), None, None, True)]` один раз (флаг `_exit_submitted` до возврата во флэт).

- [ ] **Step 1: Тесты** (юнит — вызов `on_bar` на списке баров; интеграционные 3 — Engine+BrokerSimulator на маленьких базах как в `tests/test_engine.py`):
```python
def test_wave_entry_limit_maker_fill():        # касание low -> Fill reason "limit", price == лимит ровно
def test_ttl_expiry_cancels_entry():           # нет касания за e баров -> pending пуст на S+e+1
def test_no_new_limits_while_position_open():  # лонг жив: ни одного BUY-ордера
def test_exit_market_reduce_only_after_hold(): # через hold баров -> MARKET SELL reduce_only, ровно один
def test_only_one_fillable_entry_limit():      # на границе волны в очереди 2 (транзенторно), но касание даёт ровно один entry-филл: на баре S+e старая ещё исполнима (новая подана после обработки бара), на S+e+1 уже мертва — двойного филла не существует (owner-решение A3)
def test_params_validation():                  # depth/ttl/hold границы
```
- [ ] **Step 2:** pytest файла → FAIL.
- [ ] **Step 3: Реализовать** модуль + регистрация (STRATEGY_CLASSES, build, импорт в examples.py — как остальные).
- [ ] **Step 4:** PASS файла; полный pytest/ruff/mypy чисто.
- [ ] **Step 5:** `git add strategylab/strategy/limit_pullback.py strategylab/strategy/examples.py tests/test_strategy_limit_pullback.py; git commit -m "feat(strategy): limit_pullback wave-scheduled maker entries with TIF"` (на calibration-suite).

---

