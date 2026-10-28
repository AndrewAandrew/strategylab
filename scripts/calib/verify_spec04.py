"""Verify spec_04 (limit_pullback): независимая машина волн по сырым часам БД.

Заморожен ДО прогона сетки по design §7 (V4.1–V4.4, поправки A2/A3) и Task 5
брифу. Ноль импортов из `strategylab`: своё часовое агрегирование `ts // 3600`
(close — последний час, low — минимум минут), свои Decimal-константы design §3
и пины spec_version (канонический JSON, §3) — посчитаны stdlib ДО прогона.

Машина воспроизводит дисциплину волн из текста дизайна: на баре i, где флэт и
`i ≡ 0 (mod e)`, подаётся LIMIT BUY `close·(1−d)`; живёт бары i+1…i+e (заявка с
бара N активна с N+1 — подтверждено кодом движка до прогона); на входе apply_bar
i+e+1 молча списывается. Исполнение — первый бар с `low ≤ limit` по цене лимита
(maker). Позиция живёт H баров (выход-сигнал на entry_bar+H, MARKET исполняется
следующим баром по `close·(1−slip)` тейкер); открытая позиция в конце окна
сводится движком на последнем баре — это НЕ филл брокера (висящее window-close).
Перекрытие волн (A3): на баре границы в очереди транзенторно две лимитки —
списание старой происходит на входе следующего apply_bar; на каждый бар
исполнима ровно одна, машина моделирует очереди списком.

Вывод — таблица `PASS/FAIL V4.x | ячейка` по обеим ногам; ненулевой exit при
любом FAIL; крах парсинга ячейки — `FAIL fatal`, вторая нога продолжается.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START_TS = 1761523200
END_TS = 1790294400
CAPITAL = Decimal("10000")
MAKER = Decimal("0.0002")
TAKER = Decimal("0.0006")
SLIPPAGE = Decimal("0.0005")
HOURS_FULL = 7992
QUANT = Decimal("1E-20")
ONE = Decimal("1")
STRATEGY = "limit_pullback"
DEPTHS: tuple[str, ...] = ("0.003", "0.005")
TTLS: tuple[int, ...] = (12, 24, 48)
HOLDS: tuple[int, ...] = (24, 48)
GRID: tuple[tuple[str, int, int], ...] = tuple(
    (d, e, h) for d in DEPTHS for e in TTLS for h in HOLDS
)
SYMBOLS: tuple[tuple[str, Path], ...] = (
    ("BTCUSDT", ROOT / "data" / "btcusdt.db"),
    ("ETHUSDT", ROOT / "data" / "ethusdt.db"),
)
CALIB = ROOT / "data" / "calib"

# design §3 пины: канонический JSON StrategySpec {name: "calib:limit_pullback",
# symbol, interval: 3600, start_ts, end_ts, params {"depth": "0.003",
# "hold_bars": "24", "ttl_bars": "12"} (Decimal → normalize(MAX_PREC) → format
# "f" по spec.py:_canonical_decimal), rules_ref: "calibration-suite"} →
# sha256[:12]. Посчитаны stdlib ДО прогона.
EXPECTED_VERSIONS: dict[tuple[str, str, int, int], str] = {
    ("BTCUSDT", "0.003", 12, 24): "bbfa792d9950",
    ("BTCUSDT", "0.003", 12, 48): "8f51f08fe9a8",
    ("BTCUSDT", "0.003", 24, 24): "355d2396e400",
    ("BTCUSDT", "0.003", 24, 48): "9a3a5bee4b37",
    ("BTCUSDT", "0.003", 48, 24): "8e454bea9ff8",
    ("BTCUSDT", "0.003", 48, 48): "bba0a7f91c0d",
    ("BTCUSDT", "0.005", 12, 24): "ef832b487d22",
    ("BTCUSDT", "0.005", 12, 48): "86c7b6e5caac",
    ("BTCUSDT", "0.005", 24, 24): "ce631ca24374",
    ("BTCUSDT", "0.005", 24, 48): "f22272a297a8",
    ("BTCUSDT", "0.005", 48, 24): "2ea53ae665f4",
    ("BTCUSDT", "0.005", 48, 48): "8692abf3dc10",
    ("ETHUSDT", "0.003", 12, 24): "148ce7d4838f",
    ("ETHUSDT", "0.003", 12, 48): "e1fc4f69ff12",
    ("ETHUSDT", "0.003", 24, 24): "99d1a86b1934",
    ("ETHUSDT", "0.003", 24, 48): "ff553b002622",
    ("ETHUSDT", "0.003", 48, 24): "20bf46d4fa48",
    ("ETHUSDT", "0.003", 48, 48): "f928b70ff955",
    ("ETHUSDT", "0.005", 12, 24): "d7fbd5a99c90",
    ("ETHUSDT", "0.005", 12, 48): "6db36cffb6ba",
    ("ETHUSDT", "0.005", 24, 24): "c4b60340240a",
    ("ETHUSDT", "0.005", 24, 48): "30543054bb07",
    ("ETHUSDT", "0.005", 48, 24): "02f4d2eb931e",
    ("ETHUSDT", "0.005", 48, 48): "065480a8aa9c",
}

_FAILURES = 0


def report(verdict: str, line: str) -> None:
    global _FAILURES
    print(f"{verdict} {line}")
    if verdict == "FAIL":
        _FAILURES += 1


@dataclass(frozen=True, slots=True)
class Episode:
    """Одна закрытая сделка: бары входа/выхода и цены филлов."""

    entry_bar: int
    exit_bar: int
    entry_price: Decimal
    exit_price: Decimal


@dataclass(slots=True)
class Machine:
    episodes: list[Episode]
    submits: int = 0
    fills: int = 0
    expired: int = 0
    pending_limit: int = 0
    pending_other: int = 0
    hold_exits: int = 0
    window_closes: int = 0


def hour_bars(db_path: Path, symbol: str) -> tuple[list[Decimal], list[Decimal]] | None:
    """Параллельные списки часовых close и low: полная группа = 60 минут шагом 60 с."""
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ts, low, close FROM bars_1m WHERE symbol = ? AND ts >= ? AND ts < ?"
            " ORDER BY ts",
            (symbol, START_TS, END_TS),
        ).fetchall()
    finally:
        conn.close()
    buckets: dict[int, list[tuple[int, Decimal, Decimal]]] = {}
    for ts, low, close in rows:
        buckets.setdefault(int(ts) // 3600, []).append(
            (int(ts), Decimal(low), Decimal(close))
        )
    closes: list[Decimal] = []
    lows: list[Decimal] = []
    for hour in range(START_TS // 3600, END_TS // 3600):
        minutes = buckets.get(hour)
        if minutes is None or len(minutes) != 60:
            return None
        for offset, (ts, _, _) in enumerate(minutes):
            if ts != hour * 3600 + offset * 60:
                return None
        closes.append(minutes[-1][2])
        lows.append(min(value[1] for value in minutes))
    return (closes, lows) if len(closes) == HOURS_FULL else None


def machine(
    closes: list[Decimal], lows: list[Decimal], depth: Decimal, e: int, hold: int
) -> Machine:
    """Машина состояний ровно по правилам движка (apply_bar → on_bar → submit)."""
    n_bars = len(closes)
    m = Machine(episodes=[])
    resting: list[tuple[int, Decimal]] = []  # (бар подачи, цена лимита)
    open_pos = False
    entry_bar = -1
    entry_price = Decimal(0)
    signal_bar = -1  # бар, с которого MARKET выход ждёт исполнения следующим баром
    for i in range(n_bars):
        # Вход apply_bar(i): молчаливое списание истёкших (бар i >= w + e + 1).
        kept: list[tuple[int, Decimal]] = []
        for w, limit in resting:
            if i >= w + e + 1:
                m.expired += 1
            else:
                kept.append((w, limit))
        resting = kept
        if open_pos:
            if signal_bar == i - 1:
                # MARKET выход исполняется на следующем баре: close·(1−slip), taker.
                exit_price = closes[i] * (ONE - SLIPPAGE)
                m.episodes.append(Episode(entry_bar, i, entry_price, exit_price))
                m.hold_exits += 1
                open_pos = False
                signal_bar = -1
                if i % e == 0:
                    m.submits += 1
                    resting.append((i, closes[i] * (ONE - depth)))
        else:
            filled_index: int | None = None
            for index, (w, limit) in enumerate(resting):
                if w + 1 <= i <= w + e and lows[i] <= limit:
                    filled_index = index
                    break
            if filled_index is not None:
                _, limit = resting.pop(filled_index)
                m.fills += 1
                open_pos = True
                entry_bar = i
                entry_price = limit
            if not open_pos and i % e == 0:
                m.submits += 1
                resting.append((i, closes[i] * (ONE - depth)))
        if open_pos and signal_bar < 0 and i - entry_bar >= hold:
            signal_bar = i
    m.pending_limit = len(resting)
    if open_pos:
        if signal_bar == n_bars - 1:
            m.pending_other = 1
        m.window_closes = 1
        exit_price = closes[n_bars - 1] * (ONE - SLIPPAGE)
        m.episodes.append(Episode(entry_bar, n_bars - 1, entry_price, exit_price))
    return m


def pnl_maker_entry(ep: Episode) -> Decimal:
    """V4.1: maker на входе, taker на выходе, qty=1."""
    return (
        ep.exit_price
        - ep.entry_price
        - MAKER * ep.entry_price
        - TAKER * ep.exit_price
    )


def pnl_taker_entry(ep: Episode) -> Decimal:
    """Анти-формула: taker на обоих ногах — обязана РАСХОДИТЬСЯ с журналом."""
    return (
        ep.exit_price
        - ep.entry_price
        - TAKER * ep.entry_price
        - TAKER * ep.exit_price
    )


def parse_cells(
    csv_path: Path,
) -> tuple[list[str], dict[tuple[str, int, int], dict[str, dict[str, str]]]] | None:
    """(строки шапки, ячейка (depth, ttl, hold) → вилка → колонки) или None.

    Ровно 12 ячеек × 2 вилки = 24 строки; группировка по колонкам параметров.
    """
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("fork,")), None)
    if header_index is None:
        return None
    table = list(csv.reader(lines[header_index:]))
    header = table[0]
    data_rows = [row for row in table[1:] if row]
    if len(data_rows) != 24:
        return None
    cells: dict[tuple[str, int, int], dict[str, dict[str, str]]] = {}
    for row in data_rows:
        if len(row) != len(header) or row[0] not in ("pessimistic", "optimistic"):
            return None
        parsed = dict(zip(header, row, strict=True))
        key = (parsed["depth"], int(parsed["ttl_bars"]), int(parsed["hold_bars"]))
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


def combination_ok(row: dict[str, str], depth: str, e: int, hold: int) -> bool:
    """R2/R5: combination парсим key=value, не позиционно."""
    try:
        pairs = dict(item.split("=", 1) for item in row["combination"].split("; "))
    except ValueError:
        return False
    return pairs == {"depth": depth, "ttl_bars": str(e), "hold_bars": str(hold)}


def check_cell(
    symbol: str,
    depth: str,
    e: int,
    hold: int,
    cell: dict[str, dict[str, str]],
    closes: list[Decimal],
    lows: list[Decimal],
    journal_p: dict[str, list[dict[str, str]]],
    journal_o: dict[str, list[dict[str, str]]],
    jp_path: Path,
    jo_path: Path,
) -> None:
    pess, opt = cell["pessimistic"], cell["optimistic"]
    version = EXPECTED_VERSIONS[(symbol, depth, e, hold)]
    tag = f"{symbol} d={depth} e={e} H={hold}"
    if pess["spec_version"] != version or opt["spec_version"] != version:
        csv_pair = f"{pess['spec_version']}/{opt['spec_version']}"
        report("FAIL", f"version {tag}: csv {csv_pair} != pin {version}")
        return
    rows_p = journal_p.get(version, [])
    rows_o = journal_o.get(version, [])
    if not all(combination_ok(row, depth, e, hold) for row in rows_p + rows_o):
        report("FAIL", f"attribution {tag}: combination не соответствует ячейке")
        return

    m = machine(closes, lows, Decimal(depth), e, hold)

    # V4.1: построчный реплей — entry обязан быть ценой лимита (maker, без slip),
    # pnl == (exit − entry) − maker·entry − taker·exit; анти-формула не совпадает.
    bad: list[str] = []
    if len(rows_p) != len(m.episodes):
        bad.append(f"rows={len(rows_p)} != episodes={len(m.episodes)}")
    else:
        for index, (row, ep) in enumerate(zip(rows_p, m.episodes, strict=True)):
            if int(row["entry_ts"]) != START_TS + 3600 * ep.entry_bar:
                bad.append(f"ep{index}.entry_ts={row['entry_ts']}")
            if int(row["exit_ts"]) != START_TS + 3600 * ep.exit_bar:
                bad.append(f"ep{index}.exit_ts={row['exit_ts']}")
            for name, got, want in (
                ("entry", Decimal(row["entry_price"]), ep.entry_price),
                ("exit", Decimal(row["exit_price"]), ep.exit_price),
                ("pnl", Decimal(row["pnl"]), pnl_maker_entry(ep)),
            ):
                if abs(got - want) > QUANT:
                    bad.append(f"ep{index}.{name} got={got} want={want}")
            if abs(Decimal(row["pnl"]) - pnl_taker_entry(ep)) <= QUANT:
                bad.append(f"ep{index}.анти-формула совпала — проверка вырождена")
        total = sum(Decimal(row["pnl"]) for row in rows_p)
        if abs(total - Decimal(pess["net_pnl"])) > QUANT:
            bad.append(f"sum(pnl)={total} != net_pnl={pess['net_pnl']}")
    if not bad:
        report("PASS", f"V4.1 {tag}: сделок={len(rows_p)} реплей+формула maker-входа точны")
    else:
        report("FAIL", f"V4.1 {tag}: {bad[:6]}")

    # V4.2: колонки pending обеих вилок == предсказание машины; пережившие окно
    # заявки — finding движка («конец окна очередь не чистит»), не провал.
    wanted = f"limit={m.pending_limit} other={m.pending_other}"
    got_p = f"limit={pess['pending_limit_buy']} other={pess['pending_other']}"
    got_o = f"limit={opt['pending_limit_buy']} other={opt['pending_other']}"
    if got_p == got_o == wanted:
        note = " [finding: заявки пережили окно]" if m.pending_limit or m.pending_other else ""
        report("PASS", f"V4.2 {tag}: {wanted}{note}")
    else:
        report("FAIL", f"V4.2 {tag}: ожидалось {wanted}, pess={got_p} opt={got_o}")

    # V4.3: счётные тождества машины с csv-атрибутами, допуск 0.
    fails: list[str] = []
    if m.submits != m.fills + m.expired + m.pending_limit:
        fails.append(
            f"submits={m.submits} != fills+expired+pending={m.fills + m.expired + m.pending_limit}"
        )
    if m.expired != m.submits - m.fills - int(pess["pending_limit_buy"]):
        fails.append(
            f"expired={m.expired} != submits−fills−pending_csv"
            f"={m.submits - m.fills - int(pess['pending_limit_buy'])}"
        )
    if m.fills != len(rows_p):
        fails.append(f"entry-fills={m.fills} != rows={len(rows_p)}")
    if m.hold_exits != len(rows_p) - m.window_closes:
        fails.append(
            f"hold-выходов={m.hold_exits} != rows−window-close={len(rows_p)}−{m.window_closes}"
        )
    if m.window_closes not in (0, 1):
        fails.append(f"window_closes={m.window_closes}")
    if pess["trades"] != str(len(rows_p)) or opt["trades"] != str(len(rows_p)):
        fails.append(f"trades-колонки={pess['trades']}/{opt['trades']} != rows={len(rows_p)}")
    if not fails:
        report(
            "PASS",
            f"V4.3 {tag}: submits={m.submits} fills={m.fills} expired={m.expired}"
            f" pending={m.pending_limit}/{m.pending_other} window-close={m.window_closes}",
        )
    else:
        report("FAIL", f"V4.3 {tag}: {fails[:4]}")

    # V4.4: вилки побайтово (журнал всем файлом) и всеми метриками (csv без fork).
    raw_p = raw_lines_by_version(jp_path, version)
    raw_o = raw_lines_by_version(jo_path, version)
    forkless_p = {key: value for key, value in pess.items() if key != "fork"}
    forkless_o = {key: value for key, value in opt.items() if key != "fork"}
    whole_eq = jp_path.read_bytes() == jo_path.read_bytes()
    if raw_p == raw_o and whole_eq and forkless_p == forkless_o and rows_p == rows_o:
        report("PASS", f"V4.4 {tag}: журналы побайтово (весь файл), метрики равны")
    else:
        report(
            "FAIL",
            f"V4.4 {tag}: raw_eq={raw_p == raw_o} whole_eq={whole_eq}"
            f" metrics_eq={forkless_p == forkless_o} parsed_eq={rows_p == rows_o}",
        )


def check_symbol(symbol: str, db_path: Path) -> None:
    stem = f"{STRATEGY}_{symbol}_{START_TS}_{END_TS}"
    if not db_path.exists():
        report("FAIL", f"data {symbol}: база {db_path.name} не найдена")
        return
    bars = hour_bars(db_path, symbol)
    if bars is None:
        report("FAIL", f"data {symbol}: не {HOURS_FULL} полных часовых групп без гэпов")
        return
    closes, lows = bars
    report("PASS", f"data {symbol}: групп={len(closes)} гэпов=0")

    csv_path = CALIB / f"{stem}.csv"
    jp_path = CALIB / f"{stem}_journal_p.csv"
    jo_path = CALIB / f"{stem}_journal_o.csv"
    if not (csv_path.exists() and jp_path.exists() and jo_path.exists()):
        report("FAIL", f"artifacts {symbol}: сетка {stem} не найдена в data/calib")
        return

    parsed = parse_cells(csv_path)
    if parsed is None:
        report("FAIL", f"parse {symbol}: csv не читается как 12 ячеек × 2 вилки")
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
    if grid_pairs != {"depth": "0.003,0.005", "ttl_bars": "12,24,48", "hold_bars": "24,48"}:
        missing.append(f"grid={grid_pairs or 'не разобрана'}")
    if not missing:
        report("PASS", f"header {symbol}: условия §3/§7, сетка, отклонено=0, touches=0/0")
    else:
        report("FAIL", f"header {symbol}: в шапке нет {missing}")

    journal_p = journal_by_version(jp_path)
    journal_o = journal_by_version(jo_path)
    known = {version for (sym, _d, _e, _h), version in EXPECTED_VERSIONS.items() if sym == symbol}
    for grouped, leg in ((journal_p, "p"), (journal_o, "o")):
        foreign = sorted(set(grouped) - known)
        if foreign:
            report("FAIL", f"attribution {symbol} journal_{leg}: чужие spec_version {foreign}")

    for depth, e, hold in GRID:
        cell = cells[(depth, e, hold)]
        check_cell(
            symbol, depth, e, hold, cell, closes, lows, journal_p, journal_o, jp_path, jo_path
        )


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
