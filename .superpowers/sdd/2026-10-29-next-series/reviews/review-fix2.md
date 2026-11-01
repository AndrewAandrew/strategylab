VERDICT: PASS

# Брат-ревью: fix/window-close-cancel-all (Фикс 2, дефект C-3) @ d4bad44

Worktree: `C:/Users/egoro/AppData/Local/Temp/opencode/wt_fix2` (diff `origin/calibration-suite...HEAD`, 1 commit: `d4bad44 fix(engine): cancel pending orders at window close`, 6 файлов, +262/−20).

Критерии сняты ДО чтения кода: design-draft «Фикс 2» (п. 1–3 + протокол верификации) и `strategydocs/калибровка_набор_итог_20261028.md` стр. 30–64 (находка C, п. 1–4). Baseline-diff ведущего (40/40, 119 IDENTICAL + 41 INTENDED) принят как данность и НЕ переделывался.

## Шаг 4 — семантика против дизайна (проверено лично в коде)

1. **cancel_all первым в `_close_window`** — ДА. `engine.py:162`: `broker.cancel_all()` — первая строка тела, до снятия `broker.position` и до какого-либо эффекта сведения (`engine.py:163–179`). Порядок из design-п.1 («СНАЧАЛА cancel_all, ПОТОМ закрытие по bar.close») соблюдён буквально.
2. **Счётчик ровно на число снятых** — ДА. `sim.py:57–60`: `cancelled = [rest.order for rest in self._pending]` (снимок), `self._window_cancelled += len(cancelled)`, затем `_pending = []`. Инкремент строго по длине снятого списка, дублей и пропусков нет; повторный вызов на пустой очереди даёт `[]` и 0 приращения (тест `test_cancel_all_returns_orders_clears_queue_and_counts`).
3. **Expired/fill-семантика не сломана** — ДА. Филлы возможны только внутри `apply_bar` (`sim.py:89–106`), а submit того же бара идёт ПОСЛЕ `apply_bar` (`engine.py:63→78–79`) → заявка последнего бара не филлится и не может попасть в `window_cancelled` дважды. TIF-списание (`sim.py:91–96`) происходит в начале `apply_bar` до попыток филла → expired и fill взаимоисключены. `cancel_all` забирает только остаток `_pending` → категории filled / expired / window_cancelled попарно исключены, заявка не может быть одновременно filled и window_cancelled. Подтверждено тестом-конструктором `test_accounting_identity_submits_fills_expired_window_cancelled` (сценарий 3 заявки: 1 fill + 1 expired + 1 cancelled, тождество = 0).
4. **Pending-колонки отчётов после окна = 0** — ДА. Снимок в `harness._run_side` берётся после `Engine().run()` (`harness.py:154–159`), очередь к этому моменту всегда пуста → `_pending_counts` → `(0, 0)` для ОБЕИХ строк csv (`cell_rows`, `harness.py:210–224`); учётная строка `# window_cancelled:` добавляется на каждую ячейку (`harness.py:412`) в `report_p.txt`. Тесты проверяют `0/0` и `report.count("# window_cancelled: ") == 2`.
5. **4 адаптации старых тестов — законные, не заметание** — ДА:
   - `test_pending_snapshot_visible`: намерение «снимок ПОСЛЕ последнего submit» сохранено — `window_cancelled == 3` включает MARKET последнего бара (его видно только если он был подан), проверка на обеих вилках; ослабления нет.
   - `test_cli_grid_writes_frozen_artifacts`: чисто аддитивная проверка (2 учётные строки), ничего не снято.
   - `test_cli_reduce_only_pending_split`: `pending_other 2→0` заменено на `0` + `# window_cancelled: 2` + прежняя проверка `journal … ,market` (позиция сведена сводкой). Намерение теста (сводка окна + учёт снимаемых reduce-only) сохранено.
   - `test_engine::test_signal_on_the_last_bar_does_not_execute`: заявка больше не переживает окно — прямое следствие спека; добавлен счётчик, прежние assert'ы (не филлится, trades=0) не тронуты.
   - Якорь против заметания — **мутационный тест** `test_mutation_without_cancel_all_pending_survives`: cancel_all → no-op воспроизводит старое поведение (очередь переживает окно, счётчик 0). Ничего из старых тестов не удалено.

Дополнительно: `broker.cancel_all()` и свойство `window_cancelled` реализованы в `sim.py` (не в engine) — доступны вне зависимости от руннера; `_close_window` — единственная точка закрытия окна (`engine.py:81`), других руннеров в обход `Engine.run` нет (`run_fork` не переуполняет очередь, харнесс — потребитель).

## Находки

### [Moderate] `scripts/calib/verify_spec04.py` (V4.2/V4.3) не обновлён под фикс
Независимая машина верификатора закреплена до фикса (коммит `efdb854`, реплей старой семантики без `cancel_all` в конце окна) и сравнивает csv:
- V4.2 (`verify_spec04.py:338–347`): `csv pending == m.pending_limit/m.pending_other` — машина предсказывает пережившие окно заявки (`m.pending_limit = len(resting)`, `:197`), пост-фиксу csv всегда `0` → **FAIL в тех самых 13/24 ячейках**;
- V4.3 (`:355–359`): `expired == submits − fills − pending_csv` — при csv-pending=0 правая часть завышена на число снятых → **FAIL там же**.
В гейты (pytest/ruff/mypy) верификатор не входит — на PASS по фиксу не влияет. Но документ `калибровка_spec_04…:194` обещает «ожидание: 100×PASS, exit 0»: после мержа и перепрогона сетки задокументированная процедура «harness → verify_spec04» даст ложные FAIL, которые легко спутать с регрессией. Комментарий `:338–339` («пережившие окно заявки — finding, не провал») теперь описывает упразднённое поведение. Рекомендация (follow-up, вне этого ветки): ожидания V4.2/V4.3 перевести на `pending=0` + чтение `# window_cancelled` из report, либо явно задокументировать, что верификатор применим только к артефактам до фикса. В design-draft это названо «задокументировать как intended accounting diff» — но документация касается артефактов, а верификатор-скрипт остался со старой семантикой.

### [Minor] opt-сторона: счётчик собирается, но не персистится (ответ на вопрос ведущего (в))
`ForkSide.window_cancelled` заполняется на обеих вилках (`harness.py:107,158`), но в артефакты уходит только `# window_cancelled: {pess.window_cancelled}` в `report_p.txt` (`harness.py:412`); `report_o.txt` не существует by design (report собирается с `IntrabarOrder.PESSIMISTIC`, `harness.py:403` — пре-существующая конвенция, не регрессия фикса).

**Достаточно ли pess-only для прозрачности? — В целом да, с одним непокрытым краем:**
- для pess-замеров (вилка, у которой вообще есть report) — достаточно: счётчик на каждую ячейку, инвариант полностью проверяем из артефактов;
- гигиена очереди видна построчно на ОБЕИХ вилках: pending-колонки csv нулевые и для opt-строки;
- равенство счётчиков вилок есть хотя бы в тесте (`opt.window_cancelled == 3`, `test_calib_harness.py:368`).
- **Пробел:** состав очереди зависит от внутрибарового порядка (вилки могут разойтись). Если opt-счётчик когда-либо отличится от pess, из артефактов это не будет видно, и тождество `submits − fills − expired − window_cancelled == 0` для opt-вилки не проверяемо в принципе. Мелкое улучшение: одна строка `# window_cancelled: pess=N opt=M` (без изменения замороженной csv-схемы §10) — тогда прозрачность обеих вилок полная. Блокером не является.

### [Minor] Docstring тождества шире, чем оно доказуемо (pre-existing scope)
`sim.py:53–55` и комментарий `harness.py:410–411` заявляют `submits − fills − expired − window_cancelled == 0` без оговорки. Reduce-only заявки, снятые **посреди прогона** (`_cancel_reduced`, `_drop_resting_target`, drop при плоской позиции в `_apply_limits/_stops/_markets` через `qty is None → continue`), не входят ни в один член тождества — это и есть «silent write-off» из находки C, п.4 («не оставляет артефактного следа»). Фикс член `window_cancelled` добавил, но mid-run выпадения он не покрывает. НЕ регрессия данного фикса (до фикса они также выпадали из `pending`-заглушки), baseline-diff (40/40) этот кейс не задевает; сформулировать как известное ограничение / добавить оговорку в docstring при удобном случае.

### [Info] Pre-existing, не в диффе (для полноты)
- `tests/test_calib_harness.py:288` — mypy `func-returns-value` (`lambda … (calls.append(...), …)[1]`): воспроизводится на baseline `calibration-suite` (exit 1 там же); строка не в diff.
- `tests/test_calib_harness.py:282` — артефакт кодировки в комментарии («ДО任何 прогонов») — pre-existing.
- `_window_cancelled` накапливается между `run()` на одном брокере (reset нет) — по докстрингу задумано; харнесс создаёт брокера на сторону, счёт корректен.

### Ответы на вопросы ведущего
- **(в)** См. находку [Minor] opt-сторона: для pess-замеров достаточно; полная прозрачность обеих вилок потребовала бы второй учётной строки (опционально, не блокер).
- Baseline-diff не трогался, выводы из него не пересматривались.

## Гейты (шаг 3, worktree wt_fix2, PYTHONPATH=wt_fix2, python из .venv strategylab)

| Гейт | Результат | Exit |
|---|---|---|
| `pytest <wt>/tests -q` | **398 passed, 3 skipped** (база совпала: 398/3) | 0 |
| `pytest -k "window_close or engine or calib_harness"` (выборка фикса) | 36 passed | 0 |
| `ruff check <wt>` | All checks passed! | 0 |
| `mypy` — 5 файлов фикса (`sim.py`, `engine.py`, `harness.py`, `test_window_close.py`, `test_engine.py`) | Success: no issues found | 0 |
| `mypy` — все 6 затронутых файлов (включая `test_calib_harness.py`) | 1 ошибка `test_calib_harness.py:288` — **pre-existing**, воспроизведён на baseline calibration-suite (там же exit 1) | 1 |

Итог: все критерии design «Фикс 2» выполнены; гейты фикса зелёные; находки [Moderate]/[Minor] — follow-up на сопутствующие артефакты/документацию, не затрагивающие корректность этого изменения.
