"""ФЛЭТ: лонг-only mean-reversion от нижней границы канала Кельтнера в ADX-флэте.

Спека: strategydocs/Qwen_markdown_20261022_kav784jho.md, стратегия 1.

Поток данных: стратегия получает 1m-бары и сама агрегирует 15m- и 5H-бары.
Агрегация ведётся якорём по эпохе (`ts - ts % width`), поэтому окна совпадают с
биржевыми. Группа участвует в расчёте только целиком: неполная группа (пропуск
минут, обрыв окна) не собирается никогда и в индикаторы не попадает.

Режим (5H): ADX(14) < 20 — направленного движения нет.
Канал (5H): Lower = EMA(20) - 2 * ATR(20), Upper = EMA(20) + 2 * ATR(20).
Вход (на закрытии 15m-бара): Low15 <= Lower5H и Close15 > Low15 + 0.6 * (High15 - Low15).
Выход: SL = Entry - exit_mult * ATR15m (STOP_MARKET), TP = Entry + exit_mult * ATR15m
(LIMIT), оба reduce_only. Размер: qty = equity * risk_frac / (exit_mult * ATR15m):
риск на сделку — ровно risk_frac капитала, расстояние до стопа — exit_mult * ATR.
Множитель exit_mult — параметр стратегии (по умолчанию 1.5, как в спеке).

Решения, которые здесь зафиксированы
-----------------------------------

1. ATR считается сглаживанием Уайлдера (RMA), а не SMA: RMA — стандарт ATR
   Уайлдера, из которого взяты и ADX, и Кельтнер в оригинальной формуле. EMA
   центральной линии — обычная EMA с коэффициентом 2/(n+1), сид — первое
   значение. Все вычисления на Decimal, float не участвует. Эти блоки вместе с
   агрегатором живут в общем модуле `strategylab.strategy.indicators`, чтобы
   формула одного индикатора не расходилась между стратегиями.

2. Длина ATR15m в документации не задана. Взят стандарт длины Уайлдера —
   14 (`atr_len_exit`); длина ATR канала 5H задана явно — 20 (`atr_len_regime`).

3. Условие экспоненты Херста из спеки (H < 0.5) не реализовано: в задании
   режим определён только ADX-порогом, а оценка H требует своей методологии
   (окна, способ оценки). Всё, что не перечислено выше, — вне объёма.

4. Цена входа в момент выставления брекета — оценка. Движок не отдаёт стратегии
   цену исполнения: `StrategyState` знает только знаковую позицию, капитал и
   номер бара. Поэтому брекет ставится на баре, где вход уже исполнен, и за
   цену входа берётся close этого бара: MARKET-ордер исполняется по закрытию
   бара, то есть реальная цена входа = close * (1 + проскальзывание). Ошибка
   оценки — одно проскальзывание, то есть доли процента от exit_mult * ATR.
   Зафиксированное решение: `entry = close бара исполнения входа`.

5. Семантика стоп-ордеров в этом движке. reduce_only заявка не исполняется
   как обычный ордер: `BrokerSimulator.submit` зеркалит её уровень в позицию
   (`Position.stop_price` для STOP_MARKET, `Position.take_profit` для LIMIT), а
   выход происходит на уровне позиции в начале следующего бара — до лимитов и
   маркетов. Следствия, на которые опирается стратегия:
   - стоп срабатывает на баре после постановки брекета, не на том же;
   - если на одном баре достигнуты и стоп, и цель, решает внутрибазовый порядок
     (`IntrabarOrder`: PESSIMISTIC — стоп, OPTIMISTIC — цель);
   - стоп исполняется по цене бара при гэпе (open), иначе по своему уровню,
     затем применяется проскальзывание; цель исполняется по своему уровню с
     maker-комиссией;
   - закрытие позиции снимает покоящиеся reduce_only заявки, поэтому двойного
     выхода не бывает;
   - reduce_only заявку нельзя поставить при плоской позиции (qty больше
     открытой позиции — ValueError), поэтому брекет ставится на баре после
     исполнения входа, когда позиция уже открыта, и его размер равен размеру
     позиции.

6. Капитал берётся из `StrategyState.equity`. В движке это накопленный
   реализованный P&L: у прогона без стартового капитала equity = 0, размер
   сделки вышел бы нулевым, и стратегия не открывает позицию вовсе.

7. Множитель ATR в стопе и целе — параметр `exit_mult`, а не константа: по
   умолчанию 1.5 (значение из спеки), но менять его можно без правки кода —
   ровно для этого он и вынесен в параметры. Размер позиции считается от того
   же расстояния до стопа (`exit_mult * ATR`), поэтому риск на сделку остаётся
   `risk_frac` капитала при любом значении множителя.

8. Канал отдаётся наружу по запросу: `enable_channel_log` включает запись
   состояния канала по 15m-барам, `channel_log` отдаёт её. Запись выключена по
   умолчанию и на решения стратегии не влияет. Экспорт сцены берёт канал отсюда,
   а не пересчитывает его заново: пересчёт показал бы другой канал, если бы
   индикаторы считались иначе, и экспорт врал бы о том, по чему торговали.
   Точка снимается на каждом закрытом 15m-баре, на котором канал уже посчитан,
   и несёт значения последнего закрытого 5H-бара: между закрытиями 5H канал
   не меняется, и точки образуют ступеньку.
"""

from __future__ import annotations

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
    Ema,
    positive,
    positive_int,
)

_ONE = Decimal("1")


class FlatKeltnerStrategy:
    """ФЛЭТ: лонг от нижней границы Кельтнера, когда по 5H нет тренда.

    Сигнал читается на закрытии 15m-бара, вход — MARKET-ордером, выходы —
    reduce_only-брекетом (SL ниже входа, TP выше). Пока позиция открыта, новых
    входов нет.

    Ширина брекета — `exit_mult * ATR15m`; тот же множитель задаёт размер
    позиции, поэтому риск на сделку равен `risk_frac` капитала.
    """

    name = "flat_keltner"

    PARAM_NAMES = (
        "adx_len",
        "adx_max",
        "ema_len",
        "channel_mult",
        "atr_len_regime",
        "atr_len_exit",
        "exit_mult",
        "risk_frac",
        "reentry_frac",
    )

    def __init__(
        self,
        adx_len: Decimal = Decimal("14"),
        adx_max: Decimal = Decimal("20"),
        ema_len: Decimal = Decimal("20"),
        channel_mult: Decimal = Decimal("2"),
        atr_len_regime: Decimal = Decimal("20"),
        atr_len_exit: Decimal = Decimal("14"),
        exit_mult: Decimal = Decimal("1.5"),
        risk_frac: Decimal = Decimal("0.015"),
        reentry_frac: Decimal = Decimal("0.6"),
    ) -> None:
        self._adx_len = positive_int(adx_len, "adx_len")
        self._adx_max = positive(adx_max, "adx_max")
        self._ema_len = positive_int(ema_len, "ema_len")
        self._channel_mult = positive(channel_mult, "channel_mult")
        self._atr_len_regime = positive_int(atr_len_regime, "atr_len_regime")
        self._atr_len_exit = positive_int(atr_len_exit, "atr_len_exit")
        self._exit_mult = positive(exit_mult, "exit_mult")
        self._risk_frac = positive(risk_frac, "risk_frac")
        if self._risk_frac > _ONE:
            raise ValueError(f"risk_frac должен быть <= 1, получено {risk_frac}")
        self._reentry_frac = positive(reentry_frac, "reentry_frac")
        if self._reentry_frac > _ONE:
            raise ValueError(f"reentry_frac должен быть <= 1, получено {reentry_frac}")

        self._m15 = Aggregator(FIFTEEN_MIN_WIDTH, FIFTEEN_MIN_COUNT)
        self._m5h = Aggregator(FIVE_HOURS_WIDTH, FIVE_HOURS_COUNT)
        self._atr15 = Atr(self._atr_len_exit)
        self._ema5h = Ema(self._ema_len)
        self._atr5h = Atr(self._atr_len_regime)
        self._adx5h = Adx(self._adx_len)

        self._lower: Decimal | None = None
        self._mid: Decimal | None = None
        self._upper: Decimal | None = None
        self._adx: Decimal | None = None
        self._atr15_value: Decimal | None = None
        self._entry_pending = False
        self._entry_atr: Decimal | None = None
        self._channel_log: list[ChannelPoint] | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> FlatKeltnerStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры flat_keltner: {unknown}")
        kwargs = {name: params[name] for name in cls.PARAM_NAMES if name in params}
        return cls(**kwargs)

    @property
    def last_adx(self) -> Decimal | None:
        """ADX последнего закрытого 5H-бара: режим смотрит в 5H, не в 15m."""
        return self._adx

    @property
    def last_lower(self) -> Decimal | None:
        """Нижняя граница канала Кельтнера последнего закрытого 5H-бара."""
        return self._lower

    @property
    def last_atr15m(self) -> Decimal | None:
        """ATR последнего закрытого 15m-бара: из него стоп, цель и размер."""
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
                entry = self._entry_order(state)
                if entry is not None:
                    orders.append(entry)
        if self._entry_pending and state.position > 0:
            # Вход исполнен на этом баре: цена входа оценивается его закрытием.
            self._entry_pending = False
            orders.extend(self._bracket_orders(bar, state))
        return orders

    def _update_15m(self, bar15: Bar) -> None:
        atr = self._atr15.update(bar15.high, bar15.low, bar15.close)
        if atr is not None:
            self._atr15_value = atr

    def _update_5h(self, bar5h: Bar) -> None:
        ema = self._ema5h.update(bar5h.close)
        atr = self._atr5h.update(bar5h.high, bar5h.low, bar5h.close)
        adx = self._adx5h.update(bar5h.high, bar5h.low, bar5h.close)
        if ema is None or atr is None:
            return
        self._mid = ema
        self._upper = ema + self._channel_mult * atr
        self._lower = ema - self._channel_mult * atr
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
        """Режим флэт, цена коснулась нижней границы, свеча отвергла вверх."""
        if self._lower is None or self._adx is None:
            return False
        if self._adx >= self._adx_max:
            return False
        if state.position != 0 or self._entry_pending:
            return False
        if bar15.low > self._lower:
            return False
        rejection = bar15.low + self._reentry_frac * (bar15.high - bar15.low)
        return bar15.close > rejection

    def _entry_order(self, state: StrategyState) -> Order | None:
        """MARKET-вход: размер по риску risk_frac капитала и ширине стопа.

        Ширина стопа — `exit_mult * ATR15m`, и именно она в знаменателе: риск на
        сделку равен `risk_frac` капитала при любом `exit_mult`.

        None — входить нечем: ATR15m ещё не готов или капитал не положителен
        (тогда размер вышел бы нулевым, и ордер был бы пустым).
        """
        atr = self._atr15_value
        if atr is None or atr <= 0 or state.equity <= 0:
            return None
        qty = state.equity * self._risk_frac / (self._exit_mult * atr)
        self._entry_pending = True
        self._entry_atr = atr
        return Order(Side.BUY, OrderKind.MARKET, qty, None, None, False)

    def _bracket_orders(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Брекет по открытой позиции: стоп ниже входа, цель выше.

        Цена входа оценивается как close бара, на котором вход исполнен:
        MARKET-ордер исполняется по закрытию бара плюс проскальзывание, то
        есть ошибка оценки равна одному проскальзыванию.
        """
        atr = self._entry_atr
        if atr is None or atr <= 0:
            return []
        entry = bar.close
        qty = state.position
        stop = entry - self._exit_mult * atr
        target = entry + self._exit_mult * atr
        return [
            Order(Side.SELL, OrderKind.STOP_MARKET, qty, None, stop, True),
            Order(Side.SELL, OrderKind.LIMIT, qty, target, None, True),
        ]
