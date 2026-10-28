"""Verify spec_02 (buy & hold): независимый пересчёт конвейера из сырых баров.

Заморожен ДО прогона артефактов по design §5 (V2.1–V2.4), §3 (совпадение вилок
побайтово, пины spec_version) и Task 2 брифа. Ноль импортов из `strategylab`:
только stdlib — sqlite3 (read-only URI) с SQL-литералами, свой часовой bucketing
`ts // 3600`, свои Decimal-константы из Global Constraints design §3.

Вывод: построчно `PASS|FAIL <критерий> <СИМВОЛ> <числа>`; ненулевой exit при любом
FAIL. Парсинг-краш ячейки регистрируется как `FAIL fatal` и не убивает вторую ногу.

Tolerance (design §5): строгое равенство Decimal с допуском ±1E-20 на ассоциацию
вычитаний; допущение «±1 бар на исполнение» здесь не используется — бар входа
выведен из движка строго (ордер с бара 0 исполняется на apply_bar бара 1).
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
STRATEGY = "buy_hold"
SYMBOLS: tuple[tuple[str, Path], ...] = (
    ("BTCUSDT", ROOT / "data" / "btcusdt.db"),
    ("ETHUSDT", ROOT / "data" / "ethusdt.db"),
)
CALIB = ROOT / "data" / "calib"

# design §3 «пины spec_version-хешей»: канонический JSON StrategySpec
# {name: "calib:buy_hold", symbol, interval: 3600, start_ts, end_ts, params: {},
#  rules_ref: "calibration-suite"} → sha256[:12] (стратегия без параметров).
EXPECTED_VERSION = {"BTCUSDT": "7c82a0e0adbc", "ETHUSDT": "421696b5bfa0"}

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


def parse_cell(path: Path) -> tuple[list[str], dict[str, dict[str, str]]] | None:
    """(сырые строки шапки до колонок, строки обеих вилок по заголовку) или None.

    Шапка снимается СЫРЫМИ строками: харнесс пишет её не через csv.writer, а
    построчно, и `window: [a, b)` с запятой внутри csv-ридер распал бы на поля.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("fork,")), None)
    if header_index is None:
        return None
    table = list(csv.reader(lines[header_index:]))
    header = table[0]
    data_rows = [row for row in table[1:] if row]
    if len(data_rows) != 2:
        return None
    parsed: dict[str, dict[str, str]] = {}
    for row in data_rows:
        if len(row) != len(header) or row[0] not in ("pessimistic", "optimistic"):
            return None
        if row[0] in parsed:
            return None
        parsed[row[0]] = dict(zip(header, row, strict=True))
    if set(parsed) != {"pessimistic", "optimistic"}:
        return None
    return lines[:header_index], parsed


def journal_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def no_exponent(value: str) -> bool:
    return "E" not in value.upper()


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

    entry = closes[1] * (Decimal("1") + SLIPPAGE)
    fee_in = TAKER * entry
    exit_price = closes[-1] * (Decimal("1") - SLIPPAGE)
    fee_out = TAKER * exit_price
    net = exit_price - entry - fee_in - fee_out
    premise_loss = exit_price < entry  # убывающее окно (design §5: BTC −26% ETH −36%)

    csv_path = CALIB / f"{stem}.csv"
    jp_path = CALIB / f"{stem}_journal_p.csv"
    jo_path = CALIB / f"{stem}_journal_o.csv"
    report_path = CALIB / f"{stem}_report_p.txt"
    if not (csv_path.exists() and jp_path.exists() and jo_path.exists() and report_path.exists()):
        report("FAIL", f"artifacts {symbol}: не найдены полные артефакты прогона в data/calib")
        return

    parsed = parse_cell(csv_path)
    if parsed is None:
        report("FAIL", f"parse {symbol}: csv ячейки не читается как 2 строки вилки")
        return
    provenance, rows = parsed
    pess, opt = rows["pessimistic"], rows["optimistic"]

    # дизайн §3: замороженные условия прогона задекларированы в шапке артефакта
    wanted_header = {
        f"window: [{START_TS}, {END_TS})",
        f"capital: {CAPITAL}",
        "costs: taker_fee=0.0006 maker_fee=0.0002 slippage=0.0005 leverage=1",
        "zones_used: fit",
    }
    missing = sorted(wanted_header - set(provenance))
    if not missing:
        report("PASS", f"header {symbol}: window/capital/costs/zones соответствует §3")
    else:
        report("FAIL", f"header {symbol}: в шапке нет {missing}")

    # design §3: пин spec_version-хеша + сквозная идентичность идентификатора
    versions = {pess["spec_version"], opt["spec_version"]}
    journal = journal_rows(jp_path)
    journal_o = journal_rows(jo_path)
    versions |= {row["spec_version"] for row in journal + journal_o[:1]}
    if versions == {EXPECTED_VERSION[symbol]}:
        report("PASS", f"version {symbol}: spec_version={EXPECTED_VERSION[symbol]} везде")
    else:
        report("FAIL", f"version {symbol}: найдено {sorted(versions)}")

    # V2.1: ровно одна сделка на инструмент, закрыта сводкой окна
    if len(journal) == 1 and journal[0]["exit_reason"] == "market":
        report("PASS", f"V2.1 {symbol}: сделок=1 exit_reason=market")
    else:
        reason = journal[0]["exit_reason"] if journal else "(нет)"
        report("FAIL", f"V2.1 {symbol}: строк={len(journal)} exit_reason={reason}")

    # V2.2: таймстемпы/цены/pnl/equity/max_drawdown/pending == аналитика из баров
    if not journal:
        report("FAIL", f"V2.2 {symbol}: journal пуст — сверять нечего")
    else:
        trade = journal[0]
        numeric_fields = [
            pess["net_pnl"], pess["equity"], pess["max_drawdown"], pess["win_rate"],
            pess["profit_factor"], trade["entry_price"], trade["exit_price"], trade["pnl"],
        ]
        decimal_checks: list[tuple[str, Decimal, Decimal]] = [
            ("entry_price", Decimal(trade["entry_price"]), entry),
            ("exit_price", Decimal(trade["exit_price"]), exit_price),
            ("journal.pnl", Decimal(trade["pnl"]), net),
            ("csv.net_pnl", Decimal(pess["net_pnl"]), net),
            ("csv.equity", Decimal(pess["equity"]), CAPITAL + net),
        ]
        if premise_loss:
            decimal_checks.append(("csv.max_drawdown", Decimal(pess["max_drawdown"]), -net))
        exact_checks: list[tuple[str, object, object]] = [
            ("entry_ts", int(trade["entry_ts"]), START_TS + 3600),
            ("exit_ts", int(trade["exit_ts"]), END_TS - 3600),
            ("csv.pending_limit_buy", pess["pending_limit_buy"], "0"),
            ("csv.pending_other", pess["pending_other"], "0"),
        ]
        bad = [
            f"{name}: got={got} want={want}"
            for name, got, want in decimal_checks
            if abs(got - want) > QUANT
        ] + [
            f"{name}: got={got} want={want}"
            for name, got, want in exact_checks
            if got != want
        ]
        exponent_breakers = [value for value in numeric_fields if not no_exponent(value)]
        if not bad and not exponent_breakers:
            report(
                "PASS",
                f"V2.2 {symbol}: entry_ts={START_TS + 3600} entry={entry} exit={exit_price}"
                f" net={net} equity={pess['equity']} dd={pess['max_drawdown']} pending=0/0",
            )
        else:
            report("FAIL", f"V2.2 {symbol}: {bad} экспоненты={exponent_breakers}")

    # V2.3: вырожденная статистика + рендер null-маркеров (премиса: убыточное окно)
    report_lines = report_path.read_text(encoding="utf-8").splitlines()
    if premise_loss:
        stats_ok = (
            pess["trades"] == "1"
            and pess["wins"] == "0"
            and pess["win_rate"] == "0"
            and pess["profit_factor"] == "0"
            and pess["sharpe"] == "null"
        )
        render_ok = (
            f"profit_factor: {pess['profit_factor']}" in report_lines
            and f"win_rate: {pess['win_rate']}" in report_lines
            and f"sharpe: {pess['sharpe']}" in report_lines
        )
        if stats_ok and render_ok:
            report(
                "PASS",
                f"V2.3 {symbol}: trades=1 wins=0 win_rate='0' pf='0' sharpe='null'"
                f" + построчные маркеры рендера ({len(report_lines)} строк)",
            )
        else:
            report(
                "FAIL",
                f"V2.3 {symbol}: stats=({pess['win_rate']},{pess['profit_factor']},"
                f"{pess['sharpe']}) render_ok={render_ok}",
            )
        # V2.4: знак по фактическим исполняемым барам (вход — бар 1, не close[0])
        if Decimal(pess["net_pnl"]) < 0:
            report("PASS", f"V2.4 {symbol}: exit={exit_price}<entry={entry} => net<0")
        else:
            report("FAIL", f"V2.4 {symbol}: premise exit<entry, но net={pess['net_pnl']} >= 0")
    else:
        premise_fail = "премиса убывающего окна не подтвердилась — стоп к владельцу"
        report("FAIL", f"V2.3 {symbol}: {premise_fail}")
        report("FAIL", f"V2.4 {symbol}: {premise_fail}")

    # design §3: у спек без внутрибарных развилок вилки совпадают ПОБАЙТОВО
    forkless_p = {k: v for k, v in pess.items() if k != "fork"}
    forkless_o = {k: v for k, v in opt.items() if k != "fork"}
    if forkless_p == forkless_o and jp_path.read_bytes() == jo_path.read_bytes():
        report("PASS", f"forks {symbol}: csv без колонки fork и журналы побайтово равны")
    else:
        report(
            "FAIL",
            f"forks {symbol}: csv_равен={forkless_p == forkless_o}"
            f" journal_байты_равны={jp_path.read_bytes() == jo_path.read_bytes()}",
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
