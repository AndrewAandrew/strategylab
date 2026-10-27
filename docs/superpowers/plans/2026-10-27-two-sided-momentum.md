# Спека 1 «Двусторонний импульс/пробой» — реализация и первый прогон

> **Для агентских исполнителей:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans для выполнения этого плана по задачам. Шаги используют синтаксис чекбоксов (`- [ ]`) для отслеживания.

**Цель:** реализовать стратегию `two_sided_momentum` по pre-registered спеке, добавить в движок раздельный учёт долга и шорта и провести развёртку 18 комбинаций на fit-окне календаря спеки, посчитав гейты до holdout.

**Архитектура:** ядро, брокер и драйвер не меняются поведению — меняются только два места: `Metrics` начинает копить сделки по направлению позиции (движок уже знает это направление), а `Report` печатает долг и шорт раздельно, как требует гейт 5 спеки. Стратегия приходит готовым пакетом индикаторов (`indicators.Aggregator`, `Atr`, `Rma`) и той же схемой «MARKET-вход, брекет на баре после исполнения», что и три существующие стратегии. Развёртка и гейты — скрипты вне пакета, как `scripts/sweep.py`.

**Стек:** Python 3.12+ (на этой машине 3.14), SQLite3 из стандартной библиотеки, `Decimal` для денег и цен, pytest, ruff, mypy --strict.

**Спека:** `strategydocs/spec_01_two_sided_momentum_20261022.md` (pre-registered v1, 2026-10-22)
**Отчёт, породивший эту спеку:** `strategydocs/отчёт_протестировано_3_спеков_20261022.md`
**Дизайн среды:** `docs/superpowers/specs/2026-10-11-strategy-testing-environment-design.md`

---

## Global Constraints

Каждое требование ниже действует для **каждой** задачи этого плана.

1. **Деньги и цены — только `Decimal`.** `float` не допускается ни в одном месте, включая промежуточные вычисления и значения сетки.
2. **Время — `int`, Unix-секунды, UTC.**
3. **Хранится только 1m.** 15m-бары стратегия собирает сама через `Aggregator(FIFTEEN_MIN_WIDTH, FIFTEEN_MIN_COUNT)` — отдельных строк в базе нет.
4. **Бар закрытый. Сигнал действует на следующий бар.** Брекет ставится на баре после исполнения входа — как во всех трёх существующих стратегиях.
5. **Издержки и вилка присутствуют в каждом отчёте.** Отчёт без шапки не собирается.
6. **`core/` не импортирует `drivers/`.** Ловится `tests/test_layering.py`.
7. **Зона бара неизменна.** Смена зоны — исключение, а не обновление.
8. **`profit_factor` возвращает `None`, а не бесконечность.** Сериализация печатает `null`.
9. **Никакого `Decimal("Infinity")` ни в одном месте проекта.**
10. **Свободных параметров — ровно три** (`donch_len`, `atr_mult`, `bias_len`). `atr_len = 14` и `risk_frac = 0.01` — константы, в `PARAM_NAMES` не входят и через `--param` не задаются: спека зафиксировала «≤3 свободных параметра», и четвёртый параметр этот гейт ломает.
11. **Издержки — базовые условия движка:** `CostModel()` по умолчанию (taker 0.0006, maker 0.0002, slippage 0.0005). Никакой задачи «сделать комиссии ниже» в этом плане нет.
12. **Прогоны только по зоне `fit`.** Holdout и live этого календаря не запускаются ни разу: касание регистрируется в `data/touch_registry.json` и расходуется безвозвратно. Зоны объявляются, прогоны по ним — нет.
13. Ruff, line length 100. mypy --strict. Асинхронного кода нет.
14. Коммит на каждую задачу. Сообщения в стиле проекта: `feat:`, `test:`, `fix:`, `docs:`.
15. **Windows:** любая команда, печатающая кириллицу (CLI-отчёт, sweep), требует `$env:PYTHONUTF8 = "1"` — иначе `UnicodeEncodeError` в cp1251.

---

## Review Focus

Пять входов и отказов, которые спека подразумевает, но ни одна задача ниже явно не проверяет. Порядок — по вероятности укусить. Каждый пункт закрывается тестом в задаче, которой он принадлежит.

1. **Трейлинг шорт-стопа на гэпе** (задача 10). Зеркальный стоп короткой позиции исполняется вверх по `high`, а не вниз по `low`: `_stop_fill` для `Side.BUY` смотрит `bar.high < stop`, для `Side.SELL` — `bar.low > stop`. Существующие тесты двигают только лонг-стоп. Ошибка направления здесь даёт «стоп не выбило» на падающем рынке и ровный P&L из ниоткуда.
2. **Переворот одним маркет-филлом** (задача 9). `Position.apply_fill` в ветке переворота берёт `new_fee = fill.fee * (fill.qty - self.qty) / fill.qty` — входная комиссия новой ноги приходит из закрывающего филла. Если стратегия подаст вместо одного переворачивающего ордера два (закрытие и открытие), `net_pnl` и разбивка долг/шорт разойдутся с суммой журнала сделок.
3. **`bias_len = 0` — выключенный фильтр** (задачи 6 и 11). `positive_int` бросил бы `ValueError` на нуле, а сетка спеки содержит `bias_len ∈ {0, 48}`: ноль обязан приниматься конструктором и означать «фильтра нет», иначе `sweep.py` отбракует девять из восемнадцати комбинаций и медианный PF сетки будет посчитан по половине сетки.
4. **Вилки могут сойтись** (задача 12). Фиксированной цели у стратегии нет вовсе, поэтому `PESSIMISTIC` и `OPTIMISTIC` нередко дают одинаковый `net_pnl`, и вердикт «измерима» перестаёт что-либо утверждать. Это не баг и не повод для гордости: в отчёте развёртки ширина вилки печатается явно, и совпадение вилок читается как факт, а не как успех.
5. **Сведение окна в конце прогона** (задача 3). `_close_window` закрывает открытую позицию на последнем баре; сделка обязана попасть в bucket **стороны позиции**, а не стороны закрывающего филла, иначе последний шорт запишется как долг и гейт 5 врёт.

---

## Структура файлов

```
strategylab/
  core/
    metrics.py       + SideMetrics, общие функции отношений, record(fill, pnl, side)
    engine.py        передаёт сторону закрываемой позиции в metrics.record
  broker/
    sim.py           вытеснение устаревшего зеркального стопа при повторной постановке
  report/
    report.py        шесть новых полей: long_trades, long_net_pnl, long_profit_factor и short_*
  strategy/
    two_sided_momentum.py   новая стратегия: Дончиан + ATR-стоп с трейлингом + EMA-уклон
    examples.py             регистрация в STRATEGY_CLASSES и build()
scripts/
  sweep.py          шесть новых колонок CSV
  gates.py          подсчёт гейт-таблицы по CSV развёртки (новая)
tests/
  test_metrics.py, test_engine_trades.py, test_report.py, test_broker_sim.py,
  test_strategy_two_sided_momentum.py (новая), test_sweep.py
```

---

### Task 1: Календарь спеки: данные и зоны

Кода эта задача не пишет. Она обязана быть первой: без баров спекиные окна нельзя ни объявить, ни прогнать, и любая последующая задача проверялась бы на временной базе вместо настоящих свечей.

**Files:**
- Create (вне git, `data/` в `.gitignore`): `data\btcusdt.db`, `data\ethusdt.db`
- Modify: таблицы `zones` в обеих базах

- [ ] **Step 1: Проверить покрытие по чанкам, а не одним итогом**

Итоговое число баров не отличает «не докачан один месяц» от «пропуски рассыпаны по всему окну», а вторые как раз и рождают `IncompleteBarGroup` посреди развёртки.

Run (из корня репозитория):
```powershell
$env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe -X utf8 -c "from strategylab.data.schema import connect
from strategylab.data.store import CandleStore
chunks=[(1761523200,1761955200),(1761955200,1764547200),(1764547200,1767225600),(1767225600,1769904000),(1769904000,1772323200),(1772323200,1775001600),(1775001600,1777593600),(1777593600,1780272000),(1780272000,1782864000),(1782864000,1785542400),(1785542400,1788220800),(1788220800,1790812800),(1790812800,1792627200)]
for sym, db in [('BTCUSDT','data/btcusdt.db'),('ETHUSDT','data/ethusdt.db')]:
    c=connect(db); st=CandleStore(c,sym); miss=[(a,b,st.count(sym,a,b),(b-a)//60) for a,b in chunks if st.count(sym,a,b)!=(b-a)//60]
    print(sym,'total',st.count(sym,1761523200,1792627200),'want 518400; коротких чанков:',len(miss),miss)
    c.close()"
```
Expected: `total 518400 ... коротких чанков: 0` для обоих символов.

Если чанков коротких больше нуля — это отказ биржи, а не порча данных, и лечится он снаружи его кода: `fetch_page` повторяет только оборванный сетевой хендшейк, а `BybitError` (в частности `retCode=10006 Too many visits`) бросает сразу, по документированному решению «отказ биржи повторами не лечатся, молчаливый ретрай лишь отложил бы падение, потеряв его причину». Значит пауза между запросами принадлежит вызывающему: докачка тем же `cli sync` по пропускам, между попытками 45·N секунд, до пяти попыток на чанк. `INSERT OR IGNORE` делает повтор безопасным. Проверено на этой машине: шесть чанков BTCUSDT (2026-04-01 → 2026-10-01) легли со второй попытки именно так.

- [ ] **Step 2: Проверить сплошность внутри окна**

Run: `SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars_1m WHERE symbol=? AND ts>=1761523200 AND ts<1792627200`, сверка `count * 60 == max - min + 60`.
Expected: равенство для обоих символов. Границы `MIN`/`MAX` берутся **из окна**, а не из таблицы целиком: в `data\btcusdt.db` лежит ещё 2024 год без зоны, и глобальный `min/max` честно покажет разрыв в 297 суток там, где данных хватает. Именно на этом спотыкается первая проверка.

- [ ] **Step 3: Проверить, что зон в этих базах ещё нет**

Run: `SELECT symbol, start_ts, end_ts, zone FROM zones` в обеих базах.
Expected: 0 строк. Если строки есть — **остановиться и спросить владельца базы**, а не чинить: `DELETE FROM zones` и перелицовка зон запрещены Global Constraint 7 («смена зоны — исключение, а не обновление»), а отчёт друга говорит, что его BTC-фит исторически объявлен «двумя подзонами», то есть в его базе один интервал fit выглядит как две строки. На этой машине проверено: обе базы пусты по `zones`, `data/touch_registry.json` отсутствует — касаний не было.

- [ ] **Step 4: Объявить зоны BTCUSDT**

```powershell
.\.venv\Scripts\python.exe -m strategylab.cli zone data\btcusdt.db BTCUSDT 1761523200 1790294400 fit
.\.venv\Scripts\python.exe -m strategylab.cli zone data\btcusdt.db BTCUSDT 1790294400 1791849600 holdout
.\.venv\Scripts\python.exe -m strategylab.cli zone data\btcusdt.db BTCUSDT 1791849600 1792627200 live
```
Expected: три строки `объявлена зона ...`. Границы — 333 / 18 / 9 суток, ровно как в отчёте друга. `holdout` и `live` объявляются, чтобы `sweep.py` мог отвергнуть прогон по ним механически и зарегистрировать касание; **ни один прогон этого плана в них не запускается** (Global Constraint 12). Фит объявляется одним интервалом, а не двумя подзонами: у этой базы своя история, а `assign_zone` соседние полуинтервалы пересечением не считает.

- [ ] **Step 5: Объявить зоны ETHUSDT**

Те же три команды для `data\ethusdt.db` и символа `ETHUSDT`.
Expected: то же.

- [ ] **Step 6: Проверить неизменность**

Run: `cli zone data\btcusdt.db BTCUSDT 1790294400 1791849600 fit`
Expected: `InmutableZoneError` с текстом про пересечение с уже объявленной зоной `holdout`. Это не ошибка задачи, а подтверждение инварианта 5 спеки среды. Объявленная `holdout`-зона потом не превращается в `fit` — и наоборот, поэтому границы берутся ровно из спеки, до первого прогона.

---

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

### Task 3: Движок передаёт сторону закрываемой позиции

**Files:**
- Modify: `strategylab/core/engine.py:120-138` (`_record_realized`), `strategylab/core/engine.py:154-169` (`_close_window`)
- Test: `tests/test_engine_trades.py`

**Interfaces:**
- Consumes: `Metrics.record(fill, pnl, side)` (задача 2), `Side`
- Produces: тот же `Engine.run`; у возвращаемых `Metrics` заполнены `long` и `short`

- [ ] **Step 1: Написать падающий тест на шорт через реальный прогон**

```python
def test_short_trade_lands_in_short_bucket() -> None:
    metrics = _run({0: [_market(Side.SELL, "1")], 2: [_market(Side.BUY, "1")]}, None)
    assert metrics.short.trades == 1
    assert metrics.long.trades == 0
    assert metrics.short.net_pnl == metrics.net_pnl
    assert metrics.long.net_pnl + metrics.short.net_pnl == metrics.net_pnl
```

Помощники этого файла уже делают всё нужное: `_run(script, trade_log)` гоняет `ScriptedStrategy` по `ListFeed(_bars(5))` с `PESSIMISTIC`-вилкой, `_market(side, qty)` строит маркет-ордер. Своего `_feed` здесь нет и изобретать его не надо.

- [ ] **Step 2: Написать падающий тест на сведение окна (Review Focus 5)**

```python
def test_window_close_is_bucketed_by_position_side() -> None:
    # Стратегия открывает шорт и больше ничего: движок сводит окно на последнем баре.
    metrics = _run({0: [_market(Side.SELL, "1")]}, None)
    assert metrics.short.trades == 1 and metrics.long.trades == 0
    assert metrics.short.net_pnl == metrics.net_pnl
```

Смысл теста именно в том, что `_close_window` записывает сделку в bucket **стороны позиции**: закрывающий филл короткой позиции — `Side.BUY`, и если взять сторону филла, последний шорт отчёта станет долгом.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine_trades.py -v`
Expected: FAIL — `assert 0 == 1` на `metrics.short.trades`

- [ ] **Step 4: Реализовать**

`_record_realized` уже получает `before_side: Side` — передать его в `metrics.record(fill, realized, before_side)`. В `_close_window` взять сторону позиции **до** `position.apply_fill(fill)` и передать в `metrics.record(fill, realized, side_before_close)`. Никаких других изменений в движке нет: порядок вызовов и pnl-формулы не тронуты.

- [ ] **Step 5: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine_trades.py tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 6: Проверить инвариант суммы**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, включая существующий `test_engine_trades.py::...` про совпадение суммы pnl журнала и `net_pnl`: `long.net_pnl + short.net_pnl == net_pnl` обязан выполняться во всех прогонах — добавить это утверждение в тест шага 1.

- [ ] **Step 7: Коммит**

```bash
git add strategylab/core/engine.py tests/test_engine_trades.py
git commit -m "feat: engine attributes realized pnl to position side"
```

---

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

### Task 5: Брокер вытесняет устаревший зеркальный стоп

Без этой задачи трейлинг неработающ производительнее: каждая повторная `reduce_only` STOP_MARKET заявка остаётся в `_pending` до закрытия позиции (`_apply_stops` их пропускает, `_cancel_reduced` снимает только совпадающие по стороне с позицией), а `apply_bar` проходит по `_pending` пять раз за бар. На 518 400 барах и сделке с сотнями обновлений стопа это квадратичная деградация прогона.

**Files:**
- Modify: `strategylab/broker/sim.py:48-60` (`_mirror_reduced`)
- Test: `tests/test_broker_sim.py`

**Interfaces:**
- Consumes: `Order`, `OrderKind`, `Side`
- Produces: тот же `submit`; после второй постановки `reduce_only` STOP_MARKET той же стороны в `pending_orders` остаётся ровно одна такая заявка

- [ ] **Step 1: Написать падающий тест**

```python
def test_second_stop_submit_replaces_the_resting_mirror() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.BUY, OrderKind.MARKET, Decimal("1"), None, None, False))
    b.apply_bar(_bar((1, "100", "101", "99", "100")))
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("95"), True))
    b.submit(Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("1"), None, Decimal("98"), True))
    stops = [o for o in b.pending_orders if o.kind is OrderKind.STOP_MARKET]
    assert len(stops) == 1 and stops[0].stop_price == Decimal("98")
    assert b.position.stop_price == Decimal("98")
```

- [ ] **Step 2: Написать тест, что частичная цель не вытесняется (не путать с вытеснением стопа)**

```python
def test_limit_mirror_is_not_deduplicated() -> None:
    # Две reduce_only LIMIT разных объёмов — разные цели: движок обязан оставить обе.
    ...
    assert len([o for o in b.pending_orders if o.kind is OrderKind.LIMIT]) == 2
```

Ветка `sweep_liquidity` live-периода полагается на то, что resting-цели доживают до своего век; дедупликация вводится только для STOP_MARKET и только по стороне.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_broker_sim.py -v`
Expected: FAIL — `assert 2 == 1`

- [ ] **Step 4: Реализовать**

В `_mirror_reduced` по ветке STOP_MARKET отфильтровать `self._pending`, убрав заявки с `reduce_only and kind is OrderKind.STOP_MARKET and side == order.side`. Вызов идёт до `append`, поэтому новая заявка остаётся. Смысл в шапке метода: зеркало уже перезаписано в позицию, устаревшая resting-заявка не может исполниться (`_apply_stops` пропускает `reduce_only`) и только оплачивает обход.

- [ ] **Step 5: Запустить тесты и весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — 363 + новые; в частности `test_broker_sim.py` и `test_strategy_sweep_liquidity.py` (частичные тейки) остаются зелёными.

- [ ] **Step 6: Коммит**

```bash
git add strategylab/broker/sim.py tests/test_broker_sim.py
git commit -m "fix: superseded mirrored stops leave the pending queue"
```

---

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

### Task 12: Развёртка 18 комбинаций, гейты и отчёт

Задача читает то, что дал план, и спорить с числами не имеет права: вердикт по pre-registered спеке фиксируется как написан, включая заранее зарегистрированный исход провала.

**Files:**
- Create: `scripts/gates.py`
- Create: `strategydocs/отчёт_spec_01_two_sided_momentum_20261027.md`
- Create (вне git): CSV-развёртки в `data/`

**Interfaces:**
- Consumes: `data\sweep_btc_fit.csv`, `data\sweep_eth_fit.csv`, `data\sweep_eth_fold1.csv`, `data\sweep_eth_fold2.csv`, `data\sweep_eth_fold3.csv`
- Produces: `gates.py --btc <csv> --eth <csv> --folds <csv> <csv> <csv>` печатает таблицу пяти гейтов и строку `ГЕЙТЫ: пройдены` / `ГЕЙТЫ: не пройдены`

Дефолтная комбинация, по которой считаются гейты 1, 3 и 4, — константа модуля `DEFAULT_COMBO = {"donch_len": Decimal("48"), "atr_mult": Decimal("1.5"), "bias_len": Decimal("48")}` (pre-registered дефолты спеки); ряд CSV опознаётся по значениям трёх именных колонок, а не по номеру строки.

**Контракт stdout `sweep.py`, который обязан читать парсер.** Формат зафиксирован кодом (`_header`, строки 353–382): сначала 10 строк шапки `ключ: значение`, затем строка заголовка CSV, начинающаяся с `spec_version,`, затем по строке на комбинацию, затем пустая строка и хвост `комбинаций: N` / (если было) `отклонено: N` / `elapsed: ...`. Отклонённые комбинации печатаются в **stderr** и в CSV не видны вовсе — отсюда обязательная проверка «ровно 18 строк», а не «сколько удалось».

**Перенаправление.** В Windows PowerShell `>` пишет UTF-16LE с BOM, и `gates.py` прочитал бы мусор: развёртки пишутся через `| Out-File -Encoding utf8`, а файл открывается `encoding="utf-8-sig"` — на случай, если кто-то всё же воспользуется `>`.

- [ ] **Step 1: Замерить цену одного прогона до запуска сетки**

Run:
```powershell
$env:PYTHONUTF8="1"
Measure-Command { .\.venv\Scripts\python.exe -m strategylab.cli run data\btcusdt.db BTCUSDT MINUTE 1761523200 1771113600 two_sided_momentum --capital 10000 }
```
Expected: время одного прогона на 111 суток. Умножить на 3 (столько фолдов) и на 39 (столько прогонов в задаче). Если оценка больше ~40 минут — сказать владельцу **до** запуска сетки: развёртка самое дорогое, что есть в плане, и перезапускать её из-за неверного порядка шагов нельзя. Этот же шаг — первая проверка стратегии на живых барах: `IncompleteBarGroup` здесь падает громко и дёшево, а не на восьмой комбинации.

- [ ] **Step 2: Прогнать сетку на BTC fit**

```powershell
$env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe scripts\sweep.py data\btcusdt.db BTCUSDT 1761523200 1790294400 two_sided_momentum `
  --param donch_len=24,48,96 --param atr_mult=1.0,1.5,2.0 --param bias_len=0,48 `
  | Out-File -Encoding utf8 data\sweep_btc_fit.csv
```
Expected: 18 строк CSV, шапка с `zones_used: fit`, `touches: holdout=0, live=0`. Ноль в последних — единственная проверка Global Constraint 12: если там не ноль, окно задано неправильно и чистая история уже тронута, и дальнейшие шаги обязаны остановиться. «Ровно 18» — проверка Review Focus 3: если `bias_len = 0` не прошёл валидацию стратегии, строк станет 9, и медиана будет посчитана по половине сетки, а `отклонена:` из stderr при перенаправлении потеряется.

- [ ] **Step 3: Те же 18 комбинаций на полном ETH fit**

Та же команда для `data\ethusdt.db ETHUSDT 1761523200 1790294400` → `data\sweep_eth_fit.csv`.
Expected: 18 строк, `zones_used: fit`. Это не украшение: заранее зарегистрированный провал спека формулирует как «медианный PF сетки < 1.15 **на BTC и ETH**», значит медиана обязана быть посчитана на обоих инструментах, и для этого нужна полная ETH-сетка, а не только дефолтная комбинация.

- [ ] **Step 4: Дефолтная комбинация на трёх непересекающихся ETH-фолдах**

Фолды — треть окна каждая: `[1761523200, 1771113600)`, `[1771113600, 1780704000)`, `[1780704000, 1790294400)` (333 суток / 3 = 111 суток). По одному прогону на фолд с дефолтными `donch_len=48, atr_mult=1.5, bias_len=48` → `data\sweep_eth_fold1.csv`, `..._fold2.csv`, `..._fold3.csv`.
Expected: по одной строке CSV в каждом файле, `zones_used: fit` во всех трёх шапках. Сетка на фолдах не гоняется: гейт 4 требует знак P&L, а не поиск.

- [ ] **Step 5: Написать `scripts/gates.py` падающим тестом**

Тест `tests/test_gates.py` вызывает `gates.evaluate(...)` на синтетических CSV и ждёт таблицу из пяти гейтов. Правила, которые иначе пришлось бы угадывать:

1. **Гейт 1 — ≥100 сделок на инструмент.** Сумма `long_trades + short_trades` строки дефолтной комбинации, проверяется и на BTC, и на ETH. Спека оговорила третий исход отдельно: `<30 — расширять спек`, поэтому печатается `расширять спек` при <30 и `не пройден` в диапазоне 30–99.
2. **Гейт 2 — медианный PF сетки ≥ 1.15 на каждом инструменте**: по 18 строкам BTC и по 18 строкам ETH. `null` в ячейке `profit_factor` означает «у комбинации ни одной убыточной сделки» — математически бесконечное преимущество, поэтому в медиане такой ряд ранжируется **выше** любого числа: не выбрасывается (иначе сетка тихо теряет лучшие комбинации) и не превращается в ноль (иначе безубыточную комбинацию обвинили бы в провале). Сколько рядов было `null`, печатается отдельной строкой: три-четыре бесконечных PF означают, что медиана держится на малом числе сделок.
3. **Гейт 3 — PF дефолтной комбинации ≥ 1.3** при базовых двойных комиссиях (`CostModel()` по умолчанию — это и есть «уже фик движка»).
4. **Гейт 4 — ≥2 из 3 ETH-фолдов с `net_pnl > 0`** на дефолтной комбинации.
5. **Гейт 5 — раздельность опубликована**: у обеих сторон `trades ≥ 1`; если `net_pnl` одной стороны ≤ 0, а другая удерживает всю прибыль — пометка `одно-система`. Это требование к отчёту и к честности интерпретации, а не число: строка `ГЕЙТЫ: пройдены / не пройдены` складывается из гейтов 1–4, гейт 5 помечается отдельно и молча вердикт не переворачивает — иначе «спека сводится к одно-системе» было бы неотличимо от «спека прошла».

Все числа берутся из pessimistic-развёртки (её печатает `sweep.py`, `intrabar: PESSIMISTIC` в шапке); optimistic участвует только в шаге 7. Парсер: `encoding="utf-8-sig"`, строки до `spec_version,` пропустить, первая пустая строка означает конец таблицы, `null` в `profit_factor` читается как `None`, а не как 0.

- [ ] **Step 6: Реализовать и прогнать**

Run: `.venv\Scripts\python.exe scripts\gates.py --btc data\sweep_btc_fit.csv --eth data\sweep_eth_fit.csv --folds data\sweep_eth_fold1.csv data\sweep_eth_fold2.csv data\sweep_eth_fold3.csv`
Expected: таблица + вердикт. `gates.py` ничего не пишет в базу и не трогает `touch_registry.json`.

- [ ] **Step 7: Проверить ширину вилки (Review Focus 4)**

Из тех же прогонов сравнить `pessimistic` и `optimistic` (`cli run` на дефолтной комбинации того же окна): рассчитанный `net_pnl` обеих вилок печатать в отчёте явно. Совпадение вилок при отсутствии цели — ожидаемо и записывается фактом, а не выводом о robustness.

- [ ] **Step 8: Написать отчёт**

`strategydocs/отчёт_spec_01_two_sided_momentum_20261027.md`: шапка (дата, движок, издержки, зоны и число касаний), таблица 18 комбинаций BTC, таблица 18 комбинаций ETH, медиана и центр PF по каждому инструменту, таблица трёх ETH-фолдов, разбивка долг/шорт дефолтной комбинации, ширина вилки (шаг 7), вердикт по пяти гейтам, и — при непроходе — дословно зарегистрированный в спеке вывод: «2025-10..2026-10 не торговать классику вообще; следующий шаг — не подгон параметров, а смена класса идей». Holdout и live не тронуты — это отдельной строкой, с числом касаний из шапки развёртки.

- [ ] **Step 9: Полный набор, линтер, типы**

Run: `pytest -q`, `ruff check .`, `mypy strategylab`
Expected: всё зелёное

- [ ] **Step 10: Коммит**

```bash
git add scripts/gates.py tests/test_gates.py strategydocs/отчёт_spec_01_two_sided_momentum_20261027.md
git commit -m "feat: gate evaluation for two-sided momentum and spec 01 report"
```

---

## Что этот план закрывает и что остаётся

**Закрывает:** спеку `spec_01_two_sided_momentum_20261022.md` целиком, включая гейты 1–5, и гейт инварианта 5 среды в новом месте — раздельный долг/шорт в отчёте.

**Не закрывает намеренно:** инвариант 6 спеки среды (одна проверка на версию) и план 2 (подбор параметров, walk-forward, отвержение отчёта с барами `fit`) — это по-прежнему предмет отдельного плана. Фолды в задаче 12 считаются вручную по трём непересекающимся окнам, как написано в pre-registered спеке, а не автоматическим walk-forward.

**После:** если гейты пройдены —Holdout-прогон по 18 суткам `[1790294400, 1791849600)` делает **друг**: одно касание, одна запись в `data/touch_registry.json`, и это окно потрачено. Если не пройдены — действует заранее зарегистрированный вывод спеки, и следующий шаг это смена класса идей, а не сетка параметров.
