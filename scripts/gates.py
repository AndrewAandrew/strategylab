"""Гейты спеки 01 «Двусторонний импульс/пробой»: таблица пяти гейтов по CSV-развёрткам.

Скрипт живёт вне пакета `strategylab` и импортирует только стандартную бибилиотеку:
он читает пять CSV-развёрток `sweep.py` и ничего не пишет — ни в базу, ни в
`data/touch_registry.json` (касания регистрирует развёртка, не гейты).

Правила зафиксированы pre-registered спекой (`strategydocs/spec_01_two_sided_momentum_20261022.md`)
и брифом задачи 12; спорить с числами скрипт не вправе:

1. Гейт 1 — ≥100 сделок на инструмент (сумма `long_trades + short_trades` строки
   дефолтной комбинации на BTC и на ETH). Спека оговорила третий исход отдельно:
   <30 печатает «расширять спек», 30–99 печатает «не пройден».
2. Гейт 2 — медианный PF сетки ≥1.15 на КАЖДОМ инструменте (по 18 строкам).
   `null` в `profit_factor` — ни одной убыточной сделки, то есть бесконечное
   преимущество: такой ряд ранжируется ВЫШЕ любого числа (не выбрасывается и не
   превращается в ноль). Число null-рядов печатается отдельной строкой: медиана,
   держящаяся на малом числе конечных строк, — это видно.
3. Гейт 3 — PF дефолтной комбинации ≥1.3 (при базовых двойных комиссиях — ровно
   тех, что уже зафиксированы движком и напечатаны в шапке развёртки). null PF
   здесь = бесконечность = пройден.
4. Гейт 4 — ≥2 из 3 ETH-фолдов с `net_pnl > 0` на дефолтной комбинации.
5. Гейт 5 — раздельность опубликована: у обеих сторон `trades ≥ 1`; если
   `net_pnl` одной стороны ≤ 0, а положительная сторона удерживает всю прибыль,
   — пометка «одно-система». Это требование честности интерпретации, а не число:
   строка «ГЕЙТЫ: пройдены / не пройдены» складывается из гейтов 1–4, гейт 5
   помечается отдельно и вердикт не переворачивает.

Дефолтная комбинация — константа модуля; ряд CSV опознаётся по значениям трёх
имённых колонок, а не по номеру строки. Парсер читает контракт stdout `sweep.py`:
`encoding="utf-8-sig"` (страховка от перенаправления через `>`), строки до
`spec_version,` — шапка, первая пустая строка — конец таблицы, хвост
`комбинаций:` / `отклонено:` строками сетки не становится. Числа берутся только
из пессимистичной развёртки: шапка обязана содержать `intrabar: PESSIMISTIC`.

Медиана чётного числа рядов — среднее двух средних (правило зафиксировано здесь
же: если одно из двух средних — null-ряд, медиана бесконечна).

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe scripts\\gates.py --btc data\\sweep_btc_fit.csv `
        --eth data\\sweep_eth_fit.csv --folds data\\sweep_eth_fold1.csv `
        data\\sweep_eth_fold2.csv data\\sweep_eth_fold3.csv
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

# Pre-registered дефолты спеки; ряд CSV опознаётся по этим трём колонкам.
DEFAULT_COMBO: Mapping[str, Decimal] = {
    "donch_len": Decimal("48"),
    "atr_mult": Decimal("1.5"),
    "bias_len": Decimal("48"),
}

MIN_TRADES = 100  # гейт 1: нижняя граница «пройден»
EXPAND_TRADES = 30  # гейт 1: ниже — «расширять спек», между — «не пройден»
MEDIAN_PF_FLOOR = Decimal("1.15")  # гейт 2
DEFAULT_PF_FLOOR = Decimal("1.3")  # гейт 3
INFINITE_MARK = "∞"  # печать бесконечного PF (null-ряда)


@dataclass(frozen=True)
class SweepRow:
    """Одна строка CSV-развёртки: спек, параметры, метрики пессимистичной вилки."""

    spec_version: str
    params: Mapping[str, Decimal]
    trades: int
    wins: int
    net_pnl: Decimal
    max_drawdown: Decimal
    profit_factor: Decimal | None
    sharpe: Decimal | None
    long_trades: int
    long_net_pnl: Decimal
    long_profit_factor: Decimal | None
    short_trades: int
    short_net_pnl: Decimal
    short_profit_factor: Decimal | None


def _cell(record: Mapping[str, str | None], name: str) -> str:
    """Обязательная ячейка CSV: отсутствующая колонка — ошибка, а не тихий пропуск."""
    value = record.get(name)
    if value is None:
        raise ValueError(f"в CSV нет колонки {name!r} — парсер читает контракт sweep.py")
    return value


def _decimal(record: Mapping[str, str | None], name: str) -> Decimal:
    return Decimal(_cell(record, name))


def _integer(record: Mapping[str, str | None], name: str) -> int:
    return int(_cell(record, name))


def _decimal_or_null(record: Mapping[str, str | None], name: str) -> Decimal | None:
    """null — это None (бесконечность), а не 0: безубыточную комбинацию не казнят нулём."""
    value = _cell(record, name)
    return None if value == "null" else Decimal(value)


def _row(record: Mapping[str, str | None]) -> SweepRow:
    return SweepRow(
        spec_version=_cell(record, "spec_version"),
        params={name: _decimal(record, name) for name in DEFAULT_COMBO},
        trades=_integer(record, "trades"),
        wins=_integer(record, "wins"),
        net_pnl=_decimal(record, "net_pnl"),
        max_drawdown=_decimal(record, "max_drawdown"),
        profit_factor=_decimal_or_null(record, "profit_factor"),
        sharpe=_decimal_or_null(record, "sharpe"),
        long_trades=_integer(record, "long_trades"),
        long_net_pnl=_decimal(record, "long_net_pnl"),
        long_profit_factor=_decimal_or_null(record, "long_profit_factor"),
        short_trades=_integer(record, "short_trades"),
        short_net_pnl=_decimal(record, "short_net_pnl"),
        short_profit_factor=_decimal_or_null(record, "short_profit_factor"),
    )


def parse_sweep(path: Path) -> tuple[SweepRow, ...]:
    """Строки таблицы CSV-развёртки: до `spec_version,` шапка, пустая строка — конец таблицы."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_at: int | None = None
    for index, line in enumerate(lines):
        if line.startswith("spec_version,"):
            header_at = index
            break
    if header_at is None:
        raise ValueError(f"{path}: нет строки заголовка, начинающейся с `spec_version,`")
    header = [line.strip() for line in lines[:header_at]]
    if "intrabar: PESSIMISTIC" not in header:
        raise ValueError(
            f"{path}: шапка без `intrabar: PESSIMISTIC` — числа не из развёртки sweep.py"
        )
    table: list[str] = []
    for line in lines[header_at + 1 :]:
        if not line.strip():
            break
        table.append(line)
    reader = csv.DictReader([lines[header_at], *table])
    return tuple(_row(record) for record in reader)


def find_default_row(rows: Sequence[SweepRow], label: str) -> SweepRow:
    """Ровно один ряд с pre-registered дефолтами по значениям трёх именных колонок."""
    matches = [
        row
        for row in rows
        if all(row.params[name] == value for name, value in DEFAULT_COMBO.items())
    ]
    if not matches:
        raise ValueError(f"{label}: в развёртке нет строки дефолтной комбинации")
    if len(matches) > 1:
        raise ValueError(f"{label}: несколько строк дефолтной комбинации ({len(matches)})")
    return matches[0]


def median_pf(values: Sequence[Decimal | None]) -> Decimal | None:
    """Медиана PF с null-рядами: None ранжируется выше любого числа (бесконечность).

    Чётное число рядов — среднее двух средних; если хотя бы одно из двух средних
    — None, медиана бесконечна и возвращается None. Пустой набор — ошибка.
    """
    if not values:
        raise ValueError("медиана не считается по пустому набору")
    finite = sorted(value for value in values if value is not None)
    total = len(values)
    indexes = [total // 2] if total % 2 else [total // 2 - 1, total // 2]
    picked: list[Decimal] = []
    for index in indexes:
        if index >= len(finite):
            # Позиция попала в хвост null-рядов: медиана бесконечна.
            return None
        value = finite[index]
        picked.append(value)
    if len(picked) == 2:
        return (picked[0] + picked[1]) / Decimal(2)
    return picked[0]


def _at_least(value: Decimal | None, floor: Decimal) -> bool:
    """None = бесконечность: всегда не меньше любого конечного порога."""
    return value is None or value >= floor


def _verdict(passed: bool) -> str:
    return "пройден" if passed else "не пройден"


def _fmt(value: Decimal | None) -> str:
    """Decimal без экспоненты; None — знаком бесконечности."""
    return INFINITE_MARK if value is None else format(value, "f")


def evaluate(btc_csv: Path, eth_csv: Path, fold_csvs: Sequence[Path]) -> list[str]:
    """Таблица пяти гейтов + строка вердикта; вердикт складывается из гейтов 1–4."""
    if len(fold_csvs) != 3:
        raise ValueError("гейт 4 требует ровно три CSV-фолда")
    btc_rows = parse_sweep(btc_csv)
    eth_rows = parse_sweep(eth_csv)
    btc_default = find_default_row(btc_rows, "BTC")
    eth_default = find_default_row(eth_rows, "ETH")
    folds = [
        find_default_row(parse_sweep(path), f"фолд{index + 1}")
        for index, path in enumerate(fold_csvs)
    ]

    lines = [f"строки сетки: BTC={len(btc_rows)}, ETH={len(eth_rows)}"]
    instruments = (("BTC", btc_default), ("ETH", eth_default))

    # Гейт 1: сделки дефолтной комбинации, сумма сторон.
    parts: list[str] = []
    gate1 = True
    for label, row in instruments:
        total = row.long_trades + row.short_trades
        if total >= MIN_TRADES:
            outcome = "пройден"
        elif total >= EXPAND_TRADES:
            outcome = "не пройден"
            gate1 = False
        else:
            outcome = "расширять спек"
            gate1 = False
        parts.append(f"{label}: {total} — {outcome}")
    gate1_prefix = f"гейт 1 (≥{MIN_TRADES} сделок на инструмент, long+short дефолта): "
    lines.append(gate1_prefix + "; ".join(parts))

    # Гейт 2: медианный PF сетки на каждом инструменте.
    btc_median = median_pf([row.profit_factor for row in btc_rows])
    eth_median = median_pf([row.profit_factor for row in eth_rows])
    btc_gate2 = _at_least(btc_median, MEDIAN_PF_FLOOR)
    eth_gate2 = _at_least(eth_median, MEDIAN_PF_FLOOR)
    gate2 = btc_gate2 and eth_gate2
    lines.append(
        f"гейт 2 (медианный PF сетки ≥ {_fmt(MEDIAN_PF_FLOOR)} на каждом инструменте): "
        f"BTC: {_fmt(btc_median)} — {_verdict(btc_gate2)}; "
        f"ETH: {_fmt(eth_median)} — {_verdict(eth_gate2)}"
    )
    btc_nulls = sum(1 for row in btc_rows if row.profit_factor is None)
    eth_nulls = sum(1 for row in eth_rows if row.profit_factor is None)
    lines.append(
        f"null-рядов: BTC={btc_nulls}, ETH={eth_nulls} "
        "(PF=null — ни одной убыточной сделки, в медиане выше любого числа)"
    )

    # Гейт 3: PF дефолтной комбинации (двойные комиссии уже в шапке развёртки).
    lines.append(
        f"гейт 3 (PF дефолтной комбинации ≥ {_fmt(DEFAULT_PF_FLOOR)}): "
        f"BTC: {_fmt(btc_default.profit_factor)} — "
        f"{'пройден' if _at_least(btc_default.profit_factor, DEFAULT_PF_FLOOR) else 'не пройден'}"
        f"; ETH: {_fmt(eth_default.profit_factor)} — "
        f"{'пройден' if _at_least(eth_default.profit_factor, DEFAULT_PF_FLOOR) else 'не пройден'}"
    )
    gate3 = _at_least(btc_default.profit_factor, DEFAULT_PF_FLOOR) and _at_least(
        eth_default.profit_factor, DEFAULT_PF_FLOOR
    )

    # Гейт 4: знак net_pnl дефолта на трёх ETH-фолдах.
    fold_texts = ", ".join(
        f"фолд{index + 1}: {format(row.net_pnl, 'f')}" for index, row in enumerate(folds)
    )
    positive = sum(1 for row in folds if row.net_pnl > 0)
    gate4 = positive >= 2
    lines.append(
        f"гейт 4 (≥2 из 3 ETH-фолдов с net_pnl > 0): {fold_texts} — "
        f"{'пройдено' if gate4 else 'не пройдено'} ({positive} из 3)"
    )

    # Гейт 5: раздельность опубликована; в вердикт 1–4 не входит.
    parts = []
    one_system: list[str] = []
    for label, row in instruments:
        published = row.long_trades >= 1 and row.short_trades >= 1
        parts.append(
            f"{label}: долг {row.long_trades} / шорт {row.short_trades} — "
            f"{'опубликована' if published else 'не опубликована'}"
        )
        # Вся прибыль на одной стороне: вторая ≤ 0, положительная строго > 0.
        if row.long_net_pnl > 0 >= row.short_net_pnl or row.short_net_pnl > 0 >= row.long_net_pnl:
            one_system.append(label)
    gate5_line = (
        "гейт 5 (раздельность long/short опубликована; в вердикт не входит): " + "; ".join(parts)
    )
    if one_system:
        gate5_line += f"; пометка: одно-система ({', '.join(one_system)})"
    lines.append(gate5_line)

    verdict = "ГЕЙТЫ: пройдены" if (gate1 and gate2 and gate3 and gate4) else "ГЕЙТЫ: не пройдены"
    lines.append(verdict)
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gates.py",
        description="Гейты спеки 01 по CSV-развёрткам sweep.py: таблица пяти гейтов и вердикт.",
    )
    parser.add_argument(
        "--btc", type=Path, required=True, metavar="CSV", help="CSV сетки на BTC fit"
    )
    parser.add_argument(
        "--eth", type=Path, required=True, metavar="CSV", help="CSV сетки на ETH fit"
    )
    parser.add_argument(
        "--folds",
        type=Path,
        nargs=3,
        required=True,
        metavar="CSV",
        help="три CSV ETH-фолдов (дефолтная комбинация)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    for line in evaluate(args.btc, args.eth, args.folds):
        print(line)


if __name__ == "__main__":
    main()
