"""Тесты Decimal-симулятора Series E zone-deviation (strategylab/deviation.py).

Закон — E-2-F пререгистрации `.superpowers/sdd/2026-11-02-seriesE-deviation/`:
замороженные константы (W=3, MIN_GAP=10, confirm=ts(touch2)+W·tf, cooldown 48×1h,
stuck 360/sentinel 399, net = capital*side*(px/entry_px−1) − 2*capital*fee) и
единственное признанное отличие от зонда — k2 на trailing-медиане 30d. Арифметика
сверяется с ручными Decimal-ожиданиями (прецедент tests/test_carry_sim.py).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from strategylab.core.bars import Bar
from strategylab.deviation import (
    DeviationParams,
    DeviationResult,
    build_zones,
    run_deviation,
)

HOUR = 3600
TF4H = 4 * HOUR
CAPITAL = Decimal("10000")
FEE = Decimal("0.0006")
SLIP = Decimal("0.0005")

DEFAULT = ("100", "100.6", "99.5", "100")  # o,h,l,c обычного часа
TOUCH = ("100", "101.0", "99.0", "100")  # час касания зоны S@99 / R@101
BREAK = ("99.8", "100.2", "98.4", "100.0")  # час ложного пробоя S (глубина 0.606%)


def build_1m(
    n_hours: int = 91,
    *,
    touches: tuple[int, ...] = (12, 52),
    breaks: tuple[int, ...] = (68,),
    break_shape: tuple[str, str, str, str] = BREAK,
    after: dict[int, tuple[str, str, str, str]] | None = None,
    vol_per_min: dict[int, str] | None = None,
    minute_close: dict[tuple[int, int], str] | None = None,
) -> list[Bar]:
    """1m-синтетика: DEFAULT-часы, TOUCH на touches, BREAK-форма на breaks;
    after — явные формы отдельных часов, vol_per_min — объём минуты часа,
    minute_close — плоский (o=h=l=c) час.минута для stuck-перебоя."""
    spec = {ht: DEFAULT for ht in range(n_hours)}
    for ht in touches:
        spec[ht] = TOUCH
    for ht in breaks:
        spec[ht] = break_shape
    for ht, s in (after or {}).items():
        spec[ht] = s
    out: list[Bar] = []
    for ht in range(n_hours):
        o, h, lo, c = (Decimal(x) for x in spec[ht])
        v = Decimal((vol_per_min or {}).get(ht, "1"))
        for m in range(60):
            px = (minute_close or {}).get((ht, m))
            if px is None:
                out.append(Bar(ht * HOUR + m * 60, o, h, lo, c, v))
            else:
                p = Decimal(px)
                out.append(Bar(ht * HOUR + m * 60, p, p, p, p, v))
    return out


def params(
    *,
    tol: str = "0.005",
    depth: str = "0.003",
    conf: int = 0,
    k: int = 1,
    exit_mode: str = "V1_48",
) -> DeviationParams:
    return DeviationParams(
        tol=Decimal(tol),
        depth=Decimal(depth),
        conf_min=conf,
        vol_filter=k,
        exit_mode=exit_mode,
    )


def run(
    bars: list[Bar], p: DeviationParams, start: int = 0, end: int | None = None
) -> DeviationResult:
    if end is None:
        end = bars[-1].ts + 60
    return run_deviation(bars, p, start_ts=start, end_ts=end)


def test_build_zones_min_gap_band_median_confirm() -> None:
    """Зеркало зондового S3: зазор >=10, допуск от МЕДИАНЫ кластера,
    confirm = ts(touch2) + W*tf; разрывband'а и короткий кластер — без зоны."""
    pivots = [(0, Decimal("99.0"), "S"), (12, Decimal("99.05"), "S"), (20, Decimal("97.0"), "S")]
    zones = build_zones(pivots, 0, TF4H, 2, Decimal("0.005"))
    assert len(zones) == 1
    z = zones[0]
    assert z.kind == "S" and z.ref == Decimal("99.025") and z.confirm_ts == 15 * TF4H
    # зазор 5 < MIN_GAP — касания не соединяются
    assert not build_zones(
        [(0, Decimal("99.0"), "S"), (5, Decimal("99.0"), "S")], 0, TF4H, 2, Decimal("0.005")
    )
    # разрыв допуска (101 vs медиана 99): зона только из первых двух
    assert (
        len(
            build_zones(
                [
                    (0, Decimal("99.0"), "S"),
                    (12, Decimal("99.0"), "S"),
                    (30, Decimal("101.0"), "S"),
                ],
                0,
                TF4H,
                2,
                Decimal("0.005"),
            )
        )
        == 1
    )


def test_long_v1_48_force_hand_net() -> None:
    """Базовый сценарий (зеркало S4–S5 зонда): зона S@99 от касаний h12/h52
    (confirm 64h), ложный пробой h68 (глубина 0.606%, close обратно) -> long.
    Вход open h69 adverse+slip, стоп = low событийного бара, форс-выход
    CLOSE последнего бара фолда (закреплено тестом: форс по close, не по open)."""
    res = run(build_1m(), params())
    assert res.zones_count == 2 and res.events_count == 1
    assert res.rounds_count == 1 and res.flat_fold is False
    r = res.rounds[0]
    assert r.kind == "S" and r.ts == 68 * HOUR
    entry_fill = Decimal("100") * (1 + SLIP)
    exit_fill = Decimal("100") * (1 - SLIP)
    assert r.entry_ts == 69 * HOUR and r.entry_ref == Decimal("100")
    assert r.entry_fill == entry_fill
    assert r.stop_px == Decimal("98.4")
    assert r.reason == "force" and r.exit_ts == 90 * HOUR and r.exit_fill == exit_fill
    expected = CAPITAL * (exit_fill / entry_fill - 1) - 2 * CAPITAL * FEE
    assert r.net == expected
    assert res.net == expected and res.equity == CAPITAL + expected
    assert res.price_gross == CAPITAL * (exit_fill / entry_fill - 1)
    assert res.friction == 2 * CAPITAL * FEE and res.n_forced == 1
    rows = [(row.kind, row.ts) for row in res.journal]
    assert rows == [("entry", 69 * HOUR), ("exit", 90 * HOUR)]
    assert res.journal[-1].cash_after == expected


def test_short_r_zone_hand_net() -> None:
    """Зеркало сверху: R-зона@101, пробой сверху h68 (hi 101.6, close 100.9<101),
    шорт: вход open+следующего часа*(1−slip), стоп = high события, форс close
    последнего бара; падающие послесобытийные часы дают плюс."""
    after = {ht: ("100.0", "100.2", "99.0", "99.0") for ht in range(69, 91)}
    bars = build_1m(
        after=after,
        break_shape=("101.2", "101.6", "99.8", "100.9"),
    )
    res = run(bars, params())
    assert res.rounds_count == 1
    r = res.rounds[0]
    assert r.kind == "R" and r.ts == 68 * HOUR
    entry_fill = Decimal("100") * (1 - SLIP)  # шорт adverse — вниз от open
    exit_fill = Decimal("99") * (1 + SLIP)
    assert r.entry_ts == 69 * HOUR and r.stop_px == Decimal("101.6")
    assert r.reason == "force"
    expected = CAPITAL * (-1) * (exit_fill / entry_fill - 1) - 2 * CAPITAL * FEE
    assert r.net == expected and res.net == expected


def test_v2_r2_stop_priority_in_same_bar() -> None:
    """V2_R2: бар, где одновременно достижимы stop и take, обязан дать stop —
    консервативный приоритет стопа перед тейком из зонда. Стоп-филл = stop_px*(1−slip)."""
    after = {
        69: ("99.0", "99.6", "98.9", "99.5"),
        70: ("99.5", "101.0", "98.3", "100.0"),
    }
    res = run(build_1m(after=after), params(exit_mode="V2_R2"))
    assert res.rounds_count == 1 and res.n_stops == 1
    r = res.rounds[0]
    entry_fill = Decimal("99") * (1 + SLIP)
    stop_fill = Decimal("98.4") * (1 - SLIP)
    assert r.entry_ts == 69 * HOUR and r.stop_px == Decimal("98.4")
    assert r.reason == "stop" and r.exit_ts == 70 * HOUR and r.exit_fill == stop_fill
    expected = CAPITAL * (stop_fill / entry_fill - 1) - 2 * CAPITAL * FEE
    assert r.net == expected


def test_depth_and_conf_gates_and_decision_shift() -> None:
    """d-гейт: глубина 0.606% живёт при d<=0.005 и умирает при d=0.010 (зонд S7).
    conf=0/30/60 при sentinel-stuck (399) проходят все, но решение сдвигается
    (формула зонда ts+3600+conf*60, вход — первый open >= решения): conf0 -> h69,
    conf30 -> 69.5h -> h70, conf60 -> ровно h70 -> h70."""
    bars = build_1m()
    assert run(bars, params(depth="0.010")).rounds_count == 0
    assert run(bars, params(depth="0.005")).rounds_count == 1
    assert run(bars, params(conf=30)).rounds[0].entry_ts == 70 * HOUR
    assert run(bars, params(conf=60)).rounds[0].entry_ts == 70 * HOUR
    # перебой: 1m close ниже уровня через 10 мин после закрытия события -> stuck=10
    mc = {(69, m): "98.95" for m in range(10, 60)}
    bars_rb = build_1m(minute_close=mc)
    assert run(bars_rb, params()).rounds_count == 1
    assert run(bars_rb, params(conf=30)).rounds_count == 0
    assert run(bars_rb, params(conf=60)).rounds_count == 0


def test_zone_cooldown_48_at_scan_stage() -> None:
    """Кулдаун зоны 48×1h-баров применяется на стадии скана событий (семантика
    зонда): событие h100 (зазор 32) съедено кулдауном h68; событие h120 (52) — нет."""
    res1 = run(build_1m(n_hours=130, breaks=(68, 100)), params())
    assert res1.events_count == 1 and res1.rounds_count == 1
    res2 = run(build_1m(n_hours=130, breaks=(68, 120)), params())
    assert res2.events_count == 2 and res2.rounds_count == 2
    reasons = [r.reason for r in res2.rounds]
    assert reasons == ["tb", "force"]  # 69+48=117 <= 129 — time-box; 121+48 — форс


def test_k2_trailing_median_and_min_history() -> None:
    """Ужесточение freeze: k2 = объём события >= 2*медианы последних <=480 1h-баров
    (20d, включая сам событийный бар); окно меньше 200 баров вместе с событийным —
    клетка НЕ стреляет (консервативно); wide-spread обязателен."""
    # 91 час: история 69 баров к событию h68 < 200 -> k2 мертва, k1 жива
    bars91 = build_1m(vol_per_min={68: "5"})
    assert run(bars91, params()).rounds_count == 1
    assert run(bars91, params(k=2)).rounds_count == 0
    # 260 часов: событие h220 с историей 221>=200, объём 300 против медианы 60,
    # wide=True (|c−o|=1.5 >= 0.6*1.8) -> k2 стреляет ровно один раз
    bars260 = build_1m(
        260,
        breaks=(68, 220),
        break_shape=("98.6", "100.2", "98.4", "100.1"),
        vol_per_min={220: "5"},
    )
    assert run(bars260, params()).rounds_count == 2
    res_k2 = run(bars260, params(k=2))
    assert res_k2.rounds_count == 1 and res_k2.rounds[0].ts == 220 * HOUR
    # без wide-spread (стандартная BREAK-форма: |c−o|=0.2 < 0.6*1.8) k2 мертва
    bars_narrow = build_1m(260, breaks=(68, 220), vol_per_min={220: "5"})
    assert run(bars_narrow, params(k=2)).rounds_count == 0


def test_fold_window_events_only_inside_fold() -> None:
    """Контекст до start_ts строит зоны/пивоты/объёмы, но СОБЫТИЯ — только с
    bar.ts >= start_ts; решение обязано успеть в фолд (нужен бар входа)."""
    bars = build_1m()
    a = run(bars, params(), start=0, end=91 * HOUR)
    b = run(bars, params(), start=40 * HOUR, end=91 * HOUR)
    assert a.rounds == b.rounds and b.rounds_count == 1
    c = run(bars, params(), start=69 * HOUR, end=91 * HOUR)
    assert c.rounds_count == 0 and c.flat_fold is True
    d = run(bars, params(), start=68 * HOUR, end=91 * HOUR)
    assert d.rounds_count == 1


def test_no_lookahead_entry_data_after_event_unused() -> None:
    """Скрепы no-lookahead: данные ПОСЛЕ закрытия событийного бара (кроме самих
    филлов/стопа-в-баре) не меняют ни зону, ни решение, ни вход. Сценарии расходятся
    с h72 (краш / обрезка окна) — entry/stop первого круга идентичны байт-в-байт."""
    base = build_1m()
    crash_after = build_1m(
        after={ht: ("98.5", "98.8", "97.5", "97.6") for ht in range(72, 91)},
    )
    truncated = build_1m(n_hours=75)
    runs = [run(base, params(exit_mode="V2_R2")), run(crash_after, params(exit_mode="V2_R2"))]
    for res in runs:
        r = res.rounds[0]
        assert (r.ts, r.entry_ts, r.entry_ref, r.entry_fill, r.stop_px) == (
            68 * HOUR,
            69 * HOUR,
            Decimal("100"),
            Decimal("100") * (1 + SLIP),
            Decimal("98.4"),
        )
    # расхождение фактов после h72 видно только в выходе, не во входе
    assert runs[0].rounds[0].reason == "force" and runs[1].rounds[0].reason == "stop"
    cut = run(truncated, params())
    assert cut.journal[0] == runs[0].journal[0]


def test_determinism_x2() -> None:
    bars = build_1m()
    p = params(k=2, exit_mode="V2_R2")
    r1 = run(bars, p)
    r2 = run(bars, p)
    assert r1.journal == r2.journal
    assert r1.rounds == r2.rounds
    assert r1.net == r2.net and r1.equity == r2.equity


def test_validation() -> None:
    bars = build_1m()
    ok = params()
    with pytest.raises(ValueError):
        run(bars, DeviationParams(tol=Decimal("0.005"), depth=Decimal("0.003"),
                                  conf_min=45, vol_filter=1, exit_mode="V1_48"))
    with pytest.raises(ValueError):
        run(bars, DeviationParams(tol=Decimal("0.005"), depth=Decimal("0.003"),
                                  conf_min=0, vol_filter=3, exit_mode="V1_48"))
    with pytest.raises(ValueError):
        run(bars, DeviationParams(tol=Decimal("0.005"), depth=Decimal("0.003"),
                                  conf_min=0, vol_filter=1, exit_mode="V1_24"))
    with pytest.raises(ValueError):
        run(bars, params(depth="0.001"))  # ниже сканируемого порога зонда
    with pytest.raises(ValueError):
        run(bars, params(tol="0"))
    with pytest.raises(ValueError):
        run(bars, DeviationParams(tol=Decimal("0.005"), depth=Decimal("0.003"),
                                  conf_min=0, vol_filter=1, exit_mode="V1_48",
                                  capital=Decimal("0")))
    with pytest.raises(ValueError):
        run(bars, DeviationParams(tol=Decimal("0.005"), depth=Decimal("0.003"),
                                  conf_min=0, vol_filter=1, exit_mode="V1_48",
                                  touches=1))
    with pytest.raises(ValueError):
        run(bars, ok, start=0, end=0)
    with pytest.raises(ValueError):
        run(bars + [Bar(91 * HOUR, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
                        Decimal("1"))], ok, end=91 * HOUR)
    dup = bars + bars[:1]
    with pytest.raises(ValueError):
        run(dup, ok)


def test_deviation_module_has_no_kernel_imports() -> None:
    """E-5/B-5-идентично: deviation.py импортирует только core.bars — без ядра."""
    from pathlib import Path

    src = Path("strategylab/deviation.py").read_text(encoding="utf-8")
    for forbidden in (
        "strategylab.core.engine",
        "strategylab.broker",
        "strategylab.strategy",
        "strategylab.data",
    ):
        assert forbidden not in src
