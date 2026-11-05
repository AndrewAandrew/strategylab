"""Тесты Series V расширения Decimal-движка (strategylab/deviation.py).

Закон — V-2-SIM AMENDMENT `.superpowers/sdd/2026-11-03-seriesV-altcoins/
V2-SIM-AMENDMENT-FROZEN.md` §1 (заморожен 2026-11-05 до sim-чисел): к E-движку
добавлены РОВНО три вещи — exit_mode {V1_24, V2_R1, V2_R15}, regime {а, в, г}
(«б» отвергается валидатором) и touch_ts зон/событий с attach 1-в-1 probe
`run_full`. Семантика сверяется с probe-машиной (`meas/v0_zone_deviation_probe.py`:
walk_exit / regime_ok / cell_rounds) на синтетике; E-контракт (regime=а, V1_48/V2_R2)
обязан остаться побайтно тем же — опорные Decimal-строки сняты с движка ДО правки
(прецедент tests/test_deviation_sim.py: ручной Decimal, без float).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest

from strategylab.core.bars import Bar
from strategylab.deviation import (
    EXIT_MODES,
    FRESH_SECONDS,
    PAUSE_SECONDS,
    PAUSE_STOPS,
    REGIMES,
    DeviationParams,
    DeviationResult,
    Event,
    Round,
    Zone,
    _play_rounds,
    _scan_events,
    aggregate,
    attach_touches,
    build_zones,
    find_pivots,
    run_deviation,
)

HOUR = 3600
TF4H = 4 * HOUR
DAY = 86400
CAPITAL = Decimal("10000")
FEE = Decimal("0.0006")
SLIP = Decimal("0.0005")
ONE = Decimal("1")

DEFAULT = ("100", "100.6", "99.5", "100")  # o,h,l,c обычного часа
TOUCH = ("100", "101.0", "99.0", "100")  # час касания зоны S@99 / R@101
BREAK = ("99.8", "100.2", "98.4", "100.0")  # час ложного пробоя S (0.606%, возврат)
DIP = ("98.4", "98.6", "98.0", "98.1")  # час входа: срыв ниже стопа, close под зоной
TAKE = ("100", "101.75", "99.5", "101.5")  # час входа: снятие тейка V2_R1 (101.7)

# Decimal-строки того же движка ДО V-amend (снимок сценариев A/B/C на коммите 49e85f3):
# регресс-пин «E-артефакты обязаны остаться побайтно те же» (amend §1.2).
PRE_V_NET_A = "-21.99500249875062468765617200"
PRE_V_GROSS_A = "-9.99500249875062468765617200"
PRE_V_QTY_A = "99.95002498750624687656171914"
PRE_V_NET_B_R2 = "-82.54048733209152999257946800"
PRE_V_EXIT_FILL_B_R2 = "98.35080"
PRE_V_CASH_TAIL_B_R2 = "-82.54048733209152999257946800"
PRE_V_NET_C_2ROUNDS = "-43.99000499750124937531234400"


def build_1m(
    n_hours: int = 91,
    *,
    touches: tuple[int, ...] = (12, 52),
    breaks: tuple[int, ...] = (68,),
    after: dict[int, tuple[str, str, str, str]] | None = None,
) -> list[Bar]:
    """1m-синтетика (конвенция tests/test_deviation_sim.py): DEFAULT-часы, TOUCH на
    touches, BREAK на breaks, after — явные формы отдельных часов."""
    spec = {ht: DEFAULT for ht in range(n_hours)}
    for ht in touches:
        spec[ht] = TOUCH
    for ht in breaks:
        spec[ht] = BREAK
    for ht, s in (after or {}).items():
        spec[ht] = s
    out: list[Bar] = []
    for ht in range(n_hours):
        o, h, lo, c = (Decimal(x) for x in spec[ht])
        for m in range(60):
            out.append(Bar(ht * HOUR + m * 60, o, h, lo, c, Decimal("1")))
    return out


def hourly(n_hours: int, after: dict[int, tuple[str, str, str, str]] | None = None) -> list[Bar]:
    """Голый 1h-ряд: машина кругов (_play_rounds) 1m-данные не читает."""
    spec = {ht: DEFAULT for ht in range(n_hours)}
    for ht, s in (after or {}).items():
        spec[ht] = s
    out: list[Bar] = []
    for ht in range(n_hours):
        o, h, lo, c = (Decimal(x) for x in spec[ht])
        out.append(Bar(ht * HOUR, o, h, lo, c, Decimal("1")))
    return out


def machine_bars(n_hours: int, plan: list[tuple[int, str]]) -> list[Bar]:
    """1h-ряд под машину кругов: событийный час = BREAK (стоп 98.4), час входа — DIP
    (стоп на баре входа), TAKE (тейк 101.7 на баре входа) или DEFAULT (доживает).
    Выход прямо на баре входа => круг не видит последующие бары, перекрёстных
    стопов между кругами нет (порядок плана по времени заведомо чист)."""
    after: dict[int, tuple[str, str, str, str]] = {}
    for ht, outcome in plan:
        after[ht] = BREAK
        after[ht + 1] = {"stop": DIP, "take": TAKE, "open": DEFAULT}[outcome]
    return hourly(n_hours, after=after)


def params(
    *,
    tol: str = "0.005",
    depth: str = "0.003",
    conf: int = 0,
    k: int = 1,
    exit_mode: str = "V1_48",
    regime: str = "а",
) -> DeviationParams:
    return DeviationParams(
        tol=Decimal(tol),
        depth=Decimal(depth),
        conf_min=conf,
        vol_filter=k,
        exit_mode=exit_mode,
        regime=regime,
    )


def run(
    bars: list[Bar], p: DeviationParams, start: int = 0, end: int | None = None
) -> DeviationResult:
    if end is None:
        end = bars[-1].ts + 60
    return run_deviation(bars, p, start_ts=start, end_ts=end)


def event(i: int, *, touch_ts: tuple[int, ...] = ()) -> Event:
    """Событие-кандидат скана для прямых вызовов машины кругов (S, стоп = low бара i)."""
    return Event(
        i=i,
        ts=i * HOUR,
        kind="S",
        ref=Decimal("99.0"),
        depth=Decimal("0.0061"),
        stuck=0,
        volok=True,
        wide=True,
        touch_ts=touch_ts,
    )


def pairs(rounds: Sequence[Round]) -> list[tuple[int, str]]:
    return [(r.ts, r.reason) for r in rounds]


# --- exit_mode V1_24: time-box 24 1h-баров (probe EXITS ("V1_24", 24, 0.0)) -----------


def test_v1_24_time_box_exits_at_24th_bar_with_tb_reason() -> None:
    """hold_i = j_fill+24, выход — close бара hold_i, причина "tb" (probe walk_exit с
    r_mult==0): событие h68 -> вход h69 -> выход bar 93 (close 100 adverse-down)."""
    res = run(build_1m(n_hours=95), params(exit_mode="V1_24"))
    assert res.rounds_count == 1 and res.n_tb == 1 and res.n_forced == 0
    r = res.rounds[0]
    entry_fill = Decimal("100") * (ONE + SLIP)
    exit_fill = Decimal("100") * (ONE - SLIP)
    assert (r.ts, r.entry_ts, r.exit_ts, r.reason) == (68 * HOUR, 69 * HOUR, 93 * HOUR, "tb")
    assert r.exit_ref == Decimal("100") and r.exit_fill == exit_fill
    assert r.net == CAPITAL * (exit_fill / entry_fill - ONE) - 2 * CAPITAL * FEE
    assert res.net == r.net and res.friction == 2 * CAPITAL * FEE
    assert res.equity == CAPITAL + r.net


def test_v1_24_window_truncation_exits_forced_on_last_bar() -> None:
    """V1_24 у края фолда: hold_i=93 > end_i=90 => форс = CLOSE последнего бара окна,
    причина "force" (probe: b = min(j_fill+hold_h, end_i))."""
    res = run(build_1m(n_hours=91), params(exit_mode="V1_24"))
    assert res.rounds_count == 1 and res.n_forced == 1 and res.n_tb == 0
    r = res.rounds[0]
    assert (r.exit_ts, r.reason, r.exit_ref) == (90 * HOUR, "force", Decimal("100"))
    assert r.exit_fill == Decimal("100") * (ONE - SLIP)


# --- exit_mode V2_R1 / V2_R15: R-множители 1 и 1.5 (probe EXITS) ----------------------


def test_v2_r1_take_at_one_r_exactly() -> None:
    """V2_R1: тейк = entry_fill + 1*risk, risk = (entry_fill - stop)*side. Здесь
    entry 100.05, stop 98.4 => take 101.70 ровно; выход — цена тейка (не close бара)."""
    res = run(build_1m(after={70: ("100.5", "102.0", "100.4", "101.5")}), params(exit_mode="V2_R1"))
    assert res.rounds_count == 1 and res.n_takes == 1
    r = res.rounds[0]
    entry_fill = Decimal("100") * (ONE + SLIP)
    take_px = entry_fill + ONE * (entry_fill - Decimal("98.4"))
    assert take_px == Decimal("101.7")
    assert r.stop_px == Decimal("98.4")
    assert (r.reason, r.exit_ts, r.exit_ref, r.exit_fill) == (
        "take", 70 * HOUR, take_px, take_px * (ONE - SLIP))
    assert r.net == CAPITAL * (r.exit_fill / entry_fill - ONE) - 2 * CAPITAL * FEE


def test_v2_r1_stop_checked_before_take_in_same_bar() -> None:
    """Один бар достреливает и stop (low 98.0 <= 98.4) и take (high 102.5 >= 101.7):
    обязан дать stop — консервативный приоритет probe сохранён и для R1."""
    res = run(build_1m(after={70: ("100.5", "102.5", "98.0", "101.2")}), params(exit_mode="V2_R1"))
    assert res.rounds_count == 1 and res.n_stops == 1 and res.n_takes == 0
    r = res.rounds[0]
    entry_fill = Decimal("100") * (ONE + SLIP)
    assert (r.reason, r.exit_ts, r.exit_ref, r.exit_fill) == (
        "stop",
        70 * HOUR,
        Decimal("98.4"),
        Decimal("98.4") * (ONE - SLIP),
    )
    assert r.net == CAPITAL * (r.exit_fill / entry_fill - ONE) - 2 * CAPITAL * FEE


def test_v2_r15_take_price_at_one_and_half_r() -> None:
    """V2_R15: R-множитель Decimal("1.5") => 100.05 + 1.5*1.65 = 102.525 ровно
    (никакого float-дрейфа в цене выхода)."""
    res = run(build_1m(after={70: ("100.5", "102.6", "100.4", "101.5")}),
              params(exit_mode="V2_R15"))
    assert res.rounds_count == 1 and res.n_takes == 1
    r = res.rounds[0]
    entry_fill = Decimal("100") * (ONE + SLIP)
    take_px = entry_fill + Decimal("1.5") * (entry_fill - Decimal("98.4"))
    assert take_px == Decimal("102.525")
    assert (r.reason, r.exit_ts, r.exit_ref, r.exit_fill) == (
        "take",
        70 * HOUR,
        take_px,
        take_px * (ONE - SLIP),
    )


def test_v2_r1_nonpositive_risk_skips_round_time_box_does_not() -> None:
    """risk <= 0 (входной бар открылся ниже стопа событийного бара) => круг НЕ
    играется (probe walk_exit возвращает None); time-box на тех же данных круг
    играет — значит пропуск идёт от R-гейта, а не от данных."""
    bars = build_1m(after={69: ("98.3", "99.0", "98.0", "98.5")})
    for mode in ("V2_R1", "V2_R15", "V2_R2"):
        assert run(bars, params(exit_mode=mode)).rounds_count == 0, mode
    for mode in ("V1_24", "V1_48"):
        assert run(bars, params(exit_mode=mode)).rounds_count == 1, mode


# --- валидаторы regime / exit_mode ---------------------------------------------------


def test_regime_b_and_unknown_rejected_known_accepted() -> None:
    """regime обязан быть из {а, в, г}: «б» (исключено freeze-пропозалом) и всё
    остальное — ValueError; дефолт поля — «а» (обратная совместимость E)."""
    bars = build_1m()
    assert params().regime == "а"
    assert REGIMES == ("а", "в", "г")
    for bad in ("б", "д", "a", ""):
        with pytest.raises(ValueError):
            run(bars, params(regime=bad))
    for good in REGIMES:
        assert run(bars, params(regime=good)).rounds_count == 1, good


def test_exit_mode_set_extended_and_unknown_rejected() -> None:
    """EXIT_MODES расширен до пяти значений amend; незнакомый V2_R3 — ValueError."""
    assert set(EXIT_MODES) == {"V1_48", "V2_R2", "V1_24", "V2_R1", "V2_R15"}
    bars = build_1m()
    for mode in EXIT_MODES:
        assert run(bars, params(exit_mode=mode)).rounds_count == 1, mode
    with pytest.raises(ValueError):
        run(bars, params(exit_mode="V2_R3"))


# --- зоны/события: touch_ts и attach (probe run_full) --------------------------------


def test_build_zones_fills_cluster_touch_timestamps() -> None:
    """build_zones кладёт в touch_ts ВРЕМЕНА КАСАНИЙ КЛАСТЕРА (bucket0 + i*tf_secs,
    sorted tuple); у Zone/Event значение по умолчанию — пустой кортеж."""
    pivots = [
        (0, Decimal("99.0"), "S"),
        (12, Decimal("99.05"), "S"),
        (20, Decimal("97.0"), "S"),
    ]
    zones = build_zones(pivots, 0, TF4H, 2, Decimal("0.005"))
    assert len(zones) == 1
    assert zones[0].touch_ts == (0, 12 * TF4H)
    assert Zone(ref=Decimal("99.0"), kind="S", confirm_ts=0).touch_ts == ()
    assert event(1).touch_ts == ()


def test_attach_touches_adds_in_band_pivots_outside_the_cluster() -> None:
    """Attach 1-в-1 probe `run_full`: в набор касаний зоны попадают ВСЕ пивоты того же
    kind в допуске tol от ref, даже если кластер разорван по MIN_GAP (пивот b=15 по
    цене 98.9 в полосе 99.0±0.005, но зазор 2 < MIN_GAP => только attach). ref/kind/
    confirm_ts не меняются, входной список не мутируется."""
    pivots = [
        (3, Decimal("99.0"), "S"),
        (13, Decimal("99.0"), "S"),
        (15, Decimal("98.9"), "S"),
        (17, Decimal("98.4"), "S"),
        (40, Decimal("101.0"), "R"),
    ]
    zones = build_zones(pivots, 0, TF4H, 2, Decimal("0.005"))
    assert [(z.ref, z.kind, z.confirm_ts, z.touch_ts) for z in zones] == [
        (Decimal("99.0"), "S", 16 * TF4H, (3 * TF4H, 13 * TF4H))
    ]
    attached = attach_touches(zones, pivots, 0, TF4H, Decimal("0.005"))
    assert len(attached) == 1
    z = attached[0]
    assert z.touch_ts == (3 * TF4H, 13 * TF4H, 15 * TF4H)
    assert (z.ref, z.kind, z.confirm_ts) == (Decimal("99.0"), "S", 16 * TF4H)
    assert zones[0].touch_ts == (3 * TF4H, 13 * TF4H)  # неизменяемость входа


def test_scan_attaches_zone_touches_into_events() -> None:
    """Событие уносит touch_ts своей зоны (probe: c["touch_ts"] = z["touch_ts"]):
    касания кластера h12/h52; пивот пробоя h68 (98.4) вне полосы tol=0.005 => не
    добавляется; порядок — sorted, дублей нет."""
    bars = build_1m()
    bars1h = aggregate(bars, HOUR)
    zone_bars = aggregate(bars, TF4H)
    pivots = find_pivots(zone_bars)
    zones = attach_touches(
        build_zones(pivots, zone_bars[0].ts, TF4H, 2, Decimal("0.005")),
        pivots,
        zone_bars[0].ts,
        TF4H,
        Decimal("0.005"),
    )
    events = _scan_events(bars1h, [b.ts for b in bars], [b.close for b in bars], zones, 0)
    assert len(events) == 1
    assert events[0].ts == 68 * HOUR
    assert events[0].touch_ts == (3 * TF4H, 13 * TF4H)  # 12h и 52h


# --- regime «в»: свежесть 30d (probe regime_ok) ---------------------------------------


def test_regime_v_freshness_boundary_future_only_and_empty_touch_set() -> None:
    """Границы свежести (probe regime_ok «в» — по max касания <= ts): ровно 30d назад
    живо, 30d+1с мертво, набор только из будущих касаний мертво, пустой набор мёртв.
    «а» и «г» свежесть не фильтруют (probe: reg in (а, г) => True)."""
    bars = hourly(1010)
    ts = 1000 * HOUR
    for regime in ("а", "г"):
        assert len(_play_rounds([event(1000)], bars, params(regime=regime))) == 1, regime
    assert len(_play_rounds([event(1000, touch_ts=(ts - FRESH_SECONDS,))], bars,
                            params(regime="в"))) == 1
    assert len(_play_rounds([event(1000, touch_ts=(ts - DAY, 9 * DAY, ts - FRESH_SECONDS))], bars,
                            params(regime="в"))) == 1
    assert _play_rounds([event(1000, touch_ts=(ts - FRESH_SECONDS - 1,))], bars,
                        params(regime="в")) == []
    assert _play_rounds([event(1000, touch_ts=(ts + 1, 100 * DAY))], bars, params(regime="в")) == []
    assert _play_rounds([event(1000, touch_ts=())], bars, params(regime="в")) == []


def test_regime_v_engine_drops_31d_touch_keeps_29d_touch() -> None:
    """Движок на длинном пуле: касания h12/h52; ложные пробои h68 (16h), h748 (29d —
    живёт), h796 (31d — умирает). Скан от этого не меняется (events_count=3), режет
    именно машина кругов."""
    res_a = run(_V_FRESH_BARS, params())
    assert [r.ts for r in res_a.rounds] == [68 * HOUR, 748 * HOUR, 796 * HOUR]
    assert res_a.events_count == 3 and res_a.rounds_count == 3
    res_v = run(_V_FRESH_BARS, params(regime="в"))
    assert [r.ts for r in res_v.rounds] == [68 * HOUR, 748 * HOUR]
    assert res_v.events_count == 3 and res_v.rounds_count == 2


def test_regime_v_engine_survives_on_attached_touch_only() -> None:
    """Attach реально подключён к движку: у зоны S@99 кластерные касания h12/h52 (для
    события h790 — старше 30d), а пивот h764 (98.9 в полосе, но кластер разорван
    глубоким пробоем h748) приходит только attach => событие h790 в «в» выживает."""
    assert [r.ts for r in run(_V_ATTACH_BARS, params()).rounds] == [68 * HOUR, 790 * HOUR]
    assert [r.ts for r in run(_V_ATTACH_BARS, params(regime="в")).rounds] == [68 * HOUR, 790 * HOUR]


# --- regime «г»: пауза после 2 стопов (probe cell_rounds) ----------------------------


def test_regime_g_pause_machine_probe_verbatim() -> None:
    """Машина паузы 1-в-1 cell_rounds: 2 стопа подряд => paused_until = ts события +
    30d, счётчик обнуляется; событие строго раньше — пропуск; событие ровно на
    границе играется (строгое <); любой не-stop выход сбрасывает consec."""
    assert (PAUSE_STOPS, PAUSE_SECONDS) == (2, 30 * DAY)
    plan = [
        (10, "stop"),
        (20, "stop"),  # consec=2 => пауза до 20h+720h = 740h
        (300, "stop"),  # в паузе => пропуск (в «а» играется)
        (740, "stop"),  # ровно paused_until => играется (строгое <), consec=1
        (760, "stop"),  # consec=2 => пауза до 760h+720h = 1480h
        (1500, "take"),  # после паузы => играется
    ]
    bars = machine_bars(1510, plan)
    evs = [event(ht) for ht, _ in plan]
    assert pairs(_play_rounds(evs, bars, params(exit_mode="V2_R1"))) == [
        (ht * HOUR, "stop") for ht, _ in plan[:5]
    ] + [(1500 * HOUR, "take")]
    assert pairs(_play_rounds(evs, bars, params(exit_mode="V2_R1", regime="г"))) == [
        (10 * HOUR, "stop"),
        (20 * HOUR, "stop"),
        (740 * HOUR, "stop"),
        (760 * HOUR, "stop"),
        (1500 * HOUR, "take"),
    ]
    # тейк между стопами обнуляет счётчик => пауза сдвигается на пару 200/300, и
    # событие 1000 (1000h < 300h+720h = 1020h) пропускается, а 1100 играется.
    plan2 = [(10, "stop"), (100, "take"), (200, "stop"), (300, "stop"),
             (1000, "open"), (1100, "open")]
    bars2 = machine_bars(1200, plan2)
    evs2 = [event(ht) for ht, _ in plan2]
    assert [r.ts for r in _play_rounds(evs2, bars2, params(exit_mode="V2_R1"))] == [
        ht * HOUR for ht, _ in plan2
    ]
    assert pairs(_play_rounds(evs2, bars2, params(exit_mode="V2_R1", regime="г"))) == [
        (10 * HOUR, "stop"),
        (100 * HOUR, "take"),
        (200 * HOUR, "stop"),
        (300 * HOUR, "stop"),
        (1100 * HOUR, "force"),
    ]


def test_regime_g_engine_two_stops_pause_then_resume() -> None:
    """Движок на реальном скане (V2_R1): стопы h68/h120 => пауза до 840h; h200
    пропущен, h840 (граница) сыгран, тейк h900 сбросил счётчик, стопы h950/h1000 =>
    пауза до 1720h, h1100 пропущен. В «а» играются все 8 кругов того же скана."""
    res_a = run(_G_BARS, params(exit_mode="V2_R1"))
    assert res_a.events_count == 8 and res_a.rounds_count == 8
    assert pairs(res_a.rounds) == [
        (68 * HOUR, "stop"),
        (120 * HOUR, "stop"),
        (200 * HOUR, "stop"),
        (840 * HOUR, "stop"),
        (900 * HOUR, "take"),
        (950 * HOUR, "stop"),
        (1000 * HOUR, "stop"),
        (1100 * HOUR, "stop"),
    ]
    res_g = run(_G_BARS, params(exit_mode="V2_R1", regime="г"))
    assert res_g.events_count == 8  # скан не меняется — режет только машина кругов
    assert pairs(res_g.rounds) == [
        (68 * HOUR, "stop"),
        (120 * HOUR, "stop"),
        (840 * HOUR, "stop"),
        (900 * HOUR, "take"),
        (950 * HOUR, "stop"),
        (1000 * HOUR, "stop"),
    ]
    assert res_g.net != res_a.net


# --- регресс E-контракта: числа сняты с движка ДО amend ------------------------------


def test_regime_a_v1_48_numbers_byte_identical_to_pre_amend() -> None:
    """Дефолты (regime=а, V1_48) обязаны дать ПРЯ ТЕ Decimal-строки, что и движок до
    V-amend (net/gross/friction/equity/qty/строки журнала)."""
    res = run(build_1m(), params())
    assert format(res.net, "f") == PRE_V_NET_A
    assert format(res.price_gross, "f") == PRE_V_GROSS_A
    assert format(res.friction, "f") == "12.0000"
    assert format(res.equity, "f") == "9978.004997501249375312343828"
    assert format(res.journal[0].qty, "f") == PRE_V_QTY_A
    assert format(res.journal[0].cash_delta, "f") == "-6.0000"
    assert format(res.journal[-1].cash_delta, "f") == "-15.99500249875062468765617200"
    assert pairs(res.rounds) == [(68 * HOUR, "force")]


def test_regime_a_v2_r2_numbers_byte_identical_to_pre_amend() -> None:
    """V2_R2 + regime «а»: V2_R_MULT не тронут, scale Decimal-строк не поехал."""
    after = {
        69: ("99.0", "99.6", "98.9", "99.5"),
        70: ("99.5", "101.0", "98.3", "100.0"),
    }
    res = run(build_1m(after=after), params(exit_mode="V2_R2"))
    assert format(res.net, "f") == PRE_V_NET_B_R2
    assert format(res.rounds[0].exit_fill, "f") == PRE_V_EXIT_FILL_B_R2
    assert format(res.journal[-1].cash_after, "f") == PRE_V_CASH_TAIL_B_R2
    assert res.n_stops == 1


def test_regime_a_two_round_fold_numbers_byte_identical_to_pre_amend() -> None:
    """Два круга (tb + force) на 130-часовом фолде — побайтно до-amend-ный net."""
    res = run(build_1m(130, breaks=(68, 120)), params())
    assert pairs(res.rounds) == [(68 * HOUR, "tb"), (120 * HOUR, "force")]
    assert format(res.net, "f") == PRE_V_NET_C_2ROUNDS


def test_regime_a_default_equals_explicit_regime_a_for_every_mode() -> None:
    """Явный regime="а" == дефолт на всех пяти exit_mode (нетфильтра, без пауз)."""
    bars = build_1m(130, breaks=(68, 120))
    for mode in EXIT_MODES:
        implicit = run(bars, params(exit_mode=mode))
        explicit = run(bars, params(exit_mode=mode, regime="а"))
        assert implicit.rounds == explicit.rounds and implicit.net == explicit.net


def test_journal_invariants_hold_for_new_modes() -> None:
    """Инвариант журнала для новых режимов/режимов: ровно 2 строки на круг, сумма
    cash_delta == net, cash_after сходится с net, reason строки выхода = reason круга."""
    bars = build_1m(130, breaks=(68, 120))
    for mode in EXIT_MODES:
        for regime in REGIMES:
            res = run(bars, params(exit_mode=mode, regime=regime))
            assert len(res.journal) == 2 * res.rounds_count, (mode, regime)
            assert sum((row.cash_delta for row in res.journal), Decimal("0")) == res.net
            assert res.journal[-1].cash_after == res.net
            for r, en, ex in zip(res.rounds, res.journal[0::2], res.journal[1::2], strict=True):
                assert (en.kind, en.ts, en.reason) == ("entry", r.entry_ts, "")
                assert en.fill == r.entry_fill
                assert (ex.kind, ex.ts, ex.reason) == ("exit", r.exit_ts, r.reason)
                assert ex.fill == r.exit_fill


def test_determinism_x2_for_new_modes_and_regimes() -> None:
    """Контроль детерминизма (amend §6): повтор прогона даёт побайтно те же круги,
    журнал и Decimal-строку net (touch_ts-кортежи отсортированы, обход множеств
    на результат не влияет)."""
    bars = build_1m(130, breaks=(68, 120))
    for mode in EXIT_MODES:
        for regime in REGIMES:
            p = params(exit_mode=mode, regime=regime)
            r1, r2 = run(bars, p), run(bars, p)
            assert r1.rounds == r2.rounds and r1.journal == r2.journal
            assert format(r1.net, "f") == format(r2.net, "f")


# --- длинные синтетики для «в»/«г» строятся один раз на модуль -----------------------


def _fresh_bars() -> list[Bar]:
    """Касания h12/h52; ложные пробои h68, h748 (29d от касания h52) и h796 (31d).
    Пробойные цены 98.4 вне полосы tol=0.005 => в набор касаний зоны не входят."""
    return build_1m(800, breaks=(68, 748, 796))


def _attach_bars() -> list[Bar]:
    """Касания h12/h52; h68 ложный пробой; h748 настоящий пробой (close 98.5 под
    зоной => события нет, но цепочка кластера рвётся); h764 = 98.9 в полосе tol
    (attach-only касание на 764h); h790 ложный пробой: 738h от h52 (>30d) и 26h от
    attach-касания."""
    return build_1m(
        800,
        breaks=(68, 790),
        after={748: ("99.0", "99.2", "98.4", "98.5"), 764: ("99.5", "100.0", "98.9", "99.6")},
    )


def _pause_bars() -> list[Bar]:
    """8 ложных пробоев (h68..h1100): входной час DIP => стоп на баре входа; у h900
    входной час TAKE => тейк. h840 = ровно paused_until после стопов h68+h120."""
    after: dict[int, tuple[str, str, str, str]] = {ht + 1: DIP for ht in
                                                    (68, 120, 200, 840, 950, 1000, 1100)}
    after[901] = TAKE
    return build_1m(1110, breaks=(68, 120, 200, 840, 900, 950, 1000, 1100), after=after)


_V_FRESH_BARS = _fresh_bars()
_V_ATTACH_BARS = _attach_bars()
_G_BARS = _pause_bars()
