### Task 2: `SideMetrics` и отношения в `Metrics`

**Files:**
- Modify: `strategylab/core/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `Fill`, `Side` (`strategylab.broker.orders`)
- Produces: `SideMetrics` (мутабельный dataclass со `slots`): `trades: int = 0`, `wins: int = 0`, `losses: int = 0`, `net_pnl: Decimal = Decimal("0")`, `record(self, pnl: Decimal) -> None`, `profit_factor(self) -> Decimal | None`, `win_rate(self) -> Decimal | None`; `Metrics.record(self, fill: Fill, pnl: Decimal, side: Side | None = None) -> None`; поля `Metrics.long: SideMetrics` и `Metrics.short: SideMetrics`

Третьего накопителя отношений в проекте быть не должно: `profit_factor` и `win_rate` уже вычислены по списку pnl, и два ихcopies разъедутся при первой же правке. Поэтому отношения выносятся в функции модуля, а `Metrics` и `SideMetrics` только вызывают их.

- [ ] **Step 1: Написать падающий тест на разбиение по стороне**

```python
def test_record_splits_by_side() -> None:
    m = Metrics(equity=Decimal("10000"))
    m.record(_fill(Side.SELL), Decimal("-50"), Side.BUY)   # лонг закрыт в убыток
    m.record(_fill(Side.BUY), Decimal("120"), Side.SELL)   # шорт закрыт в прибыль
    assert m.long.trades == 1 and m.long.losses == 1
    assert m.short.trades == 1 and m.short.wins == 1
    assert m.long.net_pnl == Decimal("-50")
    assert m.short.net_pnl == Decimal("120")
    assert m.trades == 2 and m.net_pnl == Decimal("70")
```

`_fill(side)` в `tests/test_metrics.py` нет — там есть `make_fill()` (строка 7) с зашитой стороной; помощник добавляется вокруг неё, а не вместо неё: `def _fill(side: Side) -> Fill: return replace(make_fill(), side=side)`, импорт `from dataclasses import replace`. Третий аргумент `record` — **сторона закрываемой позиции**, а не филла: у филла она противоположная, и перепутать их значит молча перевернуть весь гейт 5.

- [ ] **Step 2: Написать падающий тест на отношения стороны**

```python
def test_side_profit_factor_is_none_without_losses() -> None:
    m = Metrics()
    m.record(_fill(Side.BUY), Decimal("10"), Side.BUY)
    m.record(_fill(Side.BUY), Decimal("5"), Side.BUY)
    assert m.long.profit_factor() is None
    assert m.long.win_rate() == Decimal("1")
    assert m.short.trades == 0 and m.short.profit_factor() is None
```

- [ ] **Step 3: Написать тест, что вызов без стороны ничего не портит**

```python
def test_record_without_side_keeps_totals_only() -> None:
    m = Metrics(equity=Decimal("10000"))
    m.record(_fill(Side.SELL), Decimal("-5"))
    assert m.trades == 1 and m.net_pnl == Decimal("-5")
    assert m.long.trades == 0 and m.short.trades == 0
```

Три существующих теста этого файла зовут `record(fill, pnl)` двумя аргументами — они обязаны пройти без правки, иначе задача ломает чужой контракт вместо его расширения.

- [ ] **Step 4: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py -v`
Expected: FAIL — `TypeError: Metrics.record() takes ... positional arguments` или `AttributeError: 'Metrics' object has no attribute 'long'`

- [ ] **Step 5: Реализовать**

Функции модуля (их же использует `Metrics`):

```python
def profit_factor_of(pnls: Sequence[Decimal], losses: int) -> Decimal | None
def win_rate_of(wins: int, trades: int) -> Decimal | None
```

`SideMetrics.record(pnl)` обновляет `trades`/`wins`/`losses`/`net_pnl` и копит `pnl` в приватный `_pnls: list[Decimal] = field(default_factory=list, repr=False)`. `Metrics.record` вызывает существующую логику (equity, пик, drawdown) и затем, если `side is not None`, `getattr(self, "long" if side is Side.BUY else "short").record(pnl)`. Поля `long` и `short` — `field(default_factory=SideMetrics)`.

- [ ] **Step 6: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py -v`
Expected: PASS — включая три прежних теста файла без изменений

- [ ] **Step 7: Прогнать набор целиком и типы**

Run: `.venv\Scripts\python.exe -m pytest -q` и `.venv\Scripts\python.exe -m mypy strategylab`
Expected: 363+ passed, 3 skipped; mypy без ошибок

- [ ] **Step 8: Коммит**

```bash
git add strategylab/core/metrics.py tests/test_metrics.py
git commit -m "feat: split trade metrics by position side"
```

---

