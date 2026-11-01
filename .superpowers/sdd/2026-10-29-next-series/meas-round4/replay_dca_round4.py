# Реконструкция D-пути (dca_grid) по часовым барам для ячеек round 4 без артефактов.
# stdlib only, БД read-only (URI mode=ro), ничего не пишет в репо.
# Точность: Decimal 28 знаков — та же арифметика, что в engine/sim (порядок операций повторён).
# Сверки (assert): числа из трейсбеков харнесса / артефактов D-ETH-f3.
from __future__ import annotations

import sqlite3
from decimal import Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
TAKER = Decimal("0.0006")
SLIP = Decimal("0.0005")
LEV = Decimal("10")
CAPITAL = Decimal("10000")
STEP = Decimal("0.02")
LAYERS = 4  # параметры из заморозки §5: шаг 0.02, layers 4 (5 лотов qty=1)

REPO = "C:/Users/egoro/Documents/GitHub/strategylab"
OUT = "C:/Users/egoro/AppData/Local/Temp/opencode/meas-round4"

FOLDS = {
    "f1": (1761523200, 1771113600),
    "f2": (1771113600, 1780704000),
    "f3": (1780704000, 1790294400),
}


def read_hour_bars(db: str, symbol: str, start: int, end: int) -> list[tuple]:
    """Часовые бары той же агрегации, что ReplayDriver: группы ровно 60 1m-баров,
    open=первый, high=max, low=min, close=последний; граница ts%3600 (эпоха Unix)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.text_factory = lambda raw: Decimal(raw.decode())
    rows = conn.execute(
        "SELECT ts, open, high, low, close FROM bars_1m "
        "WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts",
        (symbol, start, end),
    ).fetchall()
    conn.close()
    bars: list[tuple] = []
    group: list[tuple] = []
    gstart = None
    for ts, o, h, l, c in rows:
        s = ts - ts % 3600
        if gstart is not None and s != gstart:
            assert len(group) == 60, f"неполная группа {gstart}: {len(group)}"
            bars.append((gstart, group[0][1], max(x[2] for x in group),
                         min(x[3] for x in group), group[-1][4]))
            group = []
        gstart = s
        group.append((ts, o, h, l, c))
    if group:
        assert len(group) == 60, f"неполная группа хвоста {gstart}: {len(group)}"
        bars.append((gstart, group[0][1], max(x[2] for x in group),
                     min(x[3] for x in group), group[-1][4]))
    assert len(bars) == (end - start) // 3600, f"баров {len(bars)}"
    return bars


def replay(db: str, symbol: str, start: int, end: int) -> dict:
    """Точная реконструкция D-пути: гейт, филлы, ликвидация, сведение окна.

    Порядок внутри бара повторяет engine.run + BrokerSimulator.apply_bar:
    (1) liquidation-check по low (до маркет-филлов), (2) маркет-филл поданных
    лотов по slipped(close) + taker, (3) on_bar-логика dca_grid (якорь/лестница),
    (4) гейт _guard_submit на каждую поданную заявку (MARGIN_CRASH здесь),
    (5) на последнем баре — cancel очереди + сведение по slipped(close).
    Учёт equity: opening-филлы НЕ пишутся в metrics (движок booked только
    закрытия), входная комиссия висит в position.entry_fee и списывается на
    закрытии — как в engine._record_realized.
    """
    bars = read_hour_bars(db, symbol, start, end)
    equity = CAPITAL
    pos_qty = ZERO
    pos_avg = ZERO
    pos_fee = ZERO
    pending: list[Decimal] = []  # объёмы поданных маркет-заявок
    anchor: Decimal | None = None
    bought = 0
    min_marked = None
    min_marked_ts = None
    path: list[tuple] = []

    for i, (ts, o, h, l, c) in enumerate(bars):
        # (1) ликвидация до маркет-филлов (sim._apply_liquidation)
        if pos_qty > 0:
            adverse = l  # long
            unreal = (adverse - pos_avg) * pos_qty
            margin = pos_qty * pos_avg / LEV
            if unreal <= -margin:
                exit_fee = TAKER * pos_qty * adverse
                realized = (adverse - pos_avg) * pos_qty - exit_fee - pos_fee
                marked_at_liq = equity + unreal  # позиция по adverse; fill тут же закрывает
                equity_after = equity  # движок умер: realized ликвидации в metrics НЕ записан
                return {
                    "outcome": "LIQUIDATED",
                    "crash_ts": ts, "adverse": adverse, "unrealised": unreal,
                    "margin": margin, "lots": pos_qty, "avg": pos_avg,
                    "equity_booked": equity_after, "marked_at_liq": marked_at_liq,
                    "min_marked": min_marked, "min_marked_ts": min_marked_ts,
                    "bars_traded": i + 1, "path_tail": path[-3:],
                }
        # (2) маркет-филл поданных лотов
        for _ in pending:
            fill_price = c * (ONE + SLIP)
            fee = TAKER * fill_price  # qty=1
            if pos_qty == 0:
                pos_qty = ONE
                pos_avg = fill_price
                pos_fee = fee
            else:
                total = pos_qty + ONE
                pos_avg = (pos_avg * pos_qty + fill_price) / total
                pos_qty = total
                pos_fee += fee
        pending = []
        # marked на закрытии бара (гейт-формула: metrics.equity + qty×(close−avg))
        marked = equity + pos_qty * (c - pos_avg) if pos_qty > 0 else equity
        if min_marked is None or marked < min_marked:
            min_marked = marked
            min_marked_ts = ts
        path.append((ts, c, pos_qty, marked))
        # (3) логика dca_grid на баре
        submitted = []
        if anchor is None:
            anchor = c
            bought = 1
            submitted.append(ONE)
        elif pos_qty != 0 and bought < LAYERS + 1 and c <= anchor * (ONE - STEP) ** bought:
            bought += 1
            submitted.append(ONE)
        # (4) гейт на каждую поданную заявку
        for qty_order in submitted:
            required = (pos_qty * c + qty_order * c) / LEV
            marked_now = equity + pos_qty * (c - pos_avg) if pos_qty > 0 else equity
            if required > marked_now:
                return {
                    "outcome": "MARGIN_CRASH",
                    "crash_ts": ts, "close": c, "required": required,
                    "marked": marked_now, "filled_lots": pos_qty, "submitted": bought,
                    "equity_booked": equity, "avg": pos_avg,
                    "min_marked": min_marked, "min_marked_ts": min_marked_ts,
                    "bars_traded": i + 1, "path_tail": path[-3:],
                }
            pending.append(qty_order)
        # (5) конец окна: cancel + сведение
        if i == len(bars) - 1:
            if pos_qty > 0:
                price = c * (ONE - SLIP)
                fee = TAKER * pos_qty * price
                realized = (price - pos_avg) * pos_qty - fee - pos_fee
                equity += realized
                return {
                    "outcome": "SURVIVED_CLOSE" if equity >= CAPITAL else "UNDERWATER_CLOSE",
                    "lots": pos_qty, "avg": pos_avg, "exit": price,
                    "equity_final": equity, "net": equity - CAPITAL,
                    "min_marked": min_marked, "min_marked_ts": min_marked_ts,
                    "bars_traded": i + 1,
                }
            return {
                "outcome": "SURVIVED_CLOSE" if equity >= CAPITAL else "UNDERWATER_CLOSE",
                "lots": ZERO, "equity_final": equity, "net": equity - CAPITAL,
                "min_marked": min_marked, "min_marked_ts": min_marked_ts,
                "bars_traded": i + 1,
            }
    raise AssertionError("пустое окно")


def show(label: str, res: dict) -> None:
    print(f"== {label} ==")
    for key, value in res.items():
        if key == "path_tail":
            print(f"  path_tail: {value}")
        else:
            print(f"  {key}: {value}")
    print()


# --- Запуск по шести D-ячейкам с сверкой чисел харнесса -------------------------
results: dict[str, dict] = {}

res = replay(f"{REPO}/data/btcusdt.db", "BTCUSDT", *FOLDS["f1"])
assert res["outcome"] == "MARGIN_CRASH", res
assert res["close"] == Decimal("114719.5"), res["close"]
assert res["required"] == Decimal("11471.95"), res["required"]
assert res["marked"] == CAPITAL, res["marked"]
results["D-BTC-f1"] = res
show("D-BTC-f1 (сверка: close 114719.5, required 11471.95, marked 10000)", res)

res = replay(f"{REPO}/data/btcusdt.db", "BTCUSDT", *FOLDS["f2"])
assert res["outcome"] == "MARGIN_CRASH", res
assert res["required"] == Decimal("13652.6"), res["required"]
assert res["marked"] == Decimal("8544.25805"), res["marked"]
results["D-BTC-f2"] = res
show("D-BTC-f2 (сверка: required 13652.6, marked 8544.25805)", res)

res = replay(f"{REPO}/data/btcusdt.db", "BTCUSDT", *FOLDS["f3"])
assert res["outcome"] == "MARGIN_CRASH", res
assert res["required"] == Decimal("11961.38"), res["required"]
assert res["marked"] == Decimal("8747.78570"), res["marked"]
results["D-BTC-f3"] = res
show("D-BTC-f3 (сверка: required 11961.38, marked 8747.78570)", res)

res = replay(f"{REPO}/data/ethusdt.db", "ETHUSDT", *FOLDS["f1"])
assert res["outcome"] == "LIQUIDATED", res
assert res["adverse"] == Decimal("3578.57"), res["adverse"]
assert res["unrealised"] == Decimal("-2065.68428000"), res["unrealised"]
results["D-ETH-f1"] = res
show("D-ETH-f1 (сверка: adverse 3578.57, unrealised -2065.68428)", res)

res = replay(f"{REPO}/data/ethusdt.db", "ETHUSDT", *FOLDS["f2"])
assert res["outcome"] == "LIQUIDATED", res
assert res["adverse"] == Decimal("1767.42"), res["adverse"]
assert res["unrealised"] == Decimal("-1001.17668000"), res["unrealised"]
results["D-ETH-f2"] = res
show("D-ETH-f2 (сверка: adverse 1767.42, unrealised -1001.17668)", res)

res = replay(f"{REPO}/data/ethusdt.db", "ETHUSDT", *FOLDS["f3"])
assert res["outcome"] == "SURVIVED_CLOSE", res
assert res["lots"] == Decimal("3"), res["lots"]
assert res["avg"] == Decimal("1569.297590"), res["avg"]
assert res["exit"] == Decimal("2685.246705"), res["exit"]
assert res["equity_final"] == Decimal("13340.1891652690"), res["equity_final"]
results["D-ETH-f3"] = res
show("D-ETH-f3 (сверка с артефактами: lots 3, avg 1569.297590, exit 2685.246705, "
     "equity 13340.1891652690)", res)

# --- агрегаты §5 ----------------------------------------------------------------
print("== АГРЕГАТЫ D (медиана просадки = медиана (10000 - min_marked)/10000) ==")
dd = []
for name, res in results.items():
    d = (CAPITAL - res["min_marked"]) / CAPITAL * 100
    dd.append(d)
    print(f"  {name}: outcome={res['outcome']} lots={res.get('lots', res.get('filled_lots'))} "
          f"min_marked={res['min_marked']} dd%={d:.4f}")
dd_sorted = sorted(dd)
median = (dd_sorted[2] + dd_sorted[3]) / 2
print(f"  медиана просадки: {median:.4f}%")
tail = sum(1 for r in results.values() if r["outcome"] in ("LIQUIDATED", "MARGIN_CRASH"))
print(f"  хвостовые исходы (LIQUIDATED/MARGIN_CRASH): {tail}/6")
print("  ВСЕ СВЕРКИ ПРОЙДЕНЫ")
