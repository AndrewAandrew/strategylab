# StrategyLab

Среда для тестирования торговых стратегий (strategylab, 0.1.0). Python >= 3.12, основная зависимость — httpx.

## Быстрый старт

Без uv (Windows; на mac/linux активация — `source .venv/bin/activate`):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install pytest
```

С uv (создаст `.venv` и поставит dev-группу: pytest, ruff, mypy):

```bash
uv sync
```

Тесты:

```bash
pytest
```

CLI не установлен как консольная команда, запуск через модуль:

```bash
python -m strategylab.cli --help
```

Команды (`init`, `zone`) — их сигнатуры смотрите в `--help`.

## Структура репозитория

- `strategylab/` — пакет:
  - `core/` — bars, feed, engine, metrics, position, trades (журнал сделок)
  - `broker/` — costs, orders, симулятор сделок `sim`, fork
  - `data/` — store (локальный `.db`), schema, sync, aggregate, zones
  - `drivers/` — replay
  - `strategy/` — base, indicators, examples; стратегии `flat_keltner`, `sweep_liquidity`, `trend_pullback`
  - `report/`, `spec/` — отчёты и спеки
- `tests/` — pytest-тесты
- `docs/superpowers/` — дизайн-документ среды и план первой прогонки истории
- `strategydocs/` — спеки и отчёты по конкретным стратегиям (с графиками)
- `scripts/` — рабочие скрипты: `sweep.py`, `export_flat_keltner.py`

## Данные

Локальная база данных — файлы `.db` в `data/`. В репозиторий не входят (`gitignore`), у каждого пользователя своя.

## Спеки стратегий

- `strategydocs/` — спеки и отчёты по конкретным стратегиям
- `docs/superpowers/` — дизайн-документ среды и план первой прогонки истории

## Качество кода

- Стиль: `ruff` (line-length 100), типы проверяет `mypy`.
- Тесты лежат в `tests/`. Часть тестов помечена маркером `network` (требует `api.bybit.com`) и по умолчанию пропускается.
