"""ТРЕНД: лонг-only покупка отката к нижней границе трендового канала ADX-тренда.

Спека: strategydocs/Qwen_markdown_20261022_kav784jho.md, стратегия 2.

Поток данных: стратегия получает 1m-бары и сама агрегирует 15m- и 5H-бары.
Агрегация ведётся якорем по эпохе (`ts - ts % width`), поэтому окна совпадают с
биржевыми. Группа участвует в расчёте только целиком: неполная группа (пропуск
минут, обрыв окна) не собирается никогда и в индикаторы не попадает.

Режим (5H): ADX(adx_len) > adx_min — тренд есть, и наклон a линейной регрессии
по закрытиям последних `lr_len` 5H-баров положителен.
Канал (5H): Middle = a·t + b (линия регрессии, t = 0..n-1),
Upper/Lower = Middle ± sigma_mult·σ, где σ — стандартное отклонение цен от линии
на том же окне. Middle берётся на последнем баре окна (t = n-1).
Вход (на закрытии 15m-бара): Low15 <= Lower5H (откат в зону поддержки тренда),
Low15 > Low_prev15 (Higher Low) и High15 > High_prev15 (слом микро-структуры).
Выход: SL = SwingLow - stop_atr * ATR15m (STOP_MARKET), TP = Entry +
tp_mult * (Entry - SL) (LIMIT), оба reduce_only. Размер:
qty = equity * risk_frac / (Entry - SL): риск на сделку — ровно risk_frac
капитала, расстояние до стопа — риск-единица.

Решения, которые здесь зафиксированы
-----------------------------------

1. Пакет индикаторов — общий со ФЛЭТом (`strategylab.strategy.indicators`):
   ADX сглаживаниями Уайлдера (RMA), ATR15m — тоже RMA Уайлдера из него же,
   агрегатор таймфреймов тот же. Формула одного индикатора живёт в одном месте,
   и стратегии не расходятся в её определении. Все вычисления на Decimal,
   float не участвует.

2. Экспонента Херста (H > 0.5) не реализована, как и во ФЛЭТе: оценка H требует
   своей методологии (окна, способ оценки), а в задании режим определён
   порогом ADX и наклоном регрессии. Всё, что не перечислено выше, — вне
   объёма.

3. Линия регрессии — потоковая линейная регрессия по закрытиям 5H-баров:
   суммы ведутся инкрементально по формулам сумм, наклон и пересечение —
   закрытые формулы; σ считается по population-определению (деление на n),
   корень — Decimal.sqrt, отрицательная величина из-за округлений (σ² <= 0)
   обращается в ноль. Средняя — значение линии на t = n-1: канал трекает
   текущий уровень тренда, а не центр окна.

4. Цена входа в момент выставления брекета — оценка, как во ФЛЭТе (решение 4
   там): движок не отдаёт цену исполнения, поэтому брекет ставится на баре,
   где вход уже исполнен, и за цену входа берётся close этого бара. Ошибка
   оценки — одно проскальзывание против tp_mult-кратного риска, то есть доли
   процента. При сайзинге (на сигнальном баре) за вход берётся close
   сигнального 15m-бара — MARKET-ордер выставлен по его закрытию.

5. SwingLow и ATR15m замораживаются на сигнальном баре: стоп и размер обязаны
   быть теми, по которым принято решение входить, а не пересчитываться по
   состоянию к бару исполнения. SL = SwingLow - stop_atr * ATR15m; если
   расстояние Entry - SL оказывается неположительным (структура ниже входа),
   сделку отменяют: риск-сайзинг с отрицательным знаменателем не имеет смысла.

6. Семантика стоп-ордеров в этом движке. reduce_only заявка не исполняется
   как обычный ордер: стоп и цель зеркалятся в позицию, выход происходит на
   уровне позиции в начале следующего бара — до лимитов и маркетов. Следствия
   (подробно во ФЛЭТе, решение 5): стоп срабатывает на баре после постановки
   брекета; при достижении обоих уровней на одном баре решает внутрибазовый
   порядок; закрытие позиции снимает покоящиеся reduce_only заявки, поэтому
   двойного выхода не бывает; reduce_only нельзя поставить при плоской
   позиции — брекет ставится на баре после исполнения входа, и его размер
   равен размеру позиции.

7. Капитал берётся из `StrategyState.equity` — накопленный реализованный P&L:
   у прогона без стартового капитала equity = 0, размер вышел бы нулевым, и
   стратегия не открывает позицию вовсе.

8. Канал отдаётся наружу по запросу: `enable_channel_log` включает запись
   состояния канала по 15m-барам, `channel_log` отдаёт её. Запись выключена по
   умолчанию и на решения стратегии не влияет. Экспорт сцены берёт канал
   отсюда: пересчёт показал бы другой канал, если бы регрессия считалась
   иначе, и экспорт врал бы о том, по чему торговали. Точка несёт среднюю
   линию и границы ±sigma_mult·σ последнего закрытого 5H-бара: между
   закрытиями 5H канал — ступенька.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState
from strategylab.strategy.indicators import (
    FIFTEEN_MIN_COUNT,
    FIFTEEN_MIN_WIDTH,
    FIVE_HOURS_COUNT,
    FIVE_HOURS_WIDTH,
    Adx,
    Aggregator,
    Atr,
    ChannelPoint,
    LinearRegression,
    positive,
    positive_int,
)

_ONE = Decimal("1")


class TrendPullbackStrategy:
    """ТРЕНД: лонг на откате к нижней границе трендового канала регрессии.

    Сигнал читается на закрытии 15m-бара, вход — MARKET-ордером, выходы —
    reduce_only-брекетом (SL под структурным минимумом, TP на tp_mult-кратном
    риске). Пока позиция открыта, новых входов нет.
    """

    name = "trend_pullback"

    PARAM_NAMES = (
        "adx_len",
        "adx_min",
        "lr_len",
        "sigma_mult",
        "atr_len",
        "swing_len",
        "stop_atr",
        "tp_mult",
        "risk_frac",
    )

    def __init__(
        self,
        adx_len: Decimal = Decimal("14"),
        adx_min: Decimal = Decimal("25"),
        lr_len: Decimal = Decimal("50"),
        sigma_mult: Decimal = Decimal("2"),
        atr_len: Decimal = Decimal("14"),
        swing_len: Decimal = Decimal("10"),
        stop_atr: Decimal = Decimal("1.0"),
        tp_mult: Decimal = Decimal("2.5"),
        risk_frac: Decimal = Decimal("0.015"),
    ) -> None:
        self._adx_len = positive_int(adx_len, "adx_len")
        self._adx_min = positive(adx_min, "adx_min")
        self._lr_len = positive_int(lr_len, "lr_len")
        if self._lr_len < 2:
            raise ValueError(f"lr_len должен быть >= 2, получено {self._lr_len}")
        self._sigma_mult = positive(sigma_mult, "sigma_mult")
        self._atr_len = positive_int(atr_len, "atr_len")
        self._swing_len = positive_int(swing_len, "swing_len")
        self._stop_atr = positive(stop_atr, "stop_atr")
        self._tp_mult = positive(tp_mult, "tp_mult")
        self._risk_frac = positive(risk_frac, "risk_frac")
        if self._risk_frac > _ONE:
            raise ValueError(f"risk_frac должен быть <= 1, получено {risk_frac}")

        self._m15 = Aggregator(FIFTEEN_MIN_WIDTH, FIFTEEN_MIN_COUNT)
        self._m5h = Aggregator(FIVE_HOURS_WIDTH, FIVE_HOURS_COUNT)
        self._atr15 = Atr(self._atr_len)
        self._lr = LinearRegression(self._lr_len)
        self._adx5h = Adx(self._adx_len)
        self._swing: deque[Decimal] = deque(maxlen=self._swing_len)

        self._mid: Decimal | None = None
        self._upper: Decimal | None = None
        self._lower: Decimal | None = None
        self._slope: Decimal | None = None
        self._adx: Decimal | None = None
        self._atr15_value: Decimal | None = None
        self._prev15: Bar | None = None
        self._entry_pending = False
        self._entry_stop: Decimal | None = None
        self._channel_log: list[ChannelPoint] | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> TrendPullbackStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры trend_pullback: {unknown}")
        kwargs = {name: params[name] for name in cls.PARAM_NAMES if name in params}
        return cls(**kwargs)

    @property
    def last_adx(self) -> Decimal | None:
        """ADX последнего закрытого 5H-бара: режим смотрит в 5H, не в 15m."""
        return self._adx

    @property
    def last_slope(self) -> Decimal | None:
        """Наклон линии регрессии последнего закрытого 5H-бара."""
        return self._slope

    @property
    def last_lower(self) -> Decimal | None:
        """Нижняя граница канала регрессии последнего закрытого 5H-бара."""
        return self._lower

    @property
    def last_atr15m(self) -> Decimal | None:
        """ATR последнего закрытого 15m-бара: из него стоп и размер."""
        return self._atr15_value

    def enable_channel_log(self) -> None:
        """Включает запись состояния канала по 15m-барам.

        Запись выключена по умолчанию: без неё стратегия считает ровно то же
        самое, только ничего не запоминает. Включать надо до прогона — журнал
        наполняется по ходу баров.
        """
        if self._channel_log is None:
            self._channel_log = []

    @property
    def channel_log(self) -> tuple[ChannelPoint, ...]:
        """Состояния канала по 15m-барам: пусто, если запись не включалась.

        Точка появляется на каждом закрытом 15m-баре, на котором канал уже
        посчитан: до первого готового 5H-бара канала нет, и точки нет тоже.
        """
        if self._channel_log is None:
            return ()
        return tuple(self._channel_log)

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Один 1m-бар: обновить агрегаты, повесить брекет, поискать вход."""
        orders: list[Order] = []
        bar15 = self._m15.update(bar)
        if bar15 is not None:
            self._update_15m(bar15)
            bar5h = self._m5h.update(bar15)
            if bar5h is not None:
                self._update_5h(bar5h)
            self._log_channel(bar15)
            if self._is_signal(bar15, state):
                entry = self._entry_order(bar15, state)
                if entry is not None:
                    orders.append(entry)
            self._prev15 = bar15
        if self._entry_pending and state.position > 0:
            # Вход исполнен на этом баре: цена входа оценивается его закрытием.
            self._entry_pending = False
            orders.extend(self._bracket_orders(bar, state))
        return orders

    def _update_15m(self, bar15: Bar) -> None:
        self._swing.append(bar15.low)
        atr = self._atr15.update(bar15.high, bar15.low, bar15.close)
        if atr is not None:
            self._atr15_value = atr

    def _update_5h(self, bar5h: Bar) -> None:
        regression = self._lr.update(bar5h.close)
        adx = self._adx5h.update(bar5h.high, bar5h.low, bar5h.close)
        if adx is None or regression is None:
            return
        self._slope = regression.slope
        self._mid = regression.mid
        self._upper = self._mid + self._sigma_mult * regression.sigma
        self._lower = self._mid - self._sigma_mult * regression.sigma
        self._adx = adx

    def _log_channel(self, bar15: Bar) -> None:
        """Точка канала на закрытом 15m-баре: значения последнего закрытого 5H-бара.

        Точка снимается после обновления 5H: сигнал на этом 15m-баре решался
        уже по обновлённому каналу, и экспорт должен показывать тот же.
        """
        log = self._channel_log
        if log is None:
            return
        if self._mid is None or self._upper is None or self._lower is None:
            return
        log.append(
            ChannelPoint(
                ts=bar15.ts,
                mid=self._mid,
                upper=self._upper,
                lower=self._lower,
            )
        )

    def _is_signal(self, bar15: Bar, state: StrategyState) -> bool:
        """Режим тренда, откат к нижней границе, higher low и слом хаёв.

        Сигнальный бар — текущий закрытый 15m-бар, сравнивается с предыдущим
        закрытым 15m-баром. Канал — последний закрытый 5H-бар: внутри окна 5H
        нижняя граница — ступенька, откат меряется к ней.
        """
        if self._lower is None or self._slope is None or self._adx is None:
            return False
        if self._slope <= 0 or self._adx <= self._adx_min:
            return False
        if state.position != 0 or self._entry_pending:
            return False
        prev = self._prev15
        if prev is None:
            return False
        if bar15.low > self._lower:
            return False
        return bar15.low > prev.low and bar15.high > prev.high

    def _entry_order(self, bar15: Bar, state: StrategyState) -> Order | None:
        """MARKET-вход: размер по риску risk_frac капитала и расстоянию до стопа.

        Стоп замораживается на сигнальном баре: SwingLow — минимум lows
        последних `swing_len` 15m-баров, включая сигнальный, плюс
        stop_atr * ATR15m под ним. Сайзинг ведётся по оценки входа = close
        сигнального бара; знаменатель — Entry - SL. None — входить нечем:
        ATR не готов, стоп не ниже оценки входа или капитал не положителен.
        """
        atr = self._atr15_value
        if atr is None or atr <= 0 or state.equity <= 0:
            return None
        swing_low = min(self._swing)
        entry = bar15.close
        stop = swing_low - self._stop_atr * atr
        risk = entry - stop
        if risk <= 0:
            return None
        self._entry_pending = True
        self._entry_stop = stop
        qty = state.equity * self._risk_frac / risk
        return Order(Side.BUY, OrderKind.MARKET, qty, None, None, False)

    def _bracket_orders(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Брекет по открытой позиции: стоп под структурой, цель tp_mult-кратного риска.

        Цена входа оценивается как close бара, на котором вход исполнен
        (MARKET исполняется по закрытию бара плюс проскальзывание), стоп —
        замороженный с сигнального бара.
        """
        stop = self._entry_stop
        if stop is None:
            return []
        entry = bar.close
        qty = state.position
        risk = entry - stop
        if risk <= 0:
            return []
        target = entry + self._tp_mult * risk
        return [
            Order(Side.SELL, OrderKind.STOP_MARKET, qty, None, stop, True),
            Order(Side.SELL, OrderKind.LIMIT, qty, target, None, True),
        ]
