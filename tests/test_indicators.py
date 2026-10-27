"""Тесты общего модуля индикаторов: Decimal-арифметика, слайд окна, агрегаторы.

Линейная регрессия сверяется с прямым пересчётом по тем же закрытым формулам:
значения небольшие, все промежуточные суммы укладываются в точность контекста,
поэтому равенство значений — точное, а слайд окна проверяется тем же прогоном.
RMA/EMA/ATR/ADX проверяются на коротких сериях с ручным расчётом, агрегатор —
на полноте и разрыве окна.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from strategylab.core.bars import Bar
from strategylab.strategy.indicators import (
    Adx,
    Aggregator,
    Atr,
    Ema,
    LinearRegression,
    Regression,
    Rma,
)

ONE = Decimal("1")
START = 1_717_200_000  # кратен и 900, и 18000: окно открывается ровно на START
FIFTEEN_MIN_WIDTH = 900
FIVE_HOURS_WIDTH = 18000


# Линейная регрессия


def _direct_regression(closes: list[Decimal]) -> Regression:
    """Прямой пересчёт по тем же формулам сумм, что и в потоке."""
    n = len(closes)
    s_x = sum((Decimal(t) for t in range(n)), Decimal("0"))
    s_x2 = sum((Decimal(t) * Decimal(t) for t in range(n)), Decimal("0"))
    s_xy = sum((Decimal(t) * close for t, close in enumerate(closes)), Decimal("0"))
    s_c = sum(closes, Decimal("0"))
    slope = (Decimal(n) * s_xy - s_x * s_c) / (Decimal(n) * s_x2 - s_x * s_x)
    intercept = (s_c - slope * s_x) / Decimal(n)
    mid = slope * Decimal(n - 1) + intercept
    sigma2 = Decimal("0")
    for t, close in enumerate(closes):
        residual = close - (slope * Decimal(t) + intercept)
        sigma2 += residual * residual
    sigma2 /= Decimal(n)
    sigma = Decimal("0") if sigma2 <= 0 else sigma2.sqrt()
    return Regression(slope=slope, intercept=intercept, sigma=sigma, mid=mid)


def test_linear_regression_matches_direct_recomputation() -> None:
    """Каждое закрытое окно совпадает с прямым пересчётом по его барам.

    Серия не линейна: наклон, пересечение и σ вычисляются, а не подставляются.
    Окно при этом сдвигается (слайд по полным формулам сумм), и каждый сдвиг
    тоже сверяется, включая окно, съехавшее на новый старший бар.
    """
    closes = [
        Decimal("10.5"),
        Decimal("9.8"),
        Decimal("12.1"),
        Decimal("11.4"),
        Decimal("13.9"),
        Decimal("12.5"),
        Decimal("8.2"),
        Decimal("10.7"),
    ]
    lr = LinearRegression(4)

    results = [lr.update(close) for close in closes]

    assert results[:3] == [None, None, None]
    for index in range(3, len(closes)):
        expected = _direct_regression(closes[index - 3 : index + 1])
        assert results[index] is not None
        assert results[index] == expected


def test_linear_regression_returns_none_before_the_window_is_full() -> None:
    lr = LinearRegression(2)

    assert lr.update(Decimal("1")) is None


def test_linear_regression_fits_two_points_exactly() -> None:
    """Две точки: линия проходит через обе, σ = 0, средняя — вторая точка."""
    lr = LinearRegression(2)

    assert lr.update(Decimal("1")) is None
    result = lr.update(Decimal("3"))

    assert result == _direct_regression([Decimal("1"), Decimal("3")])
    assert result.slope == Decimal("2")
    assert result.intercept == Decimal("1")
    assert result.sigma == Decimal("0")
    assert result.mid == Decimal("3")


def test_linear_regression_rejects_a_window_shorter_than_two() -> None:
    with pytest.raises(ValueError, match=">= 2"):
        LinearRegression(1)


# Сглаживания Уайлдера и построенные на них индикаторы


def test_rma_seeds_with_the_mean_then_smooths() -> None:
    rma = Rma(3)

    assert rma.update(Decimal("1")) is None
    assert rma.update(Decimal("2")) is None
    assert rma.update(Decimal("3")) == Decimal("2")
    # (2 * 2 + 4) / 3
    assert rma.update(Decimal("4")) == (Decimal("2") * Decimal("2") + Decimal("4")) / Decimal("3")


def test_ema_seeds_with_the_first_value() -> None:
    ema = Ema(3)  # альфа 2/(3+1) = 0.5 — точный Decimal

    assert ema.update(Decimal("10")) == Decimal("10")
    assert ema.update(Decimal("20")) == Decimal("15")
    assert ema.update(Decimal("0")) == Decimal("7.5")


def test_ema_with_nine_is_exact_decimal() -> None:
    ema = Ema(9)  # альфа 0.2

    assert ema.update(Decimal("10")) == Decimal("10")
    assert ema.update(Decimal("20")) == Decimal("12")


def test_atr_is_true_range_smoothed_by_rma() -> None:
    atr = Atr(2)

    assert atr.update(Decimal("102"), Decimal("100"), Decimal("101")) is None
    # TR = max(2, |103−101|, |101−101|) = 2 → сид (2+2)/2
    assert atr.update(Decimal("103"), Decimal("101"), Decimal("102")) == Decimal("2")
    # TR = max(6, |105−102|, |99−102|) = 6 → (2 + 6)/2 = 4
    assert atr.update(Decimal("105"), Decimal("99"), Decimal("101")) == Decimal("4")


def test_adx_on_a_pure_uptrend_reaches_a_hundred() -> None:
    """Целый ход вверх: +DM = 2, −DM = 0, TR = 10 → DX = 100 и ADX = 100."""
    adx = Adx(3)
    bars = [
        (Decimal("110"), Decimal("100"), Decimal("105")),
        (Decimal("112"), Decimal("102"), Decimal("107")),
        (Decimal("114"), Decimal("104"), Decimal("109")),
        (Decimal("116"), Decimal("106"), Decimal("111")),
        (Decimal("118"), Decimal("108"), Decimal("113")),
    ]

    results = [adx.update(high, low, close) for high, low, close in bars]

    assert results[:4] == [None, None, None, None]
    assert results[4] == Decimal("100")


# Агрегатор: якорь по эпохе, полнота группы обязательна


def bar(ts: int, open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> Bar:
    return Bar(ts=ts, open=open_, high=high, low=low, close=close, volume=ONE)


def _minute(i: int, high: Decimal, low: Decimal, close: Decimal) -> Bar:
    """Минутный бар окна 15m с явными диапазонами."""
    return bar(START + 60 * i, Decimal("100"), high, low, close)


def flat_minute(i: int) -> Bar:
    return _minute(i, Decimal("100"), Decimal("99"), Decimal("100"))


def test_aggregator_collects_a_complete_window() -> None:
    """15 минут подряд → бар с якорем окна и агрегированными OHLCV."""
    agg = Aggregator(FIFTEEN_MIN_WIDTH, 15)
    bars = [
        _minute(
            i,
            Decimal("100") + i,
            Decimal("99") + i,
            Decimal("102") if i == 14 else Decimal("100"),
        )
        for i in range(15)
    ]

    results = [agg.update(m) for m in bars]

    assert results[:14] == [None] * 14
    result = results[14]
    assert result is not None
    assert result.ts == START
    assert result.open == Decimal("100")
    assert result.high == Decimal("114")
    assert result.low == Decimal("99")
    assert result.close == Decimal("102")
    assert result.volume == Decimal("15")


def test_aggregator_discards_an_incomplete_window() -> None:
    """14 минут — окна нет; даже позднее закрытие окна не задним числом."""
    agg = Aggregator(FIFTEEN_MIN_WIDTH, 15)

    short = [agg.update(flat_minute(i)) for i in range(14)]
    jump = agg.update(flat_minute(15))
    full = [agg.update(flat_minute(15 + i)) for i in range(15)]

    assert all(value is None for value in short)
    assert jump is None
    assert full[:14] == [None] * 14
    assert full[14] is not None


def test_aggregator_gap_kills_the_window_but_not_the_next_one() -> None:
    """Пропуск минуты внутри окна убивает окно целиком; следующее окно живёт."""
    agg = Aggregator(FIFTEEN_MIN_WIDTH, 15)

    intact = [agg.update(flat_minute(i)) for i in range(15)]
    assert intact[14] is not None

    _ = [agg.update(flat_minute(15 + i)) for i in range(5)]
    gaps = [agg.update(flat_minute(21 + i)) for i in range(10)]
    assert all(value is None for value in gaps)

    fresh = [agg.update(flat_minute(30 + i)) for i in range(15)]
    assert fresh[:14] == [None] * 14
    closed = fresh[14]
    assert closed is not None
    assert closed.ts == START + FIFTEEN_MIN_WIDTH * 2


def test_five_hour_window_aggregates_fifteen_minute_bars() -> None:
    """20 обновлений 15m-барами → 5H-бар с якорем эпохи 18000."""
    agg = Aggregator(FIVE_HOURS_WIDTH, 20)

    results = [
        agg.update(
            bar(
                START + k * FIFTEEN_MIN_WIDTH,
                Decimal("100") + k,
                Decimal("101") + k,
                Decimal("99") - k,
                Decimal("100") + k,
            )
        )
        for k in range(20)
    ]

    assert results[:19] == [None] * 19
    closed = results[19]
    assert closed is not None
    assert closed.ts == START
    assert closed.volume == Decimal("20")
