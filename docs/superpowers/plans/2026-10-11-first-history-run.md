# План 1 — один прогон по истории, пригодный для чтения

> **Для агентских исполнителей:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans для выполнения этого плана по задачам. Шаги используют синтаксис чекбоксов (`- [ ]`) для отслеживания.

**Цель:** реализовать вертикальный срез, на котором одна стратегия с фиксированными параметрами прогоняется по истории и выдаёт отчёт, которому можно верить — с зонами данных, издержками, маржой, вилкой и обязательной шапкой.

**Архитектура:** одно ядро, один драйвер на этот план (`ReplayDriver`). Свечи хранятся только в 1m в SQLite; все крупные интервалы вычисляются на месте из закрытых минутных баров. Стратегия видит только закрытые бары, сигнал действует на следующий бар. Исполнение прогоняется дважды — пессимистичным и оптимистичным внутрибазовым порядком — и отчёт публикует обе вилки.

**Стек:** Python 3.12, SQLite3 из стандартной библиотеки, `Decimal` для денег и цен, pytest, ruff, mypy --strict.

**Спека:** `docs/superpowers/specs/2026-10-11-strategy-testing-environment-design.md`

---

## Global Constraints

Каждое требование ниже действует для **каждой** задачи этого плана.

1. **Деньги и цены — только `Decimal`.** `float` не допускается ни в одном месте, включая промежуточные вычисления.
2. **Время — `int`, Unix-секунды, UTC.** Миллисекунды биржи преобразуются в секунды ровно в одном месте: на границе сети.
3. **Хранится только 1m.** Интервалы 5m/15m/1h вычисляются функцией `aggregate`, отдельных строк для них в базе нет.
4. **Бар закрытый.** Тип данных не допускает «открытого» бара.
5. **Сигнал действует на следующий бар.** Порядок вызовов в `Engine.run` это гарантирует.
6. **Издержки и вилка присутствуют в каждом отчёте.** Отчёт без шапки не собирается — бросает исключение.
7. **`core/` не импортирует `drivers/`.** Нарушение ловится линтером, см. задачу 1.
8. **Зона бара неизменна.** Смена зоны — исключение, а не обновление.
9. **`profit_factor` возвращает `None`, а не бесконечность**, когда убыточных сделок нет. Сериализация обязана печатать `null`.
10. **Агрегация не соединяет бары через разрыв.** Неполная группа порождает `IncompleteBarGroup`, а не бар меньшего размера.
11. Ruff, line length 100. mypy --strict. pytest `asyncio_mode` не требуется — асинхронного кода в этом плане нет.
12. Коммит на каждую задачу. Сообщения в стиле существующего проекта: `feat:`, `test:`, `fix:`.

---

## Review Focus

Пять входов и отказов, которые спека подразумевает, но ни одна задача не проверяет явно. Порядок — по вероятности укусить.

1. **Убыточных сделок нет.** `profit_factor` обязан вернуть `None` и напечатать `null`. Раньше здесь был `Decimal("Infinity")`, и `json.dump` на нём падал — отчёт не должен роняться на успешной стратегии.
2. **Разрыв в данных внутри окна.** Агрегация не имеет права склеить 5m-бар из трёх 1m-баров, между которыми пропуск. Молчаливый недобар — самый опасный вид тихой порчи.
3. **Стратегия не возвращает ни одного ордера.** Пустой прогон обязан дать валидный отчёт с нулевыми метриками, а не исключение.
4. **Граница зоны проходит посреди окна.** Часть баров `fit`, часть `holdout` — отчёт обязан это показать, а отчёт с любым баром `fit` в проверочном прогоне обязан быть отвергнут.
5. **Окно из одного бара или пустое.** Деление на ноль в Sharpe и `profit_factor` недопустимо; округление до знаменателя обязано обрабатываться явно.

---

## Структура файлов

```
StrategyLab/
  pyproject.toml            упаковка, pytest, ruff, mypy
  strategylab/
    __init__.py
    core/
      bars.py               Bar, Interval
      engine.py             Engine.run — главный цикл
      feed.py               BarFeed (протокол)
      position.py           Position
      metrics.py            Metrics
    data/
      schema.py             DDL + connect
      store.py              CandleStore: запись, чтение, импорт зон
      zones.py              Zone, назначение и неизменяемость
      aggregate.py          aggregate(bars, factor)
      sync.py               загрузка 1m с Bybit (из проверенного spike)
    spec/
      spec.py               StrategySpec, версия = хеш содержимого
    strategy/
      base.py               протокол Strategy
      examples.py           две эталонные стратегии
    broker/
      orders.py             Order, Side, OrderKind, Fill
      costs.py              CostModel
      sim.py                BrokerSimulator
      fork.py               IntrabarOrder + прогон парой
    report/
      report.py             Report, шапка, вердикт по вилке
    drivers/
      replay.py             ReplayDriver
    cli.py
  tests/
    ...
```

Ответственность каждого файла — одна. Файл растёт выше примерно 200 строк — сигнал, что его пора делить.

---

### Task 1: Каркас проекта, схема хранилища, запрет на импорт драйвера

**Files:**
- Create: `pyproject.toml`
- Create: `strategylab/__init__.py`, `strategylab/core/__init__.py`, `strategylab/data/__init__.py`
- Create: `strategylab/data/schema.py`
- Test: `tests/test_schema.py`, `tests/test_layering.py`

**Interfaces:**
- Consumes: ничего
- Produces: `SCHEMA: str`, `connect(path: Path) -> sqlite3.Connection` — таблицы `bars_1m` и `zones`, обе с текстовым хранением `Decimal`

- [x] **Step 1: Создать репозиторий и каркас**

```bash
cd /f/mymood/Реальность/Программинг/StrategyLab
git init
```

Создать `pyproject.toml` с `[project] requires-python = ">=3.12"`, зависимостями `pytest`, `ruff`, `mypy` в dev-группе; секцией `[tool.pytest.ini_options]`, `[tool.ruff] line-length = 100`, `[tool.mypy] strict = true`. Создать пустые `__init__.py` в `strategylab/`, `strategylab/core/`, `strategylab/data/`.

- [x] **Step 2: Написать падающий тест на `Decimal` в базе**

`tests/test_schema.py`:

```python
from decimal import Decimal
from pathlib import Path

from strategylab.data.schema import SCHEMA, connect


def test_decimal_survives_round_trip(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO bars_1m VALUES (?,?,?,?,?,?,?,?)",
            ("BTCUSDT", 1717200000, "67659.5", "67735.6", "67650.0", "67663.2",
             "12.3456789012345", "fit"),
        )
        row = conn.execute(
            "SELECT open, high, low, close, volume FROM bars_1m"
        ).fetchone()
    assert row[0] == Decimal("67659.5")
    assert row[4] == Decimal("12.3456789012345")
```

- [x] **Step 3: Убедиться, что тест падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.data.schema'`

- [x] **Step 4: Реализовать `schema.py`**

`connect(path: Path) -> sqlite3.Connection` открывает базу, включает `PRAGMA foreign_keys = ON` и `PRAGMA journal_mode = WAL`. `SCHEMA` — строка с `bars_1m` (колонки `symbol TEXT, ts INTEGER, open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, zone TEXT`, `PRIMARY KEY (symbol, ts)`, `WITHOUT ROWID`) и индексом `idx_bars_1m_zone ON bars_1m(symbol, zone, ts)`, плюс таблица `zones` (колонки `symbol TEXT, start_ts INTEGER, end_ts INTEGER, zone TEXT`, `PRIMARY KEY (symbol, start_ts)`).

- [x] **Step 5: Запустить тест — должен пройти**

Run: `.venv\Scripts\python.exe -m pytest tests/test_schema.py -v`
Expected: PASS

- [x] **Step 6: Написать падающий тест на запрет слоёв**

`tests/test_layering.py` — обойти все `.py` в `strategylab/core/` и для каждого проверить, что в исходнике нет `from strategylab.drivers` и `import strategylab.drivers`. Ожидание — пустой список нарушителей.

- [x] **Step 7: Убедиться, что тест проходит тривиально**

Run: `.venv\Scripts\python.exe -m pytest tests/test_layering.py -v`
Expected: PASS (в `core/` пока нет файлов — тест честно ничего не ловит, но станет рабочим с задачи 10)

- [x] **Step 8: Коммит**

```bash
git add pyproject.toml strategylab tests
git commit -m "feat: project scaffold and candle storage schema"
```

---

### Task 2: Модель бара и интервалы

**Files:**
- Create: `strategylab/core/bars.py`
- Test: `tests/test_bars.py`

**Interfaces:**
- Consumes: ничего
- Produces: `Bar` (frozen dataclass), `Interval` (IntEnum: `MINUTE = 60`, `FIVE_MIN = 300`, `FIFTEEN_MIN = 900`, `HOUR = 3600`), `Interval.factor` — сколько 1m баров агрегируется в один бар этого интервала

- [x] **Step 1: Написать падающий тест**

```python
from decimal import Decimal

import pytest

from strategylab.core.bars import Bar, Interval


def _bar(ts: int, o: str, h: str, l: str, c: str, v: str = "1") -> Bar:
    return Bar(ts, Decimal(o), Decimal(h), Decimal(l), Decimal(c), Decimal(v))


def test_bar_is_frozen() -> None:
    b = _bar(1717200000, "1", "2", "0.5", "1.5")
    with pytest.raises(Exception):
        b.close = Decimal("9")  # type: ignore[misc]


def test_interval_factors() -> None:
    assert Interval.MINUTE.factor == 1
    assert Interval.FIVE_MIN.factor == 5
    assert Interval.FIFTEEN_MIN.factor == 15
    assert Interval.HOUR.factor == 60
```

- [x] **Step 2: Убедиться, что тест падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_bars.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.core.bars'`

- [x] **Step 3: Реализовать `bars.py`**

```python
@dataclass(frozen=True, slots=True)
class Bar:
    ts: int          # Unix-секунды, время ОТКРЫТИЯ бара, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class Interval(IntEnum):
    MINUTE = 60
    FIVE_MIN = 300
    FIFTEEN_MIN = 900
    HOUR = 3600

    @property
    def factor(self) -> int:
        return self.value // Interval.MINUTE.value
```

- [x] **Step 4: Запустить тест**

Run: `.venv\Scripts\python.exe -m pytest tests/test_bars.py -v`
Expected: PASS

- [x] **Step 5: Коммит**

```bash
git add strategylab/core/bars.py tests/test_bars.py
git commit -m "feat: closed bar model and interval enum"
```

---

### Task 3: Зоны данных и их неизменяемость

**Files:**
- Create: `strategylab/data/zones.py`
- Test: `tests/test_zones.py`

**Interfaces:**
- Consumes: `connect` из задачи 1, `Bar` из задачи 2
- Produces: `Zone(str, Enum)` со значениями `"fit"`, `"holdout"`, `"live"`; `assign_zone(conn, symbol, start_ts, end_ts, zone) -> None`; `zone_of(conn, symbol, ts) -> Zone | None`; `zones_touched(conn, symbol, start_ts, end_ts) -> tuple[Zone, ...]` — зоны, реально пересекающие окно, в порядке `Zone`; `InmutableZoneError(Exception)`

- [x] **Step 1: Написать падающие тесты**

Три теста: (а) `zone_of` возвращает `Zone.FIT` для `ts` внутри объявленного диапазона и `None` за его пределами; (б) повторный вызов `assign_zone` на пересекающемся диапазоне поднимает `InmutableZoneError`; (в) зона, материализованная в строке бара, не совпадает с зоной бара, взятого за пределами диапазона; (г) `zones_touched` для окна, пересекающего границу `fit`/`holdout`, возвращает **обе** зоны — это пункт 4 Review Focus, и он обязан быть закреплён тестом.

- [x] **Step 2: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_zones.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.data.zones'`

- [x] **Step 3: Реализовать `zones.py`**

`assign_zone` перед вставкой проверяет, не пересекается ли `(symbol, start_ts, end_ts)` с уже объявленным диапазоном той же зоны или любой другой. Пересечение — `InmutableZoneError`: зона бара неизменна. `zone_of` делает `SELECT` по таблице `zones` с условием `start_ts <= ? AND end_ts > ?`.

- [x] **Step 4: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_zones.py -v`
Expected: PASS

- [x] **Step 5: Коммит**

```bash
git add strategylab/data/zones.py tests/test_zones.py
git commit -m "feat: immutable data zones fit holdout live"
```

---

### Task 4: Хранилище свечей и загрузка 1m с биржи

**Files:**
- Create: `strategylab/data/store.py`, `strategylab/data/sync.py`
- Test: `tests/test_store.py`, `tests/test_sync.py`

**Interfaces:**
- Consumes: `Bar` (задача 2), `connect`/`SCHEMA` (задача 1), `Zone` (задача 3)
- Produces: `CandleStore(conn)` с методами `write(bars: Iterable[Bar], zone: Zone | None = None) -> int`, `read_1m(symbol: str, start_ts: int, end_ts: int) -> Iterator[Bar]`, `count(symbol: str, start_ts: int, end_ts: int) -> int`; `BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"`; `rows_to_bars(rows: list[list[str]]) -> list[Bar]` — разворот по убыванию, миллисекунды → секунды; `MalformedRow(Exception)`; `BybitError(Exception)`; `fetch_page(cursor_end: int, symbol: str) -> list[list[str]]`; `sync_1m(conn, symbol: str, start_ts: int, end_ts: int) -> int` — возвращает число загруженных баров

`CandleStore` живёт здесь, а не в задаче ядра: он владеет записью и чтением 1m, а `sync_1m` — его единственный писатель. `read_1m` обязан читать только столбцы `ts, open, high, low, close, volume` и **не** выбирать `zone`: драйвер реплэя не должен видеть зоны, зона приходит из шапки отчёта.

- [x] **Step 1: Написать падающие тесты на хранилище, разворот и единицы времени**

`tests/test_store.py` — круг запись-чтение и запрет утечки зоны:

```python
def test_write_then_read_1m_round_trips(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000)], zone=Zone.FIT)
    assert store.count("BTCUSDT", 1717200000, 1717200060) == 1
    assert [b.ts for b in store.read_1m("BTCUSDT", 1717200000, 1717200060)] == [1717200000]


def test_read_1m_does_not_select_the_zone_column(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.write([make_bar(1717200000)], zone=Zone.HOLDOUT)
    bar = next(store.read_1m("BTCUSDT", 1717200000, 1717200060))
    assert not hasattr(bar, "zone")
```

`tests/test_sync.py` — разворот и единицы времени:

```python
from strategylab.data.sync import rows_to_bars


def test_rows_are_reversed_and_timestamps_divided() -> None:
    rows = [
        ["1717200060000", "67663.2", "67700.0", "67650.0", "67690.0", "3", "1"],
        ["1717200000000", "67659.5", "67735.6", "67650.0", "67663.2", "2", "1"],
    ]
    bars = rows_to_bars(rows)
    assert [b.ts for b in bars] == [1717200000, 1717200060]
    assert bars[0].open == Decimal("67659.5")
    assert bars[1].close == Decimal("67663.2")
```

Здесь же — тест, что пропущенная строка длиной не 7 поднимает `MalformedRow`.

- [x] **Step 2: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_store.py tests/test_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.data.store'`

- [x] **Step 3: Реализовать `store.py` и `sync.py`**

`CandleStore.write` **обязан** приводить `Decimal` к `str` перед передачей в sqlite3 — `sqlite3` не умеет биндить `Decimal` и бросит `InterfaceError`. `read_1m` выбирает шесть колонок без `zone` и **не вызывает `Decimal()` повторно**: фабрика `connect()` уже отдаёт числовые значения как `Decimal`, а нечисловые как `str`, поэтому повторная обёртка упала бы с `TypeError`. `sync_1m` — единственный писатель: он вызывает `store.write(bars, zone=None)` и обязан быть no-op при повторном запуске, что проверяется отдельно.

`rows_to_bars` бросает `MalformedRow` при длине строки не 7, разворачивает список (свежие бары приходят первыми) и делит каждое `ts` на 1000. Сетевой доступ — `httpx`. `fetch_page` запрашивает `?category=linear&symbol=…&interval=1&end=…&limit=1000` и поднимает `BybitError` при `retCode != 0`. `sync_1m` идёт назад по `end`, пока не пересечёт `start_ts`, дедуплицирует по `ts`, фильтрует окном и вставляет через `INSERT OR IGNORE`.

- [x] **Step 4: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_store.py tests/test_sync.py -v`
Expected: PASS

- [x] **Step 5: Написать сетевой тест, помеченный на пропуск**

Тест с `@pytest.mark.network`, помечающий его `skipif` при отсутствии сети: одна страница с `end = 2020-04-01T00:00:00Z` возвращает 1000 баров, самый старый из которых равен 2020-03-31T10:40Z.

- [x] **Step 6: Коммит**

```bash
git add strategylab/data/store.py strategylab/data/sync.py tests/test_store.py tests/test_sync.py
git commit -m "feat: candle store and bybit 1m sync with descending row order"
```

---

### Task 5: Агрегация с проверкой полноты

**Files:**
- Create: `strategylab/data/aggregate.py`
- Test: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: `Bar`, `Interval`
- Produces: `IncompleteBarGroup(Exception)` с полями `expected: int`, `actual: int`, `ts: int`; `aggregate(bars: Sequence[Bar], interval: Interval) -> list[Bar]`

- [ ] **Step 1: Написать падающий тест на нормальную агрегацию**

```python
def test_aggregates_five_minutes() -> None:
    bars = [_bar(1717200000 + 60 * i, "1", "2", "0.5", "1.5") for i in range(10)]
    out = aggregate(bars, Interval.FIVE_MIN)
    assert len(out) == 2
    assert out[0].ts == 1717200000
    assert out[0].open == Decimal("1")
    assert out[0].high == Decimal("2")
    assert out[0].low == Decimal("0.5")
    assert out[0].close == Decimal("1.5")
    assert out[0].volume == Decimal("5")
```

- [ ] **Step 2: Написать падающий тест на разрыв внутри окна**

```python
def test_gap_does_not_produce_a_short_bar() -> None:
    # пять баров: 0,1,2 минуты, потом пропуск, потом 4-я минута
    bars = [_bar(1717200000 + 60 * i, "1", "2", "0.5", "1.5") for i in (0, 1, 2, 4)]
    with pytest.raises(IncompleteBarGroup) as exc:
        aggregate(bars, Interval.FIVE_MIN)
    assert exc.value.expected == 5
    assert exc.value.actual == 4
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_aggregate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.data.aggregate'`

- [ ] **Step 4: Реализовать `aggregate.py`**

Группировка идёт по `ts // (factor * 60)` с якорем на эпоху Unix, поэтому границы совпадают с биржевыми. `open` — первый, `close` — последний, `high` — максимум, `low` — минимум, `volume` — сумма. Если длина группы не равна `factor`, бросается `IncompleteBarGroup`. При `factor == 1` возвращается список без проверок.

- [ ] **Step 5: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_aggregate.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add strategylab/data/aggregate.py tests/test_aggregate.py
git commit -m "feat: interval aggregation refusing to bridge data gaps"
```

---

### Task 6: Сверка агрегации с биржёй — инвариант 1

**Files:**
- Create: `tests/test_aggregate_against_exchange.py`
- Modify: `pyproject.toml` — регистрация маркера `network`

**Interfaces:**
- Consumes: `aggregate`, `fetch_page`, `rows_to_bars`, `Bar`, `Interval`
- Produces: ничего; тест закрывает инвариант 1 спеки

- [ ] **Step 1: Зарегистрировать маркер**

В `pyproject.toml` в `[tool.pytest.ini_options] markers` добавить строку `network: требует доступа к api.bybit.com`.

- [ ] **Step 2: Написать тест**

Тест с `@pytest.mark.network`, `@pytest.mark.skipif(not os.environ.get("STRATEGYLAB_NETWORK_TESTS"))`. Забирает 1m за 2024-06-01 (две страницы: `end` на конец суток и на середину), фильтрует окном, агрегирует в 5m/15m/1h, сравнивает с нативными биржевыми барами тех же интервалов по всем пяти полям с допуском `1e-8` на ценах и `1e-3` на объёме. Первое расхождение падает с `assert`, печатающим время бара и оба значения.

- [ ] **Step 3: Убедиться, что тест пропускается без переменной окружения**

Run: `.venv\Scripts\python.exe -m pytest tests/test_aggregate_against_exchange.py -v`
Expected: SKIPPED

- [ ] **Step 4: Запустить по-настоящему**

Run: `$env:STRATEGYLAB_NETWORK_TESTS="1"; .venv\Scripts\python.exe -m pytest tests/test_aggregate_against_exchange.py -v`
Expected: PASS — 408 баров, 0 расхождений. Это уже проверено вручную 2026-10-11; при расхождении сначала ищи баг в коде, а не в бирже.

- [ ] **Step 5: Убрать переменную и коммит**

```bash
git add tests/test_aggregate_against_exchange.py pyproject.toml
git commit -m "test: verify local aggregation matches bybit bars"
```

---

### Task 7: Описание стратегии и версионирование

**Files:**
- Create: `strategylab/spec/spec.py`, `strategylab/spec/__init__.py`
- Test: `tests/test_spec.py`

**Interfaces:**
- Consumes: `Interval`
- Produces: `StrategySpec` (frozen dataclass) с полями `name: str`, `symbol: str`, `interval: Interval`, `start_ts: int`, `end_ts: int`, `params: Mapping[str, Decimal]`, `rules_ref: str`, `version: str`; `compute_version(spec) -> str` — первые 12 символов sha256 канонического JSON без поля `version`; `StrategySpec.dump() -> str`, `StrategySpec.load(text: str) -> StrategySpec`

- [ ] **Step 1: Написать падающий тест на стабильность версии**

```python
def test_version_is_stable_and_content_addressed() -> None:
    a = make_spec()
    b = make_spec()
    assert a.version == b.version
    c = replace(a, params={"slow": Decimal("20")})
    assert c.version != a.version
```

Здесь же: тест, что `load(dump(a)) == a` с точностью до версии, и что `rules_ref` обязателен и непуст.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_spec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.spec.spec'`

- [ ] **Step 3: Реализовать `spec.py`**

`compute_version` строит `json.dumps` с `sort_keys=True`, `separators=(",", ":")`, все `Decimal` — строкой, и берёт `sha256(...).hexdigest()[:12]`. Валидация в `__post_init__`: `name`, `symbol`, `rules_ref` непустые; `start_ts < end_ts`; `interval` — член `Interval`.

- [ ] **Step 4: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_spec.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add strategylab/spec tests/test_spec.py
git commit -m "feat: content-addressed strategy specification"
```

---

### Task 8: Типы ордеров, контракт стратегии и две эталонные стратегии

**Files:**
- Create: `strategylab/broker/orders.py`, `strategylab/broker/__init__.py`
- Create: `strategylab/strategy/base.py`, `strategylab/strategy/__init__.py`, `strategylab/strategy/examples.py`
- Test: `tests/test_orders.py`, `tests/test_strategy_base.py`, `tests/test_examples.py`

**Interfaces:**
- Consumes: `Bar` (задача 2), `Decimal`
- Produces: `Side(str, Enum)` = `BUY`/`SELL`; `OrderKind(str, Enum)` = `MARKET`/`LIMIT`/`STOP_MARKET`; `Order` (frozen: `side: Side`, `kind: OrderKind`, `qty: Decimal`, `limit_price: Decimal | None`, `stop_price: Decimal | None`, `reduce_only: bool`); `Fill` (frozen: `ts: int`, `side: Side`, `price: Decimal`, `qty: Decimal`, `fee: Decimal`, `reason: str`); протокол `Strategy` с полем `name: str` и методом `on_bar(self, bar: Bar, state: StrategyState) -> list[Order]`; `StrategyState` (frozen: `position: Decimal`, `equity: Decimal`, `bar_index: int`); реестр `build(name: str, params: Mapping[str, Decimal]) -> Strategy`; `UnknownStrategy(Exception)`; стратегии `"sma_cross"` и `"buy_hold"`

Типы ордеров объявляются **здесь**, а не в задаче брокера: стратегии обязаны иметь возможность вернуть `Order` раньше, чем появится брокер.

**Интерпретация спеки, зафиксированная здесь:** спека §5 перечисляет «правила входа и выхода» среди содержимого описания. В этом плане правила — это **исполняемый код** в `strategylab/strategy/`, а описание хранит ссылку `rules_ref` на модуль и данные параметров. Причина: правила, выраженные данными, порождают две версии «одного и того же» правила, и их невозможно проверить покрытием.

- [ ] **Step 1: Написать падающий тест на реестр**

```python
def test_build_returns_sma_cross_with_params() -> None:
    s = build("sma_cross", {"fast": Decimal("10"), "slow": Decimal("30")})
    assert s.name == "sma_cross"
```

Здесь же: `build("нет_такой", {})` поднимает `UnknownStrategy`.

- [ ] **Step 2: Написать падающий тест на `sma_cross`**

Пять баров с заведомо известной скользящей: `sma_cross` не выдаёт ордеров до появления нужного числа баров в состоянии, а при пересечении выдаёт ровно один `Order` вида `MARKET` на следующий интервал.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_base.py tests/test_examples.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.strategy.base'`

- [ ] **Step 4: Реализовать `base.py` и `examples.py`**

`Strategy` — `typing.Protocol` с `runtime_checkable`. `examples.py` содержит `SmaCrossStrategy` (состояние — два deque, длина равна `slow`, сигнал на пересечении средних) и `BuyHoldStrategy` (один `MARKET BUY` на первом баре, `reduce_only`-закрытие на последнем известном баре окна). Обе возвращают `list[Order]` и никогда не обращаются к `strategylab.broker`.

- [ ] **Step 5: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_base.py tests/test_examples.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add strategylab/strategy tests/test_strategy_base.py tests/test_examples.py
git commit -m "feat: strategy contract and two reference strategies"
```

---

### Task 9: Позиция и метрики

**Files:**
- Create: `strategylab/core/position.py`, `strategylab/core/metrics.py`
- Test: `tests/test_position.py`, `tests/test_metrics.py`

**Interfaces:**
- Consumes: `Decimal`
- Produces: `Position` с полями `side`, `qty: Decimal`, `entry_price: Decimal`, `stop_price: Decimal | None`, `take_profit: Decimal | None`, `unrealised(price: Decimal) -> Decimal`, `is_open -> bool`; `Metrics` с полями `trades: int`, `wins: int`, `losses: int`, `net_pnl: Decimal`, `equity: Decimal`, `max_drawdown: Decimal`, `record(fill: Fill) -> None`, `profit_factor() -> Decimal | None`, `win_rate() -> Decimal | None`, `sharpe() -> Decimal | None`

- [ ] **Step 1: Написать тест на `profit_factor` без убыточных сделок**

```python
def test_profit_factor_is_none_without_losses() -> None:
    m = Metrics()
    m.record(make_fill(pnl=Decimal("10")))
    m.record(make_fill(pnl=Decimal("5")))
    assert m.profit_factor() is None
```

Здесь же: пустые метрики дают `None` от всех трёх методов; `sharpe()` при нулевом знаменателе возвращает `None`; `max_drawdown` считается по пику капитала, а не от нуля.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_position.py tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.core.position'`

- [ ] **Step 3: Реализовать оба модуля**

`profit_factor` возвращает `None` при `losses == 0`. `sharpe` — `None` при отсутствии закрытых сделок либо при нулевом стандартном отклонении. Никакой код в проекте не имеет права конструировать `Decimal("Infinity")`.

- [ ] **Step 4: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_position.py tests/test_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add strategylab/core/position.py strategylab/core/metrics.py tests
git commit -m "feat: position and metrics with null-safe ratios"
```

---

### Task 10: Брокер-симулятор

**Files:**
- Create: `strategylab/broker/costs.py`, `strategylab/broker/sim.py`
- Test: `tests/test_broker_sim.py`

**Interfaces:**
- Consumes: `Bar`, `Order`, `Side`, `OrderKind`, `Fill` (задача 8), `Position`, `Metrics` (задача 9)
- Produces: `CostModel` (frozen: `taker_fee: Decimal = Decimal("0.0006")`, `maker_fee: Decimal = Decimal("0.0002")`, `slippage: Decimal = Decimal("0.0005")`, `leverage: Decimal = Decimal("1")`); `IntrabarOrder(IntEnum)` = `PESSIMISTIC = 0`, `OPTIMISTIC = 1`; `LiquidationError(Exception)`; `BrokerSimulator(costs: CostModel, intrabar: IntrabarOrder)` с методом `submit(order: Order) -> None` и `apply_bar(bar: Bar) -> list[Fill]`

- [ ] **Step 1: Написать тест на лимитное исполнение**

```python
def test_limit_buy_fills_at_limit_price_on_touch() -> None:
    b = BrokerSimulator(CostModel(), IntrabarOrder.PESSIMISTIC)
    b.submit(Order(Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("67652.71"), None, False))
    fills = b.apply_bar(_bar(1717200000, "67659.5", "67735.6", "67650.0", "67663.2"))
    assert len(fills) == 1
    assert fills[0].price == Decimal("67652.71")
```

- [ ] **Step 2: Написать тест на вилку стоп/цель**

Позиция открыта, у неё `stop_price` и `take_profit`, оба внутри диапазона бара. При `PESSIMISTIC` заполняется стоп, при `OPTIMISTIC` — цель. Проверяется `reason` фила.

- [ ] **Step 3: Написать тест на ликвидацию**

Позиция с плечом 1 при падении цены ниже точки, обеспеченной маржой, поднимает `LiquidationError` на баре, а не возвращает отрицательный капитал.

- [ ] **Step 4: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_broker_sim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.broker'`

- [ ] **Step 5: Реализовать брокер**

`apply_bar` обрабатывает бар строго в этом порядке: (1) срабатывание стоп-ордеров по `stop_price`, (2) проверка стоп-лосса и тейк-профита открытой позиции по `high`/`low` с учётом `IntrabarOrder`, (3) исполнение оставшихся лимитных заявок по касанию цены, (4) исполнение рыночных заявок по `close` этого бара — так как сигнал приходит на закрытии бара N и действует на следующий, заявка, поданная стратегией на этом баре, исполнится лишь на следующем вызове. Комиссия берётся по `maker_fee` для лимита и `taker_fee` для рынка и стопа. Рыночная цена = `close * (1 ± slippage)` по направлению сделки.

- [ ] **Step 6: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_broker_sim.py -v`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add strategylab/broker tests
git commit -m "feat: paper broker with fees margin and intrabar ordering"
```

---

### Task 11: Ядро и драйвер реплэя

**Files:**
- Create: `strategylab/core/feed.py`, `strategylab/core/engine.py`, `strategylab/drivers/__init__.py`, `strategylab/drivers/replay.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Bar`, `Strategy`, `BrokerSimulator`, `Metrics`, `aggregate`, `CandleStore`
- Produces: `BarFeed(Protocol)` с `bars() -> Iterator[Bar]`; `CandleStore.read_1m(symbol, start_ts, end_ts) -> Iterator[Bar]`; `ReplayDriver(store, symbol, interval, start_ts, end_ts) -> BarFeed`; `Engine.run(feed, strategy, broker, metrics) -> Metrics` — возвращает `Metrics` и роняет `strategylab.drivers` только на уровне модуля, не на уровне ядра

- [ ] **Step 1: Написать тест на порядок «сигнал — следующий бар»**

```python
def test_order_from_bar_n_fills_on_bar_n_plus_one() -> None:
    # Стратегия на барах 100..104 выдаёт MARKET BUY ровно на баре 102.
    # Ожидание: первый fill имеет ts не меньше ts бара 103.
    ...
    assert first_fill.ts >= bars[103].ts
```

- [ ] **Step 2: Написать тест на слойность**

Расширить `tests/test_layering.py`: обойти `.py` в `strategylab/core/` и проверить отсутствие подстроки `strategylab.drivers`. Ожидание — по-прежнему пусто.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_layering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.core.engine'`

- [ ] **Step 4: Реализовать `feed.py`, `engine.py`, `replay.py`**

`Engine.run` на каждом баре: сначала `broker.apply_bar(bar)`, затем `strategy.on_bar(bar, state)`, затем `broker.submit(...)` для всех полученных ордеров.

`ReplayDriver` реализует два требования спеки §8, и оба обязательны:

1. **Кэш агрегатов.** Интервал стратегии агрегируется **один раз на прогон** и переиспользуется обоими вилками. Ключ кэша — `(symbol, interval, start_ts, end_ts)`. Без этого стратегия на 1h читала бы 3.48 млн минутных баров вместо 5256, то есть в 660 раз медленнее. Кэш живёт в объекте `ReplayDriver`, а не в модуле — он переживает только один прогон.
2. **Ленивая загрузка минутных баров.** Внутри интервала стратегии минутные бары нужны брокеру **только пока позиция открыта**. `ReplayDriver` отдаёт крупные бары из кэша агрегатов, а минутные подтягивает из `CandleStore` лениво, по диапазону, когда брокер запросил их для расчёта исполнения. Минутные бары открытого периода не читаются вообще.

Разрыв внутри окна обязан приводить к `IncompleteBarGroup` из задачи 5, а не к пропущенному бару.

- [ ] **Step 5: Написать тест на ленивую загрузку**

Тест со счётчиком обращений к `CandleStore.read_1m`: прогон стратегии, не открывающей позицию, на окне из 2000 минут и интервале 1h обязан прочитать **ноль** минутных баров для исполнения. Тот же прогон со стратегией, держащей позицию весь час, обязан прочитать их.

- [ ] **Step 6: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_layering.py -v`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add strategylab/core strategylab/drivers tests
git commit -m "feat: replay engine driving strategy and broker"
```

---

### Task 12: Отчёт с обязательной шапкой

**Files:**
- Create: `strategylab/report/__init__.py`, `strategylab/report/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `StrategySpec`, `Metrics`, `Zone`, `CostModel`, `IntrabarOrder`
- Produces: `MissingReportHeader(Exception)`; `Report` (frozen) с полями `spec_version: str`, `zones_used: tuple[Zone, ...]`, `costs: CostModel`, `trials: int`, `intrabar: IntrabarOrder`, `metrics: Metrics`; `build_report(...) -> Report`, бросающая `MissingReportHeader`, если любое из первых четырёх полей — `None`; `Report.to_json() -> str` — `profit_factor` печатается как `null`; `Report.to_text() -> str`

- [ ] **Step 1: Написать тест на обязательность шапки**

```python
def test_report_refuses_missing_header() -> None:
    with pytest.raises(MissingReportHeader):
        build_report(spec=make_spec(), zones_used=None, costs=CostModel(),
                      trials=1, intrabar=IntrabarOrder.PESSIMISTIC,
                      metrics=Metrics())
```

- [ ] **Step 2: Написать тест на `null` вместо бесконечности**

```python
def test_profit_factor_serialises_as_null() -> None:
    r = make_report_with_metrics(wins_only=True)
    assert '"profit_factor": null' in r.to_json()
```

Здесь же — два теста из Review Focus, которые иначе уедут в никуда:

```python
def test_strategy_that_never_trades_still_produces_a_report() -> None:
    r = make_report_with_metrics(trades=0)
    assert r.to_json()          # не исключение
    assert '"trades": 0' in r.to_json()


def test_single_bar_window_does_not_divide_by_zero() -> None:
    r = make_report_with_metrics(trades=1, wins=1, losses=0, bars=1)
    assert r.metrics.sharpe() is None
    assert r.metrics.profit_factor() is None
```

Пустой прогон и окно из одного бара обязаны давать валидный отчёт. Успешная стратегия не имеет права ронять систему на `json.dump`.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.report'`

- [ ] **Step 4: Реализовать `report.py`**

`to_json` строит словарь в фиксированном порядке: пять полей шапки, затем метрики. Значения `Decimal` сериализуются строками; `None` — как `null`. `to_text` печатает шапку первыми пятью строками, метрики ниже.

- [ ] **Step 5: Запустить тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_report.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add strategylab/report tests/test_report.py
git commit -m "feat: report with mandatory provenance header"
```

---

### Task 13: Прогон парой и CLI

**Files:**
- Create: `strategylab/broker/fork.py`, `strategylab/cli.py`
- Test: `tests/test_fork.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Engine`, `ReplayDriver`, `BrokerSimulator`, `IntrabarOrder`, `build_report`
- Produces: `Fork` (frozen) с полями `pessimistic: Report`, `optimistic: Report`; `run_fork(...) -> Fork` — два прогона одного и того же окна с двумя настройками `IntrabarOrder`; `diverges(verdict: Callable[[Report], bool]) -> bool`; `cli.py` с командой `run`

- [ ] **Step 1: Написать тест на расхождение вердиктов через реальный прогон**

Тест обязан прогнать настоящее окно через `run_fork`, а не конструировать метрики из переданных чисел. Окно взято из спеки §13.1 — это реальные биржевые бары, где стоп и цель лежат внутри одной минуты:

```python
def test_fork_diverges_when_stop_and_target_share_one_minute() -> None:
    # Бар 00:05 2024-06-01: H=67735.6, L=67650.0. Оба уровня внутри него.
    # Вход 67693.33, стоп 67652.71, цель 67733.95.
    f = run_fork(store=make_store_with_real_bars(), spec=make_spec(
        symbol="BTCUSDT", interval=Interval.MINUTE,
        start_ts=1717200000, end_ts=1717200300,
    ), strategy=make_fixed_position_strategy(
        entry=Decimal("67693.33"), stop=Decimal("67652.71"),
        take_profit=Decimal("67733.95"),
    ), costs=CostModel())
    assert f.pessimistic.metrics.net_pnl < f.optimistic.metrics.net_pnl
    assert f.diverges(verdict=lambda r: r.metrics.net_pnl > 0) is True
```

Тест, который подставляет готовые `pessimistic_pnl` и `optimistic_pnl` в аргументы, запрещён: он зелёный при полностью сломанном `run_fork`.

- [ ] **Step 2: Написать тест на CLI**

`tests/test_cli.py` вызывает `cli.main(["run", ...])` на временной базе из пяти 1m-баров и проверяет, что напечатан отчёт с пятью строками шапки и вердиктом вилки.

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fork.py tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategylab.broker.fork'`

- [ ] **Step 4: Реализовать `fork.py` и `cli.py`**

`run_fork` строит две стратегии и два брокера из одних и тех же `spec`, `store` и окна, прогоняет `Engine.run` дважды и заворачивает в `Fork`. `diverges` вызывает `verdict` на обоих отчётах и возвращает `True` при расхождении. `cli.py` — `argparse` с подкомандами `run` и `sync`; `run` печатает `report.to_text()` обоих вилок и строку `ВЕРДИКТ: измерима` либо `ВЕРДИКТ: НЕ ИЗМЕРИМА — вилки расходятся по вердикту`.

- [ ] **Step 5: Запустить полный набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, сети не требуется; сетевые тесты — SKIPPED

- [ ] **Step 6: Прогнать линтер и типы**

Run: `.venv\Scripts\python.exe -m ruff check .` и `.venv\Scripts\python.exe -m mypy strategylab`
Expected: оба без ошибок

- [ ] **Step 7: Коммит**

```bash
git add strategylab tests
git commit -m "feat: fork runner and command line entry point"
```

---

## Что этот план закрывает и что остаётся

**Закрыто:** инварианты 1–4 и 7–8 из спеки, полностью. Инвариант 9 — механически, через `diverges`. Инвариант 5 (зона бара неизменна) — через `ImmutableZoneError`. Инвариант 6 (одна проверка на версию) и стадии 2–3 — **в этом плане намеренно не закрыты**, они предмет плана 2.

**Дальше:**

- **План 2** — подбор параметров, walk-forward, журнал проверок по версиям, отвержение отчёта при наличии баров `fit`. Закрывает инвариант 6.
- **План 3** — `LiveDriver` и стадия 3 со сроком.
- **План 4** — наблюдение и уведомления.

**Правило, которое действует во всех планах и берёт начало здесь:** каждая стадия доводится до рабочей до начала следующей. Этот план доводит стадию 2 для фиксированного набора параметров.