# H-0 — машина единственного прогона Series H

- Закон (неприкосновенен): `../../.superpowers/sdd/2026-11-03-global-audit/H0-freeze-criteria-DRAFT.md`.
- Прогон: `.venv\Scripts\python.exe -X utf8 scripts\h0\run.py --live` (из корня репо) — ровно один, после
  1798761600 (2027-01-01). До закрытия окна guard возвращает rc=3 и не читает базу.
- Smoke сегодня (синтетика, база не трогается): `.venv\Scripts\python.exe -X utf8 scripts\h0\run.py --smoke`. Двойной байт-identical
  прогон с sha256 — порядок публикации по §5 закона; отчёт печатается в stdout.
- rc: 0 = прогон выполнен (вердикт в отчёте) · 2 = покрытие окна хуже замороженного минимума ·
  3 = окно ещё открыто. Тесты: `tests/test_h0_run.py` (только синтетические бары).
- Данные в окно кормит feeder: `scripts/h0_feeder.py` (Task Scheduler `strategylab-h0-feeder`,
  daily 21:30). rc=2 у feeder = GAP-ALERT в окне — чинить до 2027-01-01.
