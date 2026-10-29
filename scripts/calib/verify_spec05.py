"""Verify spec_05 (dca_grid): независимая машина лесенки по сырым часовым close БД.

Заморожен ДО прогона сетки по design §8 (V5.1–V5.5, поправки A4/A5/A6) и Task 8
брифу. Ноль импортов из `strategylab`: своё часовое агрегирование `ts // 3600`
(close — последняя минута группы), свои Decimal-константы design §3 и пины
spec_version (канонический JSON, §3) — посчитаны stdlib ДО прогона; рецепт
валидирован воспроизведением двух известных пинов spec_04.

Машина воспроизводит лесенку филл-в-филл (A5: геометрический якорь close_0,
уровни close_0·(1−s)^k по числу ПОДАННЫХ лотов): MARKET BUY подан на баре X —
исполнен на X+1 по `close·(1+slip)` тейкером; средник входа — инкрементальный
VWAP `entry=(entry·qty+p)/(qty+1)` ровно в порядке движка (Position.apply_fill),
иначе сверка 1E-20 не проходит; сведение — движок концом окна:
`realized=(x−entry)·Q − taker·Q·x − Σ fee_i` (engine._close_window).
Открытая позиция держится всё окно — self-гейта equity<=0 нет намеренно.

V5.2 сверяет артефакт аналитически: из единственной journal-строки (A4)
выводится q = pnl / ((exit − avg) − taker·(exit + avg)) и обязана совпасть с
предсказанием машины (число слоёв допуск 0, средник 1E-20 — совпадение средника
пиннит весь набор филлов). Pre-registered ожидание: t_neg (marked equity ≤ 0)
существует, и слои после t_neg подаются и исполняются (>0); отсутствие —
крупное «опровержение pre-registered ожидания» в вывод (любой исход = вывод
о модели, не авария).

V5.4: отрицательная equity проходит без abs/клиппинга; артефактный
max_drawdown сверяется с реализованной реконструкцией (одна сделка → |net|),
marked-дно машины репортится ОТДЕЛЬНЫМ числом (расхождение = finding «метрика
слепа к нереализованной просадке» — формулировка в отчёте).

Вывод — таблица `PASS/FAIL V5.x | ячейка`; ненулевой exit при любом FAIL;
крах парсинга ячейки — `FAIL fatal`, вторая нога продолжается.
"""

from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START_TS = 1761523200
END_TS = 1790294400
CAPITAL = Decimal("10000")
TAKER = Decimal("0.0006")
SLIPPAGE = Decimal("0.0005")
HOURS_FULL = 7992
QUANT = Decimal("1E-20")
ONE = Decimal("1")
STRATEGY = "dca_grid"
STEPS: tuple[str, ...] = ("0.02", "0.05")
LAYERS: tuple[int, ...] = (4, 8)
GRID: tuple[tuple[str, int], ...] = tuple((s, n) for s in STEPS for n in LAYERS)
SYMBOLS: tuple[tuple[str, Path], ...] = (
    ("BTCUSDT", ROOT / "data" / "btcusdt.db"),
    ("ETHUSDT", ROOT / "data" / "ethusdt.db"),
)
CALIB = ROOT / "data" / "calib"
REDO = CALIB / "redo"
BAD_TOKENS = re.compile(r"(NaN|inf|-inf|Infinity)")

# design §3 пины: канонический JSON StrategySpec {name: "calib:dca_grid", symbol,
# interval: 3600, start_ts, end_ts, params {"layers": "4", "step": "0.02"}
# (Decimal → normalize(MAX_PREC) → format "f" по spec.py:_canonical_decimal),
# rules_ref: "calibration-suite"} → sha256[:12]. Посчитаны stdlib ДО прогона;
# рецепт воспроизвёл пины spec_04 bbfa792d9950 и 30543054bb07 байт-в-байт.
EXPECTED_VERSIONS: dict[tuple[str, str, int], str] = {
    ("BTCUSDT", "0.02", 4): "01ab7d0ef170",
    ("BTCUSDT", "0.02", 8): "193f1cb2d49c",
    ("BTCUSDT", "0.05", 4): "a8b611dc8f9c",
    ("BTCUSDT", "0.05", 8): "0e9865156bd2",
    ("ETHUSDT", "0.02", 4): "f6316fbb1bfa",
    ("ETHUSDT", "0.02", 8): "26a7139a78b1",
    ("ETHUSDT", "0.05", 4): "98ca084b7223",
    ("ETHUSDT", "0.05", 8): "325944c53f8e",
}

METRIC_DECIMALS: tuple[str, ...] = (
    "win_rate",
    "net_pnl",
    "equity",
    "max_drawdown",
    "profit_factor",
    "sharpe",
)

_FAILURES = 0


def report(verdict: str, line: str) -> None:
    global _FAILURES
    print(f"{verdict} {line}")
    if verdict == "FAIL":
        _FAILURES += 1


@dataclass(slots=True)
class Ladder:
    """Итог реплея лесенки: филлы, предсказание артефакта и marked-серия."""

    fills: list[tuple[int, Decimal]] = field(default_factory=list)  # (бар, цена)
    submits: int = 0
    pending_at_end: int = 0
    t_neg: int | None = None
    min_marked: Decimal = CAPITAL
    layers_after_tneg: int = 0
    entry: Decimal = Decimal(0)  # инкрементальный VWAP (порядок движка)
    entry_fee: Decimal = Decimal(0)  # Σ taker·p_i
    qty: Decimal = Decimal(0)
    exit_price: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)


def hour_closes(db_path: Path, symbol: str) -> list[Decimal] | None:
    """Часовые close: полная группа = 60 минут шагом 60 с, без гэпов."""
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ts, close FROM bars_1m WHERE symbol = ? AND ts >= ? AND ts < ?"
            " ORDER BY ts",
            (symbol, START_TS, END_TS),
        ).fetchall()
    finally:
        conn.close()
    buckets: dict[int, list[tuple[int, Decimal]]] = {}
    for ts, close in rows:
        buckets.setdefault(int(ts) // 3600, []).append((int(ts), Decimal(close)))
    closes: list[Decimal] = []
    for hour in range(START_TS // 3600, END_TS // 3600):
        minutes = buckets.get(hour)
        if minutes is None or len(minutes) != 60:
            return None
        for offset, (ts, _) in enumerate(minutes):
            if ts != hour * 3600 + offset * 60:
                return None
        closes.append(minutes[-1][1])
    return closes if len(closes) == HOURS_FULL else None


def ladder(closes: list[Decimal], step: Decimal, layers: int) -> Ladder:
    """Реплей лесенки ровно по порядку операций ядра (apply_bar → on_bar)."""
    n = len(closes)
    m = Ladder()
    anchor = Decimal(0)
    pending_bar = -1  # бар подачи MARKET BUY, ждущий исполнения на следующем
    for i in range(n):
        if pending_bar >= 0 and pending_bar == i - 1:
            # Исполнение: close·(1+slip), тейкер; sim._make_fill fee=rate·qty·price.
            price = closes[i] * (ONE + SLIPPAGE)
            m.fills.append((i, price))
            m.qty += ONE
            if m.qty == ONE:
                m.entry = price
            else:
                m.entry = (m.entry * (m.qty - ONE) + price) / m.qty
            m.entry_fee += TAKER * price
            pending_bar = -1
        if i == 0:
            # Бар 0: первый MARKET BUY, якорь — его close (design §8, A5).
            anchor = closes[0]
            m.submits = 1
            pending_bar = 0
        elif (
            m.qty > 0
            and m.submits < layers + 1
            and closes[i] <= anchor * (ONE - step) ** m.submits
        ):
            m.submits += 1
            pending_bar = i
        marked = CAPITAL + (closes[i] - m.entry) * m.qty if m.qty > 0 else CAPITAL
        if marked < m.min_marked:
            m.min_marked = marked
        if m.t_neg is None and marked <= 0:
            m.t_neg = i
    m.pending_at_end = 1 if pending_bar == n - 1 else 0
    if m.t_neg is not None:
        m.layers_after_tneg = sum(1 for bar, _ in m.fills if bar > m.t_neg)
    # Сведение концом окна (engine._close_window), если позиция жива — всегда.
    m.exit_price = closes[n - 1] * (ONE - SLIPPAGE)
    m.realized = (
        (m.exit_price - m.entry) * m.qty * ONE
        - TAKER * m.qty * m.exit_price
        - m.entry_fee
    )
    return m


def derive_q(row: dict[str, str]) -> tuple[Decimal | None, str]:
    """q = pnl / ((exit − avg) − taker·(exit + avg)) из journal-строки (Task 8)."""
    entry = Decimal(row["entry_price"])
    exit_price = Decimal(row["exit_price"])
    pnl = Decimal(row["pnl"])
    denom = (exit_price - entry) - TAKER * (exit_price + entry)
    if denom == 0:
        return None, "нулевой знаменатель"
    with localcontext() as ctx:
        ctx.prec = 60
        return pnl / denom, ""


def parse_cells(
    csv_path: Path,
) -> tuple[list[str], dict[tuple[str, int], dict[str, dict[str, str]]]] | None:
    """(строки шапки, ячейка (step, layers) → вилка → колонки) или None.

    Ровно 4 ячейки × 2 вилки = 8 строк; группировка по колонкам параметров.
    """
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("fork,")), None)
    if header_index is None:
        return None
    table = list(csv.reader(lines[header_index:]))
    header = table[0]
    data_rows = [row for row in table[1:] if row]
    if len(data_rows) != 8:
        return None
    cells: dict[tuple[str, int], dict[str, dict[str, str]]] = {}
    for row in data_rows:
        if len(row) != len(header) or row[0] not in ("pessimistic", "optimistic"):
            return None
        parsed = dict(zip(header, row, strict=True))
        key = (parsed["step"], int(parsed["layers"]))
        side = cells.setdefault(key, {})
        if parsed["fork"] in side:
            return None
        side[parsed["fork"]] = parsed
    if set(cells) != set(GRID):
        return None
    if any(set(v) != {"pessimistic", "optimistic"} for v in cells.values()):
        return None
    return lines[:header_index], cells


def journal_by_version(path: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    with open(path, encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            grouped.setdefault(str(row["spec_version"]), []).append(row)
    return grouped


def combination_ok(row: dict[str, str], step: str, layers: int) -> bool:
    """R2/R5: combination парсим key=value, не позиционно."""
    try:
        pairs = dict(item.split("=", 1) for item in row["combination"].split("; "))
    except ValueError:
        return False
    return pairs == {"step": step, "layers": str(layers)}


def check_cell(
    symbol: str,
    step: str,
    layers: int,
    cell: dict[str, dict[str, str]],
    closes: list[Decimal],
    journal_p: dict[str, list[dict[str, str]]],
    journal_o: dict[str, list[dict[str, str]]],
) -> None:
    pess, opt = cell["pessimistic"], cell["optimistic"]
    version = EXPECTED_VERSIONS[(symbol, step, layers)]
    tag = f"{symbol} s={step} L={layers}"
    if pess["spec_version"] != version or opt["spec_version"] != version:
        csv_pair = f"{pess['spec_version']}/{opt['spec_version']}"
        report("FAIL", f"version {tag}: csv {csv_pair} != pin {version}")
        return
    rows_p = journal_p.get(version, [])
    rows_o = journal_o.get(version, [])
    if not all(combination_ok(row, step, layers) for row in rows_p + rows_o):
        report("FAIL", f"attribution {tag}: combination не соответствует ячейке")
        return

    # V5.1: строка есть (не ОТКЛОНЕНО — заодно отсутствие строки здесь же),
    # все Decimal-колонки парсятся.
    bad: list[str] = []
    for fork_name, row in (("pess", pess), ("opt", opt)):
        for name in METRIC_DECIMALS:
            if row[name] == "null":
                continue
            try:
                Decimal(row[name])
            except ArithmeticError:
                bad.append(f"{fork_name}.{name}={row[name]}")
        if row["win_rate"] != "null":
            win_rate = Decimal(row["win_rate"])
            if not Decimal("0") <= win_rate <= ONE:
                bad.append(f"{fork_name}.win_rate={row['win_rate']} вне [0,1]")
    if bad:
        report("FAIL", f"V5.1 {tag}: {bad[:6]}")
    else:
        report("PASS", f"V5.1 {tag}: строка есть, Decimal-колонки парсятся")

    m = ladder(closes, Decimal(step), layers)

    # V5.2: сверка машины с артефактом + pre-registered ожидание (A4/A5).
    if len(rows_p) != 1:
        report("FAIL", f"V5.2 {tag}: journal строк={len(rows_p)} != 1 (премиса A4)")
    else:
        row = rows_p[0]
        fails: list[str] = []
        if row["exit_reason"] not in ("market",):
            fails.append(f"exit_reason={row['exit_reason']} != market")
        if int(row["entry_ts"]) != START_TS + 3600 * m.fills[0][0]:
            fails.append(f"entry_ts={row['entry_ts']} != первый филл бара {m.fills[0][0]}")
        if int(row["exit_ts"]) != START_TS + 3600 * (len(closes) - 1):
            fails.append("exit_ts != последний бар окна")
        got_entry = Decimal(row["entry_price"])
        got_exit = Decimal(row["exit_price"])
        got_pnl = Decimal(row["pnl"])
        if abs(got_entry - m.entry) > QUANT:
            fails.append(f"avg got={got_entry} want={m.entry}")
        if abs(got_exit - m.exit_price) > QUANT:
            fails.append(f"exit got={got_exit} want={m.exit_price}")
        if abs(got_pnl - m.realized) > QUANT:
            fails.append(f"pnl got={got_pnl} want={m.realized}")
        q_d, why = derive_q(row)
        if q_d is None:
            fails.append(f"q не выводим: {why}")
        elif q_d < 0 or round(q_d) != m.qty or abs(q_d - m.qty) > Decimal("1E-9"):
            fails.append(f"выведенный q={q_d} != целое предсказание {m.qty}")
        pred_p = f"limit=0 other={m.pending_at_end}"
        got_p = f"limit={pess['pending_limit_buy']} other={pess['pending_other']}"
        got_o = f"limit={opt['pending_limit_buy']} other={opt['pending_other']}"
        if got_p != pred_p or got_o != pred_p:
            fails.append(f"pending: ожидалось {pred_p}, pess={got_p} opt={got_o}")
        if fails:
            report("FAIL", f"V5.2 {tag}: {fails[:4]}")
        else:
            report(
                "PASS",
                f"V5.2 {tag}: q={m.qty} филлов={len(m.fills)} подан={m.submits}"
                f" pending={m.pending_at_end} avg/exit/pnl точны",
            )
        if m.t_neg is None:
            report(
                "FAIL",
                f"V5.2-exp {tag}: ОПРОВЕРЖЕНИЕ pre-registered — marked-дно не достигнуто"
                f" (минимум={m.min_marked} > 0)",
            )
        elif m.layers_after_tneg == 0:
            report(
                "FAIL",
                f"V5.2-exp {tag}: ОПРОВЕРЖЕНИЕ — после t_neg=бар {m.t_neg} ни один слой"
                f" не исполнен (cap раньше дна или движок блокирует)",
            )
        else:
            report(
                "PASS",
                f"V5.2-exp {tag}: t_neg=бар {m.t_neg}, слоёв после дна исполнено"
                f" {m.layers_after_tneg} — self-гейта нет, модель торгует на минусе",
            )

    # V5.3: ни одного LiquidationError (нет ОТКЛОНЕНО-строк: 8/8 ячеек живые +
    # отклонено: 0 в шапке + скан), в journal нет exit_reason == "stop".
    stops = [r["exit_reason"] for r in rows_p + rows_o if r["exit_reason"] == "stop"]
    if stops:
        report("FAIL", f"V5.3 {tag}: journal содержит exit_reason=stop ×{len(stops)}")
    else:
        report(
            "PASS",
            f"V5.3 {tag}: liquidations=0 (все ячейки в csv), stop-выходов нет"
            f" (неблагоприятный ход < маржи на весь объём)",
        )

    # V5.4: знак без abs/клиппинга; mdd == реализованная реконструкция; marked-дно
    # отдельным числом.
    net = Decimal(pess["net_pnl"])
    equity = Decimal(pess["equity"])
    mdd = Decimal(pess["max_drawdown"])
    sharpe = Decimal(pess["sharpe"]) if pess["sharpe"] != "null" else None
    fails4: list[str] = []
    if abs(equity - (CAPITAL + net)) > QUANT:
        fails4.append(f"equity={equity} != capital+net={CAPITAL + net}")
    if net < 0:
        if abs(mdd - abs(net)) > QUANT:
            fails4.append(f"max_drawdown={mdd} != |net|={abs(net)}")
        if sharpe is not None and sharpe > 0:
            fails4.append(f"sharpe={sharpe} > 0 при net={net} < 0 — знак потерялся")
    elif mdd != 0:
        fails4.append(f"net>=0, но max_drawdown={mdd} != 0")
    if fails4:
        report("FAIL", f"V5.4 {tag}: {fails4[:3]}")
    else:
        report(
            "PASS",
            f"V5.4 {tag}: net={net} equity={equity} без клиппинга, mdd={mdd};"
            f" marked-дно={m.min_marked} (расхождение артефакт↔marked: находка отчёта)",
        )


def check_symbol(symbol: str, db_path: Path) -> None:
    stem = f"{STRATEGY}_{symbol}_{START_TS}_{END_TS}"
    if not db_path.exists():
        report("FAIL", f"data {symbol}: база {db_path.name} не найдена")
        return
    closes = hour_closes(db_path, symbol)
    if closes is None:
        report("FAIL", f"data {symbol}: не {HOURS_FULL} полных часовых групп без гэпов")
        return
    report("PASS", f"data {symbol}: групп={len(closes)} гэпов=0")

    csv_path = CALIB / f"{stem}.csv"
    jp_path = CALIB / f"{stem}_journal_p.csv"
    jo_path = CALIB / f"{stem}_journal_o.csv"
    rp_path = CALIB / f"{stem}_report_p.txt"
    if not (csv_path.exists() and jp_path.exists() and jo_path.exists() and rp_path.exists()):
        report("FAIL", f"artifacts {symbol}: сетка {stem} не найдена в data/calib")
        return

    # V5.1 (файловый проход): NaN/inf/-inf/Infinity нигде в четырёх артефактах.
    dirty = [
        path.name
        for path in (csv_path, jp_path, jo_path, rp_path)
        if BAD_TOKENS.search(path.read_text(encoding="utf-8"))
    ]
    if dirty:
        report("FAIL", f"V5.1 {symbol}: NaN/inf-токены в файлах {dirty}")
    else:
        report("PASS", f"V5.1 {symbol}: regex-скан csv+journal+report чист")

    parsed = parse_cells(csv_path)
    if parsed is None:
        report("FAIL", f"parse {symbol}: csv не читается как 4 ячейки × 2 вилки")
        return
    provenance, cells = parsed

    wanted_header = {
        f"strategy: {STRATEGY}",
        f"symbol: {symbol}",
        f"window: [{START_TS}, {END_TS})",
        f"capital: {CAPITAL}",
        "costs: taker_fee=0.0006 maker_fee=0.0002 slippage=0.0005 leverage=1",
        "zones_used: fit",
        "отклонено: 0",
        "touches: holdout=0, live=0",
    }
    missing = sorted(wanted_header - set(provenance))
    grid_pairs: dict[str, str] = {}
    for line in provenance:
        if line.startswith("grid: "):
            items = line.removeprefix("grid: ").split("; ")
            try:
                grid_pairs = dict(item.split("=", 1) for item in items)
            except ValueError:
                grid_pairs = {}
    if grid_pairs != {"step": "0.02,0.05", "layers": "4,8"}:
        missing.append(f"grid={grid_pairs or 'не разобрана'}")
    if not missing:
        report("PASS", f"header {symbol}: условия §3/§8, сетка, отклонено=0, touches=0/0")
    else:
        report("FAIL", f"header {symbol}: в шапке нет {missing}")

    journal_p = journal_by_version(jp_path)
    journal_o = journal_by_version(jo_path)
    known = {v for (sym, _s, _l), v in EXPECTED_VERSIONS.items() if sym == symbol}
    for grouped, leg in ((journal_p, "p"), (journal_o, "o")):
        foreign = sorted(set(grouped) - known)
        if foreign:
            report("FAIL", f"attribution {symbol} journal_{leg}: чужие spec_version {foreign}")

    for step, layers in GRID:
        check_cell(symbol, step, layers, cells[(step, layers)], closes, journal_p, journal_o)

    # V5.5: детерминизм — перепрогон в data/calib/redo побайтово равнен.
    if not REDO.exists():
        report("FAIL", f"V5.5 {symbol}: каталог redo не найден — перепрогон не сделан")
        return
    equal = 0
    for suffix in (".csv", "_journal_p.csv", "_journal_o.csv", "_report_p.txt"):
        a, b = CALIB / f"{stem}{suffix}", REDO / f"{stem}{suffix}"
        if a.exists() and b.exists():
            ha = hashlib.sha256(a.read_bytes()).hexdigest()
            hb = hashlib.sha256(b.read_bytes()).hexdigest()
            equal += 1 if ha == hb else 0
    if equal == 4:
        report("PASS", f"V5.5 {symbol}: 4/4 артефакта sha256 равны перепрогону redo/")
    else:
        report("FAIL", f"V5.5 {symbol}: совпало только {equal}/4 артефакта (или нет в redo/)")


def main() -> int:
    for symbol, db_path in SYMBOLS:
        try:
            check_symbol(symbol, db_path)
        except (
            ArithmeticError,
            LookupError,
            TypeError,
            ValueError,
            csv.Error,
            OSError,
            sqlite3.Error,
        ) as error:
            report("FAIL", f"fatal {symbol}: {type(error).__name__}: {error}")
    return 1 if _FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
