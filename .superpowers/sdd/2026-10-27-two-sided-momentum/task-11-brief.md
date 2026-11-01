### Task 11: Регистрация, CLI и колонки развёртки

**Files:**
- Modify: `strategylab/strategy/examples.py` (`STRATEGY_CLASSES`, `build`)
- Modify: `pyproject.toml` (`[tool.setuptools] packages` — список пакетов, не модулей: правка нужна только если появляется новый пакет; здесь пакета нет, шаг обязан подтвердить это тестом)
- Modify: `scripts/sweep.py` (`METRIC_COLUMNS`, `_row`)
- Modify: `strategylab/cli.py` — ничего (стратегия берётся из реестра)
- Test: `tests/test_examples.py`, `tests/test_sweep.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `TwoSidedMomentumStrategy`, `Metrics.long`, `Metrics.short`
- Produces: `build("two_sided_momentum", params)`; CSV-колонки `long_trades, long_net_pnl, long_profit_factor, short_trades, short_net_pnl, short_profit_factor` шестью столбцами после `sharpe`

- [ ] **Step 1: Написать падающий тест на реестр**

```python
def test_build_returns_two_sided_momentum() -> None:
    s = build("two_sided_momentum", {})
    assert s.name == "two_sided_momentum"
```

- [ ] **Step 2: Написать тест на то, что `sweep.py` берёт дефолты из конструктора**

`_defaults(TwoSidedMomentumStrategy) == {"donch_len": Decimal("48"), "atr_mult": Decimal("1.5"), "bias_len": Decimal("48")}` — ровно три ключа. Это и есть гейт «≤3 свободных параметра», проверяемый механически: появление четвёртого параметра этот тест не переживёт.

- [ ] **Step 3: Написать падающий тест на шесть новых CSV-колонок**

```python
def test_sweep_row_includes_side_columns() -> None:
    sweep = _load_sweep()
    metrics = Metrics(equity=Decimal("10000"))
    fill = make_fill()
    for pnl in (Decimal("-20"), Decimal("-20")):
        metrics.record(fill, pnl, Side.BUY)
    metrics.record(fill, Decimal("100"), Side.SELL)
    row = sweep._row(report_with(metrics), {"donch_len": Decimal("48")}, ["donch_len"])
    assert row.split(",")[-6:] == ["2", "-40", "0", "1", "100", "null"]
```

`report_with(metrics)` — обёртка над `build_report` из этого файла (в `test_sweep.py` отчёты уже собираются, helper переиспользуется); `make_fill` и `Side` — импорты `tests/test_metrics.py` и `strategylab.broker.orders`. Пять последних ячеек до новых колонок менять нельзя: порядок столбцов — часть контракта CSV, его читает `gates.py`.

- [ ] **Step 4: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_examples.py tests/test_sweep.py -v`
Expected: FAIL — `UnknownStrategy` из `build` (не `KeyError`: `examples.build` бросает собственный тип, строка 98) и шесть колонок отсутствуют в хвосте строки CSV

- [ ] **Step 5: Реализовать регистрацию и колонки**

В `STRATEGY_CLASSES` добавить `"two_sided_momentum": TwoSidedMomentumStrategy`, в `build` — ветку `from_params`. В `METRIC_COLUMNS` дописать шесть имён, в `_row` — шесть ячеек в том же порядке.

- [ ] **Step 6: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_examples.py tests/test_sweep.py -v`
Expected: PASS

- [ ] **Step 7: Проверить слойность и пакеты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_layering.py -q` и `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — новый модуль живёт в `strategylab/strategy/`, существующего пакета это не ломает.

- [ ] **Step 8: Smoke-прогон по-настоящему, одна комбинация, короткое окно**

```powershell
$env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe -m strategylab.cli run data\btcusdt.db BTCUSDT MINUTE 1761523200 1762128000 two_sided_momentum --capital 10000
```
Expected: две текстовые вилки и строка `ВЕРДИКТ:`; окно 7 суток может не дать ни одной сделки — это легальный пустой прогон (план 1, Review Focus 3), и `trades: 0` — не ошибка.

- [ ] **Step 9: Линтер, типы, полный набор**

Run: `.venv\Scripts\python.exe -m ruff check .`, `.venv\Scripts\python.exe -m mypy strategylab`, `.venv\Scripts\python.exe -m pytest -q` (+ `-m network` с `STRATEGYLAB_NETWORK_TESTS=1`)
Expected: все зелёные

- [ ] **Step 10: Коммит**

```bash
git add strategylab/strategy/examples.py scripts/sweep.py tests
git commit -m "feat: register two_sided_momentum and publish sides in sweep"
```

---

