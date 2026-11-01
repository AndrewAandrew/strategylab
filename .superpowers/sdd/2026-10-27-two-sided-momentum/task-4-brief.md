### Task 4: Отчёт печатает долг и шорт раздельно

Это гейт 5 спеки: «долг и шорт публикаются раздельно в отчёте; если одна сторона тянет всю прибыль — спека сводится к одно-системе».

**Files:**
- Modify: `strategylab/report/report.py`
- Test: `tests/test_report.py`
- Test: `tests/test_cli.py` — `test_run_prints_both_reports_and_verdict` (строки 83–120) сверяет **каждую** строку stdout целиком (`assert out.splitlines() == expected`), обе вилки заканчиваются на `sharpe: null`. Шесть новых строк отчёта красят этот тест, и правка ожидания принадлежит этой задаче, а не следующей.

**Interfaces:**
- Consumes: `Metrics.long`, `Metrics.short`, `_plain`, `_plain_or_null`
- Produces: новые строки `to_text()` и новые ключи `to_json()` в фиксированном порядке: `long_trades`, `long_net_pnl`, `long_profit_factor`, `short_trades`, `short_net_pnl`, `short_profit_factor` — шестью строками после `sharpe`

- [ ] **Step 1: Написать тест на шесть строк**

```python
def test_report_publishes_sides_separately() -> None:
    r = make_report_with_metrics(
        long_pnls=(Decimal("-20"), Decimal("-20")), short_pnls=(Decimal("100"),)
    )
    text = r.to_text()
    assert "long_trades: 2" in text and "long_net_pnl: -40" in text
    assert "short_trades: 1" in text and "short_net_pnl: 100" in text
    assert "long_profit_factor: 0" in text        # убытки есть, прибыли нет
    assert "short_profit_factor: null" in text    # убыточных сделок у стороны нет
```

Помощник `make_report_with_metrics(trades, wins, losses)` (строка 109) копит метрики **повтором `metrics.record`**, и new-параметры обязаны встроиться в тот же механизм: `long_pnls: Sequence[Decimal] = ()` и `short_pnls: Sequence[Decimal] = ()`, каждый pnl записывается `metrics.record(fill, pnl, Side.BUY)` / `Side.SELL` теми же вызовами, что и прежние `wins`/`losses`. Значения по умолчанию — пустые кортежи, поэтому прежние вызовы не меняются. Прямо задать `SideMetrics.trades` и `net_pnl`, оставив `_pnls` пустым, нельзя: `long_profit_factor: null` при двух убыточных сделках недостижим честным путём (PF обязан быть `0`), а такой fixture нарушил бы инвариант суммы, который закрепляет задача 3.

- [ ] **Step 2: Написать тест на JSON и порядок ключей**

```python
def test_json_keys_include_sides_after_sharpe() -> None:
    payload = json.loads(make_report_with_metrics().to_json())
    assert list(payload)[-6:] == [
        "long_trades", "long_net_pnl", "long_profit_factor",
        "short_trades", "short_net_pnl", "short_profit_factor",
    ]
```

Порядок ключей — часть контракта отчёта (`to_json` строит словарь в фиксированном порядке), поэтому он и пинится тестом, а не следует из реализации.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_report.py -v`
Expected: FAIL — `KeyError`/`assert` на отсутствии строк

- [ ] **Step 4: Реализовать**

Шесть элементов в `to_text` и шесть ключей в конец `payload` в `to_json`; `profit_factor` стороны через `_plain_or_null` — `None` обязан печататься как `null`/`null` (инвариант 9).

- [ ] **Step 5: Дополнить два пина полных отчётов**

`test_report.py::test_to_text_includes_the_ratio_metrics_with_the_json_values` и `test_cli.py::test_run_prints_both_reports_and_verdict` сверяют текст целиком. Менять надо ожидание, а не формат: шесть строк дописываются после `sharpe`, порядок — `long_trades, long_net_pnl, long_profit_factor, short_trades, short_net_pnl, short_profit_factor`.

В `test_cli.py` для `buy_hold` (одна сделка, позиция длинная, `net_pnl: -127.083013460`) в **обоих** блоках — PESSIMISTIC и OPTIMISTIC — после `sharpe: null` добавляются:

```python
    "long_trades: 1",
    "long_net_pnl: -127.083013460",
    "long_profit_factor: 0",
    "short_trades: 0",
    "short_net_pnl: 0",
    "short_profit_factor: null",
```

- [ ] **Step 6: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_report.py tests/test_cli.py -v`
Expected: PASS — прежние проверки формата остаются зелёными, к ним добавляются шесть строк.

- [ ] **Step 7: Коммит**

```bash
git add strategylab/report/report.py tests/test_report.py
git commit -m "feat: report publishes long and short metrics separately"
```

---

