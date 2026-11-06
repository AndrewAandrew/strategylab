"""Тесты машины прогона H-0 (scripts/h0/run.py) — TDD на синтетических 1h-барах.

Закон: .superpowers/sdd/2026-11-03-global-audit/H0-freeze-criteria-DRAFT.md.
Ни один тест не читает data/ethusdt.db и вообще любые реальные бары: форвард
до 2027-01-01 не читается. Модуль грузится по пути файла — приём как
tests/test_carry_grid.py::_load_grid.

Конвенции, замороженные ДО кода (интерпретации закона, п. §1-§2):
- SMA20(vol)_t и ATR(14)_t считаются ВКЛЮЧАЯ событийный бар t (как в r1 M8.1);
- направление фейда: up-кульминация <=> close_t > open_t (шорт); иначе (включая
  doji close==open) — даун-кульминация (лонг), как знак в r1-формуле;
- вход сим: fill = open(t+1) c adverse-слипом (шорт-вход — продажа ниже,
  лонг-вход — покупка выше); выход time-exit: close(t+1+k) тоже с adverse;
- катастроф-стоп: расстояние 4*ATR(14)_t событийного бара от цены входа-fill;
  гэп за стоп исполняется по цене открытия бара (хуже стопа); внутри бара,
  совпавшего с time-exit, приоритет у стопа (консервативно);
- круги независимые (перекрытие разрешено, как r1-прокси: каждый событие-круг
  сам по себе); не хватает баров до close(t+1+k) — круг отбрасывается;
- треть назначается по ts событийного бара;
- покрытие окна: ровно 1440 подряд идущих 1h-баров [WINDOW_START, +3600*1440).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from strategylab.core.bars import Bar

_RUN = Path(__file__).resolve().parent.parent / "scripts" / "h0" / "run.py"

W0 = 1793577600  # WINDOW_START — 2026-11-02 00:00 UTC (заморожено)
W1 = 1798761600  # WINDOW_END — 2027-01-01 00:00 UTC (заморожено)


def _load_h0() -> ModuleType:
    cached = sys.modules.get("h0_run_under_test")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("h0_run_under_test", _RUN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["h0_run_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def h0() -> ModuleType:
    return _load_h0()


def bar(ts: int, o: str, hi: str, lo: str, c: str, v: str) -> Bar:
    return Bar(
        ts=ts,
        open=Decimal(o),
        high=Decimal(hi),
        low=Decimal(lo),
        close=Decimal(c),
        volume=Decimal(v),
    )


def flat(n: int, start_ts: int = W0, *, price: str = "100") -> list[Bar]:
    """Ровные 1h-бары: TR=1, ATR→1, ADX→0. Индикаторы готовы с бара 27."""
    out = []
    for i in range(n):
        p = Decimal(price)
        out.append(
            bar(
                start_ts + 3600 * i,
                price,
                str(p + Decimal("0.5")),
                str(p - Decimal("0.5")),
                price,
                "1000",
            )
        )
    return out


def with_bars(base: list[Bar], *patches: tuple[int, Bar]) -> list[Bar]:
    out = list(base)
    for idx, b in patches:
        out[idx] = bar(b.ts, str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume))
    return out


def climax_bar(i: int, *, up: bool = True, vol: str = "10000") -> Bar:
    """Кульминация на ровном фоне: range 7 > 3*ATR(≈1.43), vol 10000 > 3*SMA20(=1450)."""
    ts = W0 + 3600 * i
    if up:
        return bar(ts, "100", "106", "99", "104", vol)
    return bar(ts, "100", "106", "94", "96", vol)


# ---------------------------------------------------------------- сигналы M8.1


def test_event_bar_produces_short_signal_when_adx_low(h0):
    bars = with_bars(flat(60), (30, climax_bar(30, up=True)))
    signals = h0.detect_signals(bars)
    assert [(s.event_index, s.side) for s in signals] == [(30, -1)]


def test_down_climax_produces_long_signal(h0):
    bars = with_bars(flat(60), (30, climax_bar(30, up=False)))
    signals = h0.detect_signals(bars)
    assert [(s.event_index, s.side) for s in signals] == [(30, +1)]


def test_doji_climax_counts_as_down_climax_long(h0):
    # close_t == open_t — не «up» по r1-формуле (close > open), значит даун-фейд.
    ts = W0 + 3600 * 30
    bars = with_bars(flat(60), (30, bar(ts, "100", "106", "94", "100", "10000")))
    signals = h0.detect_signals(bars)
    assert [(s.event_index, s.side) for s in signals] == [(30, +1)]


@pytest.mark.parametrize(
    "patch",
    [
        # только объём (range обычный): не кульминация
        lambda i: bar(W0 + 3600 * i, "100", "100.5", "99.5", "100", "10000"),
        # только размах (обычный объём): не кульминация
        lambda i: bar(W0 + 3600 * i, "100", "106", "94", "104", "1000"),
    ],
    ids=["vol-only", "range-only"],
)
def test_partial_conditions_do_not_fire(h0, patch):
    bars = with_bars(flat(60), (30, patch(30)))
    assert h0.detect_signals(bars) == []


def test_signals_suppressed_before_indicators_ready(h0):
    # на баре 10 ADX(14) ещё не готов (первый ADX ≈ бар 28 по закону) — сигнала нет.
    bars = with_bars(flat(60), (10, climax_bar(10, up=True)))
    assert h0.detect_signals(bars) == []


def test_signal_rejected_when_adx_ge_25(h0):
    # чистый монотонный тренд заводит ADX≥25; та же кульминация без гейта
    # стреляет — значит отсекает именно ADX-гейт (r1 M8.3: на ETH в ADX≥25 −0.20 %).
    n = 80
    base = []
    prev = Decimal("100")
    for i in range(n):
        ts = W0 + 3600 * i
        p = prev + Decimal("1")  # +DM>0, −DM=0 каждый бар → DX≈100, ADX растёт к 100
        base.append(bar(ts, str(prev), str(p + Decimal("0.5")), str(prev - Decimal("0.2")), str(p), "1000"))
        prev = p
    i = n - 10
    base[i] = bar(W0 + 3600 * i, str(prev - Decimal(1)), str(prev + Decimal(20)), str(prev - Decimal(10)), str(prev + Decimal(15)), "100000")
    gated = h0.detect_signals(base)
    ungated = h0.detect_signals(base, adx_max=None)
    assert all(s.event_index != i for s in gated)
    assert any(s.event_index == i and s.side == -1 for s in ungated)


# ------------------------------------------------------------------ круги


def test_short_time_exit_economics_exact(h0):
    # k=1: вход open(t+1)=100 (шорт — продажа ниже слипа), выход close(t+2)=90 (откуп выше).
    i = 30
    ts1, ts2 = W0 + 3600 * (i + 1), W0 + 3600 * (i + 2)
    bars = with_bars(
        flat(60),
        (i, climax_bar(i, up=True)),
        (i + 1, bar(ts1, "100", "100.5", "99.5", "100", "1000")),
        (i + 2, bar(ts2, "95", "95", "90", "90", "1000")),
    )
    (rnd,) = h0.simulate(bars, k=1)
    assert rnd.exit_reason == "time"
    slip, fee = Decimal("0.0005"), Decimal("0.0006")
    entry = Decimal("100") * (1 - slip)          # шорт-вход: продажа ниже — adverse
    exit_ = Decimal("90") * (1 + slip)           # откуп выше — adverse
    qty = Decimal(10000) / entry
    net_usd = qty * (entry - exit_) - qty * entry * fee - qty * exit_ * fee
    assert rnd.entry_price == entry
    assert rnd.exit_price == exit_
    assert rnd.net_usd == net_usd
    assert rnd.net_pct == net_usd / Decimal(10000)


def test_long_time_exit_economics_exact(h0):
    i = 30
    ts1, ts2 = W0 + 3600 * (i + 1), W0 + 3600 * (i + 2)
    bars = with_bars(
        flat(60),
        (i, climax_bar(i, up=False)),
        (i + 1, bar(ts1, "100", "100.5", "99.5", "100", "1000")),
        (i + 2, bar(ts2, "105", "110", "105", "110", "1000")),
    )
    (rnd,) = h0.simulate(bars, k=1)
    slip, fee = Decimal("0.0005"), Decimal("0.0006")
    entry = Decimal("100") * (1 + slip)          # лонг-вход: покупка выше — adverse
    exit_ = Decimal("110") * (1 - slip)          # продажа ниже — adverse
    qty = Decimal(10000) / entry
    net_usd = qty * (exit_ - entry) - qty * entry * fee - qty * exit_ * fee
    assert rnd.exit_reason == "time"
    assert rnd.net_usd == net_usd


def test_catastrophe_stop_triggers_within_hold(h0):
    # шорт на i=30: ATR_t = 20/14, entry≈99.95, стоп≈99.95+5.714=105.66;
    # бар t+1 не задевает, бар t+2 high=106 ≥ стоп → катастрофа по стопу.
    i = 30
    b = {j: bar(W0 + 3600 * j, "100", "100.5", "99.5", "100", "1000") for j in (31, 32, 33)}
    b[32] = bar(W0 + 3600 * 32, "100", "106", "99", "100", "1000")
    bars = with_bars(flat(60), (i, climax_bar(i, up=True)), *b.items())
    (rnd,) = h0.simulate(bars, k=2)
    assert rnd.exit_reason == "catastrophe"
    atr_t = (Decimal(13) * 1 + Decimal(7)) / Decimal(14)
    stop = rnd.entry_price + 4 * atr_t
    assert rnd.exit_price == stop * (1 + Decimal("0.0005"))
    assert rnd.exit_ts == W0 + 3600 * 32


def test_catastrophe_stop_gaps_open_worse_than_stop(h0):
    # бар открывается уже за стопом (110 > 105.66) — исполнение по открытию, не по стопу.
    i = 30
    b = {j: bar(W0 + 3600 * j, "100", "100.5", "99.5", "100", "1000") for j in (31, 32, 33)}
    b[32] = bar(W0 + 3600 * 32, "110", "112", "109", "110", "1000")
    bars = with_bars(flat(60), (i, climax_bar(i, up=True)), *b.items())
    (rnd,) = h0.simulate(bars, k=2)
    assert rnd.exit_reason == "catastrophe"
    atr_t = (Decimal(13) * 1 + Decimal(7)) / Decimal(14)
    stop = rnd.entry_price + 4 * atr_t
    assert stop < Decimal("110")
    assert rnd.exit_price == Decimal("110") * (1 + Decimal("0.0005"))


def test_stop_on_exit_bar_wins_over_time_exit(h0):
    # high бара t+1+k за стопом — приоритет у катастрофы (консервативно).
    i = 30
    ts2 = W0 + 3600 * (i + 2)
    bars = with_bars(
        flat(60),
        (i, climax_bar(i, up=True)),
        (i + 1, bar(W0 + 3600 * (i + 1), "100", "100.5", "99.5", "100", "1000")),
        (i + 2, bar(ts2, "100", "200", "99", "100", "1000")),
    )
    (rnd,) = h0.simulate(bars, k=1)
    assert rnd.exit_reason == "catastrophe"


def test_round_dropped_when_exit_bar_missing(h0):
    # событие на баре 58 из 60: close(58+1+1) некуда брать — круг отбрасывается.
    bars = with_bars(flat(60), (58, climax_bar(58, up=True)))
    assert h0.simulate(bars, k=1) == []
    # для k=0-границы: событие 58 с k=1 требует бар 60 — нет; событие 57 есть, бар 59 — ok.
    bars2 = with_bars(flat(60), (57, climax_bar(57, up=True)))
    assert len(h0.simulate(bars2, k=1)) == 1


def test_time_exit_price_is_close_of_t_plus_1_plus_k(h0):
    # выход берётся с close бара t+1+k, не раньше: high внутри не задевает стоп.
    i = 30
    bars = with_bars(
        flat(70),
        (i, climax_bar(i, up=True)),
        (i + 1 + 12, bar(W0 + 3600 * (i + 13), "88", "89", "87", "88", "1000")),
    )
    (rnd,) = h0.simulate(bars, k=12)
    assert rnd.exit_ts == W0 + 3600 * (i + 13)
    assert rnd.exit_price == Decimal("88") * (1 + Decimal("0.0005"))


# ------------------------------------------------------------------ трети


def test_third_boundaries(h0):
    assert h0.third_of(W0) == 0
    assert h0.third_of(W0 + 20 * 86400 - 1) == 0
    assert h0.third_of(W0 + 20 * 86400) == 1
    assert h0.third_of(W0 + 40 * 86400) == 2
    assert h0.third_of(W0 + 60 * 86400 - 1) == 2


# ------------------------------------------------------------------ гейты H-a..H-e


def make_round(h0, net_pct: str, *, ts: int = W0, cat: bool = False):
    r = h0.Round  # dataclass
    fields = {f for f in r.__dataclass_fields__}
    kwargs = dict(
        event_ts=ts,
        side=-1,
        entry_ts=ts + 3600,
        entry_price=Decimal("100"),
        exit_ts=ts + 3600,
        exit_price=Decimal("100"),
        exit_reason="catastrophe" if cat else "time",
        net_usd=Decimal(net_pct) * Decimal(10000),
        net_pct=Decimal(net_pct),
    )
    return r(**{k: v for k, v in kwargs.items() if k in fields})


def test_ha_requires_mean_net_at_least_030pct(h0):
    low = [make_round(h0, "0.0029") for _ in range(30)]
    mid = [make_round(h0, "0.0030") for _ in range(30)]
    assert not h0.evaluate_cell(low, k=6).gates["H-a"]
    assert h0.evaluate_cell(mid, k=6).gates["H-a"]


def test_hb_requires_annualized_3pct(h0):
    # ann = sum(net$)/CAPITAL*365/60: 750$ → 7.5 % за 60d → 45.6 % годовых ≥ 3 %
    r1 = [make_round(h0, "0.0025") for _ in range(30)]
    assert h0.evaluate_cell(r1, k=6).gates["H-b"]
    # 5 кругов по 0.08 % = 40$ → 0.4 % за 60d → 2.43 % годовых < 3 %
    r2 = [make_round(h0, "0.0008") for _ in range(5)]
    assert not h0.evaluate_cell(r2, k=6).gates["H-b"]


def test_hc_requires_three_rounds_and_nonneg_sum_per_third(h0):
    rounds = []
    for j in range(30):
        rounds.append(make_round(h0, "0.004", ts=W0 + 3600 * j))
    for j in range(30):
        rounds.append(make_round(h0, "0.004", ts=W0 + 20 * 86400 + 3600 * j))
    # треть 3 пуста по кругу → H-c падает, хотя H-a держится на пуле
    cell = h0.evaluate_cell(rounds, k=6)
    assert not cell.gates["H-c"]
    # теперь добавим во вторую треть два круга −0.1 % и ноль положительного баланса нет…
    t3 = [make_round(h0, "-0.001", ts=W0 + 40 * 86400 + 3600 * j) for j in range(3)]
    cell = h0.evaluate_cell(rounds + t3, k=6)
    assert not cell.gates["H-c"]  # nq=3 есть, но сумма < 0
    # и честный прохожий вариант
    t3ok = [make_round(h0, "0.001", ts=W0 + 40 * 86400 + 3600 * j) for j in range(3)]
    cell = h0.evaluate_cell(rounds + t3ok, k=6)
    assert cell.gates["H-c"]


def test_hd_requires_at_least_30_rounds(h0):
    r29 = [make_round(h0, "0.004", ts=W0 + 3600 * (j % 2)) for j in range(29)]
    r30 = r29 + [make_round(h0, "0.004", ts=W0 + 7200)]
    assert not h0.evaluate_cell(r29, k=6).gates["H-d"]
    assert h0.evaluate_cell(r30, k=6).gates["H-d"]


def test_he_allows_at_most_two_catastrophes(h0):
    rounds = [make_round(h0, "0.004", ts=W0 + 3600) for _ in range(30)]
    assert h0.evaluate_cell(rounds, k=6).gates["H-e"]
    bad = [
        make_round(h0, "0.004", ts=W0 + 3600, cat=(j < 3)) for j in range(30)
    ]
    assert not h0.evaluate_cell(bad, k=6).gates["H-e"]
    two = [make_round(h0, "0.004", ts=W0 + 3600, cat=(j < 2)) for j in range(30)]
    assert h0.evaluate_cell(two, k=6).gates["H-e"]


def test_cell_live_only_when_all_gates_pass(h0):
    good = [make_round(h0, "0.004", ts=W0 + 20 * 86400 * (j % 3) + 3600 * j) for j in range(33)]
    cell = h0.evaluate_cell(good, k=6)
    assert cell.live and all(cell.gates.values())
    # один провал (H-e) делает клетку мёртвой
    bad = [make_round(h0, "0.004", ts=W0 + 20 * 86400 * (j % 3) + 3600 * j, cat=(j < 3)) for j in range(33)]
    assert not h0.evaluate_cell(bad, k=6).live


def test_series_verdict_requires_a_live_cell(h0):
    good = [make_round(h0, "0.004", ts=W0 + 20 * 86400 * (j % 3) + 3600 * j) for j in range(33)]
    dead = [make_round(h0, "-0.004", ts=W0) for _ in range(30)]
    assert h0.series_verdict({6: good, 12: dead}).startswith("GO")
    assert h0.series_verdict({6: dead, 12: dead}).startswith("NO-GO")


# -------------------------------------------------------------- гварды и данные


def test_guard_refuses_before_window_close(h0):
    with pytest.raises(h0.GuardError):
        h0.guard(W1 - 1)


def test_guard_allows_after_window_close(h0):
    h0.guard(W1)
    h0.guard(W1 + 86400)


def test_coverage_requires_1440_contiguous_hourly_bars(h0):
    ok = flat(1440)
    h0.check_coverage(ok)  # не бросает
    with pytest.raises(h0.DataNotReady):
        h0.check_coverage(flat(1439))
    with pytest.raises(h0.DataNotReady):
        with_gap = flat(1440)
        with_gap.pop(5)
        with_gap.append(bar(W0 + 3600 * 1440, "100", "100.5", "99.5", "100", "1000"))
        h0.check_coverage(with_gap)
    with pytest.raises(h0.DataNotReady):
        h0.check_coverage(flat(1440, start_ts=W0 + 60))  # не выровнено по окну


# ------------------------------------------------------------------ отчёт


def test_report_bytes_are_deterministic(h0):
    bars = h0.synthetic_window(seed=42)
    r1 = h0.build_report(h0.run_cells(bars), synthetic=True)
    r2 = h0.build_report(h0.run_cells(bars), synthetic=True)
    assert r1 == r2
    assert hashlib.sha256(r1.encode("utf-8")).hexdigest() == hashlib.sha256(
        r2.encode("utf-8")
    ).hexdigest()
    assert "SYNTHETIC" in r1
    assert "k=6" in r1 and "k=12" in r1
    assert "VERDICT" in r1


def test_report_is_stable_across_now_values(h0):
    bars = h0.synthetic_window(seed=7)
    a = h0.build_report(h0.run_cells(bars), synthetic=True)
    b = h0.build_report(h0.run_cells(h0.synthetic_window(seed=7)), synthetic=True)
    assert a == b


def test_synthetic_window_is_full_coverage(h0):
    bars = h0.synthetic_window(seed=1)
    assert len(bars) == 1440
    assert bars[0].ts == W0
    assert bars[-1].ts == W0 + 3600 * 1439
    h0.check_coverage(bars)  # годится как покрытие окна


def test_run_cells_returns_both_frozen_k(h0):
    out = h0.run_cells(h0.synthetic_window(seed=3))
    assert set(out) == {6, 12}
