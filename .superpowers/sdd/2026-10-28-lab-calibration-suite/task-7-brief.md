### Task 7: Стратегия `dca_grid` (адверсариальная, без self-гейта) + тесты

**Files:**
- Create: `strategylab/strategy/dca_grid.py`
- Modify: `strategylab/strategy/examples.py` (реестр + `build`)
- Test: `tests/test_strategy_dca_grid.py`

**Interfaces:**
- Consumes: `Order(BUY, MARKET, Decimal(1), None, None, False)` (expiry не нужен).
- Produces: `DcaGridStrategy(step: Decimal, layers: Decimal)` — `layers=int()` конвертация, валидация `0 < step < 1`, `layers >= 1`; `from_params`; `name = "dca_grid"`. Логика (design §8 с owner-пометкой A5: геометрия вместо «цены последнего филла» — невыполнима в API, `StrategyState` не отдаёт цены филлов): опорная цена = `close` первого сигнала (bar_index==0); `bought` — счётчик выпущенных лотов; на каждом баре при `position != 0` и `bought < layers + 1` и `bar.close <= опорная·(1−step)**bought` → `[BUY MARKET 1]`. СТОПОВ/ТЕЙКОВ/ВЫХОДОВ/ГЕЙТА `equity<=0` нет — намеренно (design §8: house-гейт «починил» бы измеряемое).

- [ ] **Step 1: Тесты:**
```python
def test_first_buy_on_bar_zero():                 # bar 0 -> BUY; нет само-гейта при отрицательном equity state
def test_layers_at_geometric_levels():            # падение на step пороги -> ровно один BUY на уровень
def test_stops_at_layer_cap():                    # bought == layers+1 -> больше ни одного ордера
def test_no_equity_self_gate():                   # equity=Decimal("-1000"), под уровнем -> BUY ВЫДАН (пин отсутствия гейта)
def test_no_exit_orders_ever():                   # SELL отсутствует на растущем и падающем path
def test_params_validation():
```
- [ ] **Step 2:** FAIL → **Step 3: Реализовать + регистрация** → **Step 4:** PASS; полный pytest/ruff/mypy → **Step 5:** commit `-m "feat(strategy): dca_grid adversarial ladder without account-level gate"`.
