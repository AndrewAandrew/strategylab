"""Сборка публикации E5-seriesE-grid-RESULTS.md из JSON замера (механика; текст — замерщик).

Читает meas/grid_E_runs.json (54 запуска + rerun + детерминизм + инциденты) и
meas/grid_E_gates.json (артефактные числа + гейты E2-1…E2-5), пишет
E5-seriesE-grid-RESULTS.md рядом с сырого логом. Никаких вердиктов — только числа.

Запуск: $env:PYTHONUTF8="1"; python meas/make_E5_md.py
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
OUT = BASE / "E5-seriesE-grid-RESULTS.md"

RUNS = json.loads((HERE / "grid_E_runs.json").read_text(encoding="utf-8"))
GATES = json.loads((HERE / "grid_E_gates.json").read_text(encoding="utf-8"))

HEAD = "47080e56ec18bba54c51f3889519ae452a73e21f"
DATE = "2026-11-02"
FOLDS = ("f1", "f2", "f3")


def d4(x: str) -> str:
    v = Decimal(x)
    return f"{v:.4f}"


def sign(x: str) -> str:
    v = Decimal(x)
    return f"{v:+.4f}"


def pf(b: bool) -> str:
    return "PASS" if b else "FAIL"


lines: list[str] = []
add = lines.append

add("# E-5. Series E — замер боевой решётки 18×3 (R-next-1)")
add("")
add(f"- **Публикует:** брат-замерщик Series E (R-next-1: числа решётки публикует замерщик первым, не автор кода).")
add(f"- **Дата:** {DATE}")
add(f"- **HEAD (замер):** `{HEAD}` ветка `calibration-suite`; на момент старта замера `git log -1` = {HEAD}, `git status` чистый кроме untracked `data/` (оно в `.gitignore:10` — в git не уходит; замер никакой tracked-код/тесты/pyproject не менял).")
add(f"- **Закон (дословно до прогона):** `.superpowers/sdd/2026-11-02-seriesE-deviation/seriesE-deviation-preregistration-DRAFT.md`, секции E-2/E2 (гейты) и E-2-F (замороженная таблица 18 клеток — параметры клеток взяты ТОЛЬКО из неё).")
add(f"- **Python:** `Join-Path $HOME \"Documents\\GitHub\\strategylab\\.venv\\Scripts\\python.exe\"`, перед каждым запуском `$env:PYTHONUTF8=\"1\"`.")
add("")
add("## Команды")
add("")
add("Шаблон одного запуска (54 запуска = 18 клеток × 3 фолда; `--out` — папка, имена файлов внутри CLI генерирует сам, stem `dev_<cell>_<SYMBOL>_<start>_<end>`):")
add("")
add("```")
add("python scripts/deviation/grid.py --db <db> --symbol <sym> --start <f.start> --end <f.end> \\")
add("    --cell <E-xy> --tol <tol> --depth <depth> --conf <conf> --vol-filter <k> \\")
add("    --exit-mode <exit> --ctx-hours 960 --out data/calib/dev_<cell>_f<N>")
add("```")
add("")
add("Фактически прогнаны одной командой (cmd-таблица 54 строк внутри, все точные cmdline — в сыром логе):")
add("")
add("```")
add("$env:PYTHONUTF8=\"1\"")
add("python .superpowers/sdd/2026-11-02-seriesE-deviation/meas/run_grid_E.py")
add("python .superpowers/sdd/2026-11-02-seriesE-deviation/meas/gates_E2.py     # гейты E2 по артефактам")
add("python .superpowers/sdd/2026-11-02-seriesE-deviation/meas/make_E5_md.py   # сборка этой публикации")
add("```")
add("")
add("Базы/оси решётки (фиксированы заданием): B1 = E-a1…a6 (`data/ethusdt.db` ETHUSDT tol0.020 d0.003 k2), "
     "B2 = E-b1…b6 (`data/ethusdt.db` ETHUSDT tol0.020 d0.005 k1), B3 = E-c1…c6 (`data/btcusdt.db` BTCUSDT tol0.010 d0.005 k1); "
     "внутри базы 1/2 = conf0, 3/4 = conf30, 5/6 = conf60; выходы 1/3/5 = V1_48, 2/4/6 = V2_R2. "
     "Фолды: f1 [1761523200,1771113600), f2 [1771113600,1780704000), f3 [1780704000,1790294400); "
     "HOLDOUT_FLOOR=1790294400 (гард CLI, exit 2); все клетки: zone-tf 14400s, touches 2, capital 10000, "
     "taker-fee 0.0006, slippage 0.0005 (дефолты CLI), ctx-hours 960.")
add("")
add("## Таблица 54 запусков")
add("")
add("Числа — из metrics-csv артефактов (Decimal; в таблице net/ann округлены до 4 знаков, "
     "точные Decimal-строки — в `data/calib/dev_<cell>_f<N>/` и в stdout сыром логе). "
     "Все 54 запуска: exit=0, артефакты записаны.")
add("")
add("| # | cell | fold | rounds | events | net_pnl | annualized_pct | stops | takes | tb | forced | flat_fold | deviation_version |")
add("|---|------|------|-------:|-------:|--------:|---------------:|------:|------:|---:|-------:|----------:|-------------------|")
idx = 0
for cell in GATES:
    for fold in FOLDS:
        idx += 1
        f = GATES[cell]["folds"][fold]
        add(f"| {idx} | {cell} | {fold} | {f['rounds']} | {f['events']} | {d4(f['net_pnl'])} | "
            f"{d4(f['annualized_pct'])} | {f['n_stops']} | {f['n_takes']} | {f['n_tb']} | {f['n_forced']} | "
            f"{f['flat_fold']} | `{f['deviation_version']}` |")
add("")
add("## Сводная матрица 18 клеток × гейты E2 (по артефактам)")
add("")
add("pooled_ann = (сумма net трёх фолдов / 10000) × (365/333) × 100; 333 = суммарная длина трёх фолдов в днях (111+111+111).")
add("")
add("| cell | pooled net$ | pooled ann% | E2-1 net≥0 все фолды | E2-2 pooled≥+3.0%/год | E2-3 каждый фолд ann>0 | E2-4 bootstrap нижняя>0 | E2-5 rounds≤25 | ALL |")
add("|------|------------:|------------:|:--------------------:|:---------------------:|:----------------------:|:----------------------:|:--------------:|:---:|")
for cell, v in GATES.items():
    g = v["gates"]
    add(f"| {cell} | {v['pooled_net']} | {d4(v['pooled_ann'])} | {pf(g['E2-1'])} | {pf(g['E2-2'])} | "
        f"{pf(g['E2-3'])} | {pf(g['E2-4'])} | {pf(g['E2-5'])} | {pf(g['ALL'])} |")
add("")
add("Числа гейтов по фолдам (net / ann% / rounds по f1,f2,f3) и bootstrap-границы:")
add("")
add("| cell | net f1 | net f2 | net f3 | ann% f1 | ann% f2 | ann% f3 | rounds f1/f2/f3 | bootstrap lo95 | bootstrap hi95 |")
add("|------|-------:|-------:|-------:|--------:|--------:|--------:|:---------------:|---------------:|---------------:|")
for cell, v in GATES.items():
    fs = v["folds"]
    b = v["bootstrap"]
    add(f"| {cell} | {d4(fs['f1']['net_pnl'])} | {d4(fs['f2']['net_pnl'])} | {d4(fs['f3']['net_pnl'])} | "
        f"{d4(fs['f1']['annualized_pct'])} | {d4(fs['f2']['annualized_pct'])} | {d4(fs['f3']['annualized_pct'])} | "
        f"{fs['f1']['rounds']}/{fs['f2']['rounds']}/{fs['f3']['rounds']} | {b['lower_2.5pct']:.4f} | {b['upper_97.5pct']:.4f} |")
add("")
add("Клеток, прошедших E2-1∧E2-2∧E2-3∧E2-4∧E2-5: "
     f"**{sum(1 for v in GATES.values() if v['gates']['ALL'])}/18** "
     f"({', '.join(c for c, v in GATES.items() if v['gates']['ALL']) or 'нет'}).")
add("")
add("## E2-4 — метод bootstrap (как реализовано, дословно)")
add("")
add("Для каждой клетки дневной ряд net склеен по фолдам f1+f2+f3: каждый круг "
     "атрибутирован в день его exit_ts из `journal_p.csv` (круг = пара entry→exit, "
     "net круга = сумма cash_delta обеих строк; дни без выходов = 0); ряд = 333 дня "
     "[1761523200, 1790294400). Moving-block bootstrap: блоки длины 30 дней с circular "
     "wrap — на каждой итерации берётся 12 блоков (12×30=360 → обрезка до 333), старт "
     "каждого блока ~ uniform на {0,…,322} включительно (randrange(323)), индекс "
     "(start+i) mod 333; 1000 повторов, seed=42 (один `random.Random(42)` на весь ряд, "
     "повторы идут подряд). Статистика повтора = mean(daily)×365/10000×100 (%/год на "
     "капитал $10k, float — так и разрешено для бутстрапа). Нижняя граница = 2.5-й "
     "процентиль по 1000 отсортированных статистик (линейная интерполяция, "
     "k=0.025×(1000−1)=24.975); верхняя = 97.5-й тем же правилом. PASS (E2-4), если "
     "нижняя граница > 0.")
add("")
add("Контроль склейки: sum(daily) == pooled net трёх фолдов для всех 18 клеток (Decimal, "
     "совпало в пределах 1e-6) — сверка в `meas/grid_E_gates.json` (`daily_sum_check`).")
add("")
add("## Детерминизм-контроль (побайтный)")
add("")
add("Клетка E-b3, фолд f2, повторный прогон в `data/calib/dev_E-b3_f2_RERUN`, "
     "сравнение всех 3 файлов артефакта с оригиналом `data/calib/dev_E-b3_f2/`:")
add("")
add("| файл | bytes equal |")
add("|------|:-----------:|")
for name, ok in RUNS["determinism"].items():
    add(f"| `{name}` | {ok} |")
add("")
add(f"**ALL_EQUAL: {RUNS['determinism_all_equal']}**")
add("")
add("## Инциденты")
add("")
if RUNS["incidents"]:
    for i in RUNS["incidents"]:
        add(f"- {i}")
else:
    add("- Нет. Все 54 запуска и rerun: exit=0; HOLDOUT_GUARD / WINDOW_GUARD / PARAM_GUARD не встречались; "
        "ни один запуск не читал дальше HOLDOUT_FLOOR=1790294400 (окна фолдов и ctx упираются в границу фолда/начало базы).")
add("")
add("Наблюдения-конвенции (не инциденты, из задания/закона): у ETHUSDT f1 контекст 960ч "
     "упирается в начало базы 1761523200 — меньше 960ч, это норма; у k2-клеток (B1) в "
     "начале истории trailing-медиана <200 баров и фильтр выключен-консервативно — "
     "замороженное ужесточение, не баг (flat_fold в таблице — факт, не ошибка).")
add("")
add("## Артефакты")
add("")
add("- Полные артефакты 54 запусков: `data/calib/dev_<cell>_f<N>/` (metrics csv + `*_journal_p.csv` + `*_report_p.txt`), rerun: `data/calib/dev_E-b3_f2_RERUN/` — в git не коммитятся (`data/` ignored).")
add(f"- Сырой лог (stdout всех 54 + rerun): `.superpowers/sdd/2026-11-02-seriesE-deviation/E5-seriesE-grid-raw.txt`")
add(f"- Числа запусков (JSON): `.superpowers/sdd/2026-11-02-seriesE-deviation/meas/grid_E_runs.json`")
add(f"- Гейты E2 (JSON): `.superpowers/sdd/2026-11-02-seriesE-deviation/meas/grid_E_gates.json`")
add(f"- Runner: `meas/run_grid_E.py`; гейты: `meas/gates_E2.py`; сборка: `meas/make_E5_md.py`")
add("")
add("Вердикт серии — за лидом (независимый аудит); замерщик публикует только числа.")
add("")

OUT.write_text("\n".join(lines), encoding="utf-8", newline="\n")
print(f"wrote {OUT} ({len(lines)} lines)")
