"""Verify spec_03 (sma_cross): независимый по-эпизодный реплей журнала из сырых баров.

Заморожен ДО прогона сетки по design §6 (V3.1–V3.4, поправка A1 long/flat) и Task 3
брифу. Ноль импортов из `strategylab`: свой deque-free SMA по срезу списка (порядок
сложения old→new, как `examples.py:101-103`), свой часовой bucketing `ts // 3600`,
свои Decimal-константы design §3 и пины spec_version (канонический JSON, §3).

Переходы считаем по правилу движка: кросс на баре i (i >= slow) по парам SMA с
бара slow-1; сигнал последнего бара НЕ исполняется — K считает кроссы только на
барах кроме последнего («ордер бара N живёт с N+1»).

Вывод — таблица `ячейка | K | trades(журнал) | V3.1..V3.4` по обеим ногам; ненулевой
exit при любом FAIL; крах парсинга ячейки — `FAIL fatal`, вторая нога продолжается.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from decimal import Decimal
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
STRATEGY = "sma_cross"
GRID: tuple[tuple[int, int], ...] = ((20, 100), (20, 200), (50, 100), (50, 200))
SYMBOLS: tuple[tuple[str, Path], ...] = (
    ("BTCUSDT", ROOT / "data" / "btcusdt.db"),
    ("ETHUSDT", ROOT / "data" / "ethusdt.db"),
)
CALIB = ROOT / "data" / "calib"

# design §3 пины: канонический JSON StrategySpec {name: "calib:sma_cross", symbol,
# interval: 3600, start_ts, end_ts, params {"fast": "20", "slow": "100", ...}
# (Decimal → normalize(MAX_PREC) → format "f" по spec.py:_canonical_decimal),
# rules_ref: "calibration-suite"} → sha256[:12]. Посчитаны stdlib ДО прогона.
EXPECTED_VERSIONS: dict[tuple[str, int, int], str] = {
    ("BTCUSDT", 20, 100): "1f3cca4a8279",
    ("BTCUSDT", 20, 200): "57b46a45f8f2",
    ("BTCUSDT", 50, 100): "0d348d2dee49",
    ("BTCUSDT", 50, 200): "fca5db698a7d",
    ("ETHUSDT", 20, 100): "6adae8847720",
    ("ETHUSDT", 20, 200): "ab4e0e39da92",
    ("ETHUSDT", 50, 100): "98c06fa271ad",
    ("ETHUSDT", 50, 200): "7ee56d2a8bb4",
}

_FAILURES = 0


def report(verdict: str, line: str) -> None:
    global _FAILURES
    print(f"{verdict} {line}")
    if verdict == "FAIL":
        _FAILURES += 1


def hour_closes(db_path: Path, symbol: str) -> list[Decimal] | None:
    """Часовые close из bars_1m: полная группа = 60 минут строгими 60-сек шагами."""
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


def sma_pairs(closes: list[Decimal], fast_n: int, slow_n: int) -> list[tuple[Decimal, Decimal]]:
    """Пары (fast SMA, slow SMA) с бара slow_n-1: срез old→new, сумма к Decimal(0).

    Moving-окно fast-дека в движке длиннее (maxlen=slow_n), но SMA берёт последние
    fast_n значений (examples.py:101-103) — срез от текущего бара эквивалентен.
    """
    pairs: list[tuple[Decimal, Decimal]] = []
    for i in range(slow_n - 1, len(closes)):
        fast_sum = sum(closes[i - fast_n + 1 : i + 1], Decimal("0")) / Decimal(fast_n)
        slow_sum = sum(closes[i - slow_n + 1 : i + 1], Decimal("0")) / Decimal(slow_n)
        pairs.append((fast_sum, slow_sum))
    return pairs


def cross_events(closes: list[Decimal], fast_n: int, slow_n: int) -> list[tuple[int, bool]]:
    """Кроссы (bar_index, golden) по правилу examples.py:51-54, БЕЗ последнего бара.

    Сравнение значений SMA соседних полных баров: переход `<= → >` = golden (BUY),
    `>= → <` = death (SELL); первый полный парный бар — slow_n.
    """
    pairs = sma_pairs(closes, fast_n, slow_n)
    events: list[tuple[int, bool]] = []
    for k in range(1, len(pairs)):
        bar = slow_n - 1 + k  # pairs[0] ↔ бар slow_n-1
        if bar >= len(closes) - 1:  # сигнал последнего бара не исполняется
            continue
        prev_fast, prev_slow = pairs[k - 1]
        fast, slow = pairs[k]
        if prev_fast <= prev_slow and fast > slow:
            events.append((bar, True))
        elif prev_fast >= prev_slow and fast < slow:
            events.append((bar, False))
    return events


def last_bar_cross(closes: list[Decimal], fast_n: int, slow_n: int) -> bool:
    """Есть ли кросс ровно на ПОСЛЕДНЕМ баре: его MARKET ляжет в очередь навсегда.

    Единственная живая проверка правила «ордер с последнего бара не исполняется»:
    pending_other обязан быть 1 тогда и только тогда.
    """
    pairs = sma_pairs(closes, fast_n, slow_n)
    if len(pairs) < 2:
        return False
    (prev_fast, prev_slow), (fast, slow) = pairs[-2], pairs[-1]
    return (prev_fast <= prev_slow and fast > slow) or (prev_fast >= prev_slow and fast < slow)


def expected_episodes(
    events: list[tuple[int, bool]], closes: list[Decimal]
) -> list[tuple[int, int, Decimal, Decimal, Decimal]]:
    """Эпизоды ceil(K/2): (entry_bar, exit_bar, entry, exit, pnl).

    Сторона всех эпизодов = сторона первого кросса (long/flat или short/flat,
    обнуление без флипа, position.py:65-72). Вход — close кросс-бара+1 с slip по
    направлению входа, выход — close кросса-обнуления+1 (висящий — последний бар
    окна), taker-комиссии с обоих концов, qty=1.
    """
    if not events:
        return []
    long_side = events[0][1]
    episodes: list[tuple[int, int, Decimal, Decimal, Decimal]] = []
    for m in range(0, len(events), 2):
        entry_bar = events[m][0] + 1
        exit_bar = events[m + 1][0] + 1 if m + 1 < len(events) else len(closes) - 1
        entry_mult = ONE + SLIPPAGE if long_side else ONE - SLIPPAGE
        exit_mult = ONE - SLIPPAGE if long_side else ONE + SLIPPAGE
        entry = closes[entry_bar] * entry_mult
        exit_price = closes[exit_bar] * exit_mult
        gross = exit_price - entry if long_side else entry - exit_price
        pnl = gross - TAKER * (entry + exit_price)
        episodes.append((entry_bar, exit_bar, entry, exit_price, pnl))
    return episodes


def parse_cells(
    csv_path: Path,
) -> tuple[list[str], dict[tuple[int, int], dict[str, dict[str, str]]]] | None:
    """(сырые строки шапки, ячейка (fast,slow) → вилка → колонки) или None.

    Ровно 4 ячейки × 2 вилки = 8 строк; группировка по (fast, slow) из колонок —
    порядок строк в csv не значим, дубль вилки внутри ячейки отбивает парсинг.
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
    cells: dict[tuple[int, int], dict[str, dict[str, str]]] = {}
    for row in data_rows:
        if len(row) != len(header) or row[0] not in ("pessimistic", "optimistic"):
            return None
        parsed = dict(zip(header, row, strict=True))
        key = (int(parsed["fast"]), int(parsed["slow"]))
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


def raw_lines_by_version(path: Path, version: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines[1:] if line.startswith(f"{version},")]


def combination_ok(row: dict[str, str], fast: int, slow: int) -> bool:
    """R2/R5: combination парсим key=value, не позиционно (разная детализация ячеек)."""
    try:
        pairs = dict(item.split("=", 1) for item in row["combination"].split("; "))
    except ValueError:
        return False
    return pairs == {"fast": str(fast), "slow": str(slow)}


def check_cell(
    symbol: str,
    fast: int,
    slow: int,
    cell: dict[str, dict[str, str]],
    closes: list[Decimal],
    journal_p: dict[str, list[dict[str, str]]],
    journal_o: dict[str, list[dict[str, str]]],
    jp_path: Path,
    jo_path: Path,
) -> None:
    pess, opt = cell["pessimistic"], cell["optimistic"]
    version = EXPECTED_VERSIONS[(symbol, fast, slow)]
    events = cross_events(closes, fast, slow)
    episodes = expected_episodes(events, closes)
    rows_p = journal_p.get(version, [])
    tag = f"{symbol} fast={fast} slow={slow}"

    if pess["spec_version"] != version or opt["spec_version"] != version:
        csv_pair = f"{pess['spec_version']}/{opt['spec_version']}"
        report("FAIL", f"version {tag}: csv {csv_pair} != pin {version}")
        return
    rows_o = journal_o.get(version, [])
    if not all(combination_ok(row, fast, slow) for row in rows_p + rows_o):
        report("FAIL", f"attribution {tag}: combination не соответствует ячейке")
        return

    # V3.1: строк журнала == ceil(K/2), допуск 0; K=0 ⟹ 0 строк и net=0
    expected_rows = (len(events) + 1) // 2
    net_p = Decimal(pess["net_pnl"])
    v31 = len(rows_p) == expected_rows and (
        expected_rows != 0 or (net_p == 0 and Decimal(pess["equity"]) == CAPITAL)
    )
    counts = f"K={len(events)} ceil={expected_rows} journal={len(rows_p)}"
    trades_ok = pess["trades"] == opt["trades"] == str(len(rows_p))
    if v31 and trades_ok:
        report("PASS", f"V3.1 {tag}: {counts}, trades-колонки обеих вилок равны строкам")
    else:
        report("FAIL", f"V3.1 {tag}: {counts} net={net_p} trades={pess['trades']}/{opt['trades']}")

    # V3.2: только market-филлы переворота/свода окна; пустой журнал при K>0 — не PASS
    reasons = {row["exit_reason"] for row in rows_p}
    if reasons - {"market"}:
        report("FAIL", f"V3.2 {tag}: чужие причины {sorted(reasons - {'market'})}")
    elif rows_p or not events:
        report("PASS", f"V3.2 {tag}: exit_reason только market ({len(rows_p)} строк)")
    else:
        report("FAIL", f"V3.2 {tag}: K={len(events)}, журнал пуст — сверять нечего")

    # pending: MARKET с последнего бара (исключён из K) — единственная живая проверка
    # правила «сигнал последнего бара не исполняется»; limit-ордеров в классе нет
    want_pending = str(1 if last_bar_cross(closes, fast, slow) else 0)
    if pess["pending_limit_buy"] == "0" and pess["pending_other"] == want_pending:
        report("PASS", f"pending {tag}: limit=0 other={want_pending}")
    else:
        got_pending = f"{pess['pending_limit_buy']}/{pess['pending_other']}"
        report("FAIL", f"pending {tag}: limit/other={got_pending}, ожидался other={want_pending}")

    # V3.3: по-эпизодный реплей цифра-в-цифру + сумма pnl == net из CSV
    bad: list[str] = []
    if len(rows_p) != len(episodes):
        bad.append(f"rows={len(rows_p)} != episodes={len(episodes)}")
    else:
        total = Decimal("0")
        for index, (row, episode) in enumerate(zip(rows_p, episodes, strict=True)):
            e_bar, x_bar, entry, exit_price, pnl = episode
            if int(row["entry_ts"]) != START_TS + 3600 * e_bar:
                bad.append(f"ep{index}.entry_ts={row['entry_ts']}")
            if int(row["exit_ts"]) != START_TS + 3600 * x_bar:
                bad.append(f"ep{index}.exit_ts={row['exit_ts']}")
            for field_name, field_got, field_want in (
                ("entry", Decimal(row["entry_price"]), entry),
                ("exit", Decimal(row["exit_price"]), exit_price),
                ("pnl", Decimal(row["pnl"]), pnl),
            ):
                if abs(field_got - field_want) > QUANT:
                    bad.append(f"ep{index}.{field_name} got={field_got} want={field_want}")
            total += Decimal(row["pnl"])
        if abs(total - net_p) > QUANT:
            bad.append(f"sum(pnl)={total} != net_pnl={net_p}")
        if abs(Decimal(pess["equity"]) - (CAPITAL + net_p)) > QUANT:
            bad.append(f"equity={pess['equity']} != 10000+{net_p}")
    if not bad:
        report("PASS", f"V3.3 {tag}: эпизодов={len(episodes)} реплей точен, net={net_p}")
    else:
        report("FAIL", f"V3.3 {tag}: {bad[:6]}")

    # V3.4: вилки побайтово (журнал) и всеми колонками метрик (csv без колонки fork)
    raw_p = raw_lines_by_version(jp_path, version)
    raw_o = raw_lines_by_version(jo_path, version)
    forkless_p = {key: value for key, value in pess.items() if key != "fork"}
    forkless_o = {key: value for key, value in opt.items() if key != "fork"}
    whole_eq = jp_path.read_bytes() == jo_path.read_bytes()
    if raw_p == raw_o and whole_eq and forkless_p == forkless_o and rows_p == rows_o:
        report("PASS", f"V3.4 {tag}: журналы побайтово (весь файл), метрики всеми колонками равны")
    else:
        report(
            "FAIL",
            f"V3.4 {tag}: raw_eq={raw_p == raw_o} whole_eq={whole_eq}"
            f" metrics_eq={forkless_p == forkless_o} parsed_eq={rows_p == rows_o}",
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
    if not (csv_path.exists() and jp_path.exists() and jo_path.exists()):
        report("FAIL", f"artifacts {symbol}: сетка {stem} не найдена в data/calib")
        return

    parsed = parse_cells(csv_path)
    if parsed is None:
        report("FAIL", f"parse {symbol}: csv не читается как 4 ячейки × 2 вилки")
        return
    provenance, cells = parsed

    wanted_header = {
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
    if grid_pairs != {"fast": "20,50", "slow": "100,200"}:
        missing.append(f"grid={grid_pairs or 'не разобрана'}")
    if not missing:
        report("PASS", f"header {symbol}: условия §3, сетка, отклонено=0, touches=0/0")
    else:
        report("FAIL", f"header {symbol}: в шапке нет {missing}")

    journal_p = journal_by_version(jp_path)
    journal_o = journal_by_version(jo_path)
    known = {version for (sym, _f, _s), version in EXPECTED_VERSIONS.items() if sym == symbol}
    for grouped, leg in ((journal_p, "p"), (journal_o, "o")):
        foreign = sorted(set(grouped) - known)
        if foreign:
            report("FAIL", f"attribution {symbol} journal_{leg}: чужие spec_version {foreign}")

    for fast, slow in GRID:
        cell = cells[(fast, slow)]
        check_cell(symbol, fast, slow, cell, closes, journal_p, journal_o, jp_path, jo_path)


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
