VERDICT: PASS

# Ревью `feat/funding-data` @ 3781dd7 (fresh eyes, независимый)

Ветка: `feat/funding-data` (3781dd7 «feat(funding): stdlib loader with validation and provenance»
от 82c6817 — как в дизайне). Worktree: `C:/Users/egoro/AppData/Local/Temp/opencode/wt_funding`.
Критерии сняты с design-draft ДО чтения кода. Живых запросов к Binance ревьюер НЕ делал
(все прогоны — на фикстурах/инжекциях; ветка по описанию ведущего уже проходила чистый
live dry-run — это его evidence, не моё).

## Гейты (exit-коды)

| Гейт | Команда (из корня worktree) | Exit | Результат |
|---|---|---|---|
| pytest | `PYTHONPATH=wt_funding; python -m pytest tests -q` | **0** | 415 passed, 3 skipped (7.75s) — ровно база + 23 новых |
| ruff | `python -m ruff check .` | **0** | All checks passed! (E,F,I,UP,B; line 100) |
| mypy (штатный гейт репо) | `python -m mypy strategylab scripts` | **0** | Success: no issues found in 43 source files |
| mypy (новый файл отдельно) | `python -m mypy scripts/funding/fetch_funding.py` | **0** | Success: 1 source file |
| mypy (только для сведения) | `python -m mypy tests/test_fetch_funding.py` | 1 | 7 ошибок — tests/ вне гейта mypy (см. FIND-3) |

## Чек-лист против design — лично в коде

1. **Только stdlib** — ✅. Импорты (fetch_funding.py:38-49): argparse, json, sqlite3, sys,
   time, urllib.parse, urllib.request, collections.abc, dataclasses, datetime, decimal,
   pathlib. Ни requests, ни pandas, ни новых зависимостей в pyproject (diff их не трогает).
2. **Валидация, все 4 проверки design** — ✅.
   - выравнивание `ts % 28800 == 0` (:223-231, «ВЫРАВНИВАНИЕ»);
   - непрерывность: гэп > 28800с между последовательными ts (:243-251, «НЕПРЕРЫВНОСТЬ»);
   - sanity `|rate| ≤ 0.05` через Decimal, кап включительно (:56, :233-241; тест на "0.05"/"-0.05" проходит, "0.0500001"/"-0.06" валит);
   - счётчик: `rows ≥ ((last−first)/28800+1) × 0.98`, целочисленная арифметика (:253-260);
   - провал → stderr с префиксами проверок (кап 10 сообщений + «…и ещё N», :197-202) и
     `SystemExit(1)` (:390-395); данные не пишутся (:301).
3. **Запись в существующие untracked data/*.db, идемпотентность** — ✅.
   Путь `repo_root/data/<symbol>.db` (:324-326, конвенция bars_1m);
   `CREATE TABLE IF NOT EXISTS funding_8h` (:65-74) — схема дословно из design
   (symbol TEXT NOT NULL, ts INTEGER NOT NULL, rate TEXT NOT NULL, mark_price/rate_type TEXT,
   PK (symbol, ts)); `INSERT OR REPLACE` по PK (:271-275); существующие таблицы (bars_1m)
   не затрагиваются; тесты идемпотентности и дедупа мс-джиттера (:249-265).
4. **Provenance JSON** — ✅. Сводка (:303-320): endpoint, params (symbol/startTime/endTime/limit),
   calls, rows_fetched/unique/inserted, min_ts, max_ts, dry_run, db, loaded_at (UTC ISO).
   Всегда в stdout (:383-384); `--report <path>` пишет файл, включая случай провала (:385-388).
5. **CLI-контракт** — ✅. `--symbols` (дефолт BTCUSDT ETHUSDT), `--start`/`--end`
   `type=int` **Unix-секунды** (×1000 при уходе в API, :369, :377), `--dry-run`, `--report`.
   Dry-run не пишет и не создаёт БД: `not dry_run` перед store (:301), покрыто тестом
   в обеих ветках — БД отсутствует (не создаётся) и БД есть (содержимое не меняется) (:284-318).
6. **Holdout/live не блокируется** — ✅. Зонального фильтра в загрузчике нет по дизайну:
   качается весь [start, end] (end по умолчанию — «сейчас»); границы зон
   fit [1761523200, 1790294400), BTC-предыстория [1704067200, 1761523200), holdout/live
   ts ≥ 1790294400 задокументированы в докстринге (:8-13) — дословно требование design
   «хранилище сырое, дисциплина — в запросах замеров».
7. **Тесты не стреляют в сеть** — ✅. Все 23 теста: записанные фикстуры (2 реальные страницы
   BTCUSDT ×1000) или инжекция `fetch`/`sleep`; единственный тест, проходящий через
   `fetch_page`, подменяет `_http_get_json` monkeypatch (:144-156). Живого флага нет вовсе —
   по дизайну живая загрузка ручной шаг исполнителя, не CI. Реальные data/*.db тестами
   не открываются (только tmp_path).
8. **Ядро не тронуто** — ✅. `git diff origin/calibration-suite...HEAD --stat`:
   ровно 4 файла — `scripts/funding/fetch_funding.py` (+399),
   `tests/test_fetch_funding.py` (+392), 2 фикстуры. Никаких engine/harness/стратегий/
   CostModel/pyproject. Diff чистый (worktree без незакоммиченного), 3781dd7 сидит на 82c6817.

Дополнительно проверено: пагинация строго продвигает курсор (`max(fundingTime)+1 мс`,
:187 — фикстуры дают ровно 2 вызова, курсор 2-й страницы 1732838400001, тест :176);
пауза 0.5с только между вызовами (:180-181); `fundingTime` отбраковывается если не int
(включая bool, :119); rate парсится Decimal-ом на этапе парсинга и в sanity; докстринг
декларирует конвенцию Decimal-строк bars_1m.

## Находки (без блокеров)

- **FIND-1 (INFO).** Счётчик и непрерывность считаются по диапазону `[first, last]`
  фактических строк, а не запрошенному `[start, end]`. Гипотетическое усечение начала
  ряда биржей программно не ловится — его видно только по `min_ts` в provenance.
  Практика: у BTC/ETH funding существует задолго до запрошенных стартов, live dry-run
  ведущего подтвердил min_ts == запрошенный старт (BTC 1704067200). Мерить глазами
  по provenance — нормально; при желании владельца можно позже усилить
  `min_ts == requested start` проверкой. Не требует действий до merge.
- **FIND-2 (LOW).** `FundingParseError` и сетевые ошибки urllib не оборачиваются в main —
  падают traceback-ом (exit всё равно ≠ 0, сообщение об ошибке видно). Четыре штатные
  проверки валидации имеют явные сообщения, как требует design; это ручной туалет,
  запускаемый исполнителем. Косметика, не дефект.
- **FIND-3 (INFO, вне гейта).** `mypy --strict` на `tests/test_fetch_funding.py` даёт 7
  ошибок (annotation у `_two_page_fetch`, `list[object]` у `_parsed`). Штатный гейт репо —
  `mypy strategylab scripts` (план calibration-suite, веха 8) — tests/ не покрывает;
  гейт чист (exit 0, 43 файла), сам скрипт под strict чист. Если владелец расширит
  гейт на tests/ — потребуется мелкая правка типов в тестах.
- **FIND-4 (INFO, не дефект ветки).** `_REPO_ROOT` резолвится относительно файла скрипта,
  поэтому живую загрузку нужно запускать из основного репозитория (после merge) — иначе
  `data/*.db` создадутся внутри worktree, а не в существующих локальных базах.
  Дизайн и докстринг это уже говорят («Запуск из корня репозитория»; merge → живая
  загрузка — план ведущего). Просто держать в голове исполнителю.

## Вердикт

Все 8 пунктов чек-листа design выполнены в коде лично мной; гейты pytest/ruff/mypy —
exit 0 по штатным конвенциям репо; 23 новых теста покрывают парсер, пагинацию, все 4
валидатора, идемпотентность, dry-run-невмешательство, provenance и CLI. Находок
severity major/blocker нет. **PASS.** Merge и живую загрузку (с прикладкой provenance-JSON)
можно проводить.
