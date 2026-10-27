"""Общие индикаторы и блоки агрегации, на которых стоят стратегии пакета.

Все вычисления — на Decimal, float не участвует. Сюда вынесено то, что нужно
сразу нескольким стратегиям, чтобы формула одного индикатора жила в одном
месте и стратегии не расходились в её определении:

- сглаживание Уайлдера (`Rma`) и построенные на нём EMA, ATR и ADX;
- агрегатор таймфреймов (1m→15m→5H): якорь по эпохе, полнота группы
  обязательна;
- потоковая линейная регрессия по окну Decimal-значений (`LinearRegression`);
- точка канала для экспорта сцены (`ChannelPoint`);
- валидаторы параметров конструктора (`positive`, `positive_int`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from strategylab.core.bars import Bar

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

# Окна агрегации: 1H из 60 минутных баров, 15m из 15 минутных баров,
# 5H из 20 пятнадцатиминутных.
ONE_HOUR_WIDTH = 3600
ONE_HOUR_COUNT = 60
FIFTEEN_MIN_WIDTH = 900
FIFTEEN_MIN_COUNT = 15
FIVE_HOURS_WIDTH = 18000
FIVE_HOURS_COUNT = 20


class Rma:
    """Сглаживание Уайлдера (RMA) на Decimal.

    Сид — среднее первых `n` значений; до сида индикатор не готов и в
    расчёты не попадает. Дальше RMA = (prev * (n - 1) + value) / n.
    """

    def __init__(self, n: int) -> None:
        self._n = n
        self._sum = _ZERO
        self._count = 0
        self._value: Decimal | None = None

    def update(self, value: Decimal) -> Decimal | None:
        if self._value is None:
            self._sum += value
            self._count += 1
            if self._count < self._n:
                return None
            self._value = self._sum / Decimal(self._n)
            return self._value
        self._value = (self._value * Decimal(self._n - 1) + value) / Decimal(self._n)
        return self._value


class Ema:
    """EMA с коэффициентом 2/(n + 1). Сид — первое значение."""

    def __init__(self, n: int) -> None:
        self._alpha = Decimal(2) / Decimal(n + 1)
        self._value: Decimal | None = None

    def update(self, value: Decimal) -> Decimal | None:
        if self._value is None:
            self._value = value
            return self._value
        self._value += self._alpha * (value - self._value)
        return self._value


class Atr:
    """ATR Уайлдера: true range за бар, затем RMA по длине `n`."""

    def __init__(self, n: int) -> None:
        self._rma = Rma(n)
        self._prev: tuple[Decimal, Decimal, Decimal] | None = None

    def update(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal | None:
        prev = self._prev
        self._prev = (high, low, close)
        if prev is None:
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - prev[2]),
                abs(low - prev[2]),
            )
        return self._rma.update(tr)


class Adx:
    """ADX Уайлдера: сглаженные +DI/-DI, из них DX и сглаженный DX.

    Готов после 2n - 1 баров: n баров на сид сглаживаний DI, потом n значений
    DX на сид ADX.
    """

    def __init__(self, n: int) -> None:
        self._plus = Rma(n)
        self._minus = Rma(n)
        self._tr = Rma(n)
        self._adx = Rma(n)
        self._prev: tuple[Decimal, Decimal, Decimal] | None = None

    def update(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal | None:
        prev = self._prev
        self._prev = (high, low, close)
        if prev is None:
            plus_dm = _ZERO
            minus_dm = _ZERO
            tr = high - low
        else:
            up = high - prev[0]
            down = prev[1] - low
            plus_dm = up if up > down and up > 0 else _ZERO
            minus_dm = down if down > up and down > 0 else _ZERO
            tr = max(
                high - low,
                abs(high - prev[2]),
                abs(low - prev[2]),
            )
        smoothed_plus = self._plus.update(plus_dm)
        smoothed_minus = self._minus.update(minus_dm)
        smoothed_tr = self._tr.update(tr)
        if smoothed_plus is None or smoothed_minus is None or smoothed_tr is None:
            return None
        if smoothed_tr == 0:
            return None
        plus_di = _HUNDRED * smoothed_plus / smoothed_tr
        minus_di = _HUNDRED * smoothed_minus / smoothed_tr
        total = plus_di + minus_di
        if total == 0:
            dx = _ZERO
        else:
            dx = _HUNDRED * abs(plus_di - minus_di) / total
        return self._adx.update(dx)


@dataclass(frozen=True, slots=True)
class ChannelPoint:
    """Канал на закрытом 15m-баре: ts и три границы.

    Общая форма для стратегий с каналом: точка несёт среднюю линию и обе
    границы последнего закрытого 5H-бара. Между закрытиями 5H значения не
    меняются: канал — ступенька, а не линия. Экспорт сцены берёт эти точки
    из стратегии (`channel_log`), а не пересчитывает заново: пересчёт показал
    бы другой канал, если бы индикаторы считались иначе.
    """

    ts: int
    mid: Decimal
    upper: Decimal
    lower: Decimal


@dataclass(frozen=True, slots=True)
class Regression:
    """Регрессия окна: линия, отклонение цен от неё и значение на последнем баре.

    `slope` и `intercept` — линия Middle(t) = slope·t + intercept, где t —
    отсчёт бара внутри окна (0..n-1); `sigma` — стандартное отклонение цен от
    линии на том же окне; `mid` — значение линии на последнем баре окна
    (t = n-1): канал трекает текущий уровень, а не центр окна.
    """

    slope: Decimal
    intercept: Decimal
    sigma: Decimal
    mid: Decimal


class LinearRegression:
    """Линейная регрессия по последним `n` значениям: накапливаемые суммы.

    Суммы Σt·c, Σc, Σc², Σt, Σt² ведутся инкрементально по формулам сумм:
    при сдвиге полного окна пересчёт индексов сводится к арифметике сумм
    без обхода окна — sxy' = sxy − Сумма_c + c0 + (n−1)·c_new, где c0 —
    ушедший старший бар. Наклон и пересечение — закрытые формулы сумм:

        a = (n·Σt·c − Σt·Σc) / (n·Σt² − (Σt)²),  b = (Σc − a·Σt) / n.

    σ — стандартное отклонение цен от линии на том же окне: один проход по
    окну за закрытый бар, корень — Decimal.sqrt. Деления округляются до
    точности контекста (по умолчанию 28 значащих цифр); на масштабе цен это
    неощутимо, float всё равно не участвует.

    До полного окна регрессия не готова — update возвращает None.
    """

    def __init__(self, n: int) -> None:
        if n < 2:
            raise ValueError(f"окно регрессии должно быть >= 2, получено {n}")
        self._n = n
        self._closes: deque[Decimal] = deque()
        self._s_x = _ZERO
        self._s_x2 = _ZERO
        self._s_xy = _ZERO
        self._s_c = _ZERO
        self._s_cc = _ZERO

    def update(self, close: Decimal) -> Regression | None:
        n = self._n
        closes = self._closes
        if len(closes) == n:
            dropped = closes.popleft()
            s_c_old = self._s_c
            self._s_c = s_c_old - dropped + close
            self._s_xy = self._s_xy - s_c_old + dropped + Decimal(n - 1) * close
            self._s_cc = self._s_cc - dropped * dropped + close * close
            closes.append(close)
        else:
            t = Decimal(len(closes))
            self._s_x += t
            self._s_x2 += t * t
            self._s_xy += t * close
            self._s_c += close
            self._s_cc += close * close
            closes.append(close)
            if len(closes) < n:
                return None
        slope = (Decimal(n) * self._s_xy - self._s_x * self._s_c) / (
            Decimal(n) * self._s_x2 - self._s_x * self._s_x
        )
        intercept = (self._s_c - slope * self._s_x) / Decimal(n)
        mid = slope * Decimal(n - 1) + intercept
        sigma2 = _ZERO
        for t_index, value in enumerate(closes):
            residual = value - (slope * Decimal(t_index) + intercept)
            sigma2 += residual * residual
        sigma2 /= Decimal(n)
        sigma = _ZERO if sigma2 <= 0 else sigma2.sqrt()
        return Regression(slope=slope, intercept=intercept, sigma=sigma, mid=mid)


class Aggregator:
    """Бар интервала из баров меньшего интервала: якорь по эпохе, полнота обязательна.

    Окно шириной `width` секунд собирается из `count` баров с шагом
    `width // count` секунд. Бар возвращается, только когда окно собрано целиком
    и бары шли подряд; неполное окно не собирается никогда, а пропуск внутри
    окна убивает его целиком.
    """

    def __init__(self, width: int, count: int) -> None:
        self._width = width
        self._count = count
        self._step = width // count
        self._start = -1
        self._filled = 0
        self._broken = False
        self._open = _ZERO
        self._high = _ZERO
        self._low = _ZERO
        self._close = _ZERO
        self._volume = _ZERO

    def update(self, bar: Bar) -> Bar | None:
        start = bar.ts - bar.ts % self._width
        if start != self._start:
            self._start = start
            self._filled = 0
            self._broken = False
            self._reset_values()
        if self._broken:
            return None
        if bar.ts != self._start + self._filled * self._step:
            # Окно началось не с первой минуты или минуты идут с пропуском:
            # целиком оно уже не соберётся.
            if bar.ts != self._start:
                self._broken = True
                return None
            self._filled = 0
            self._reset_values()
        if self._filled == 0:
            self._open = bar.open
            self._high = bar.high
            self._low = bar.low
        else:
            self._high = max(self._high, bar.high)
            self._low = min(self._low, bar.low)
        self._close = bar.close
        self._volume += bar.volume
        self._filled += 1
        if self._filled < self._count:
            return None
        result = Bar(
            ts=self._start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )
        self._filled = 0
        self._reset_values()
        return result

    def _reset_values(self) -> None:
        self._open = _ZERO
        self._high = _ZERO
        self._low = _ZERO
        self._close = _ZERO
        self._volume = _ZERO


def positive(value: Decimal, name: str) -> Decimal:
    """Параметр должен быть > 0."""
    if value <= 0:
        raise ValueError(f"{name} должен быть > 0, получено {value}")
    return value


def positive_int(value: Decimal, name: str) -> int:
    """Параметр должен быть целым и > 0."""
    positive(value, name)
    if value != value.to_integral_value():
        raise ValueError(f"{name} должен быть целым, получено {value}")
    return int(value)
