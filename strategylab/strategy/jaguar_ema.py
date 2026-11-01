"""JAGUAR: откат к EMA13 в тренде EMA13/EMA34 на 5m-барах (раунд 4, класс J).

Пререгистрация: `.superpowers/sdd/2026-10-29-next-series/round4-preregistration.md`,
§4 — механика заморожена 2026-11-01, код повторяет её без доопределений и без
ослаблений. Харнесс кормит стратегию 5m-барами через ``--interval 5m``;
внутренняя агрегация не нужна (в отличие от ТРЕНДа, живущего на 1m-ленте).

Тренд-фильтр (по t−1, как в §4): ``EMA13(t−1) > EMA34(t−1)`` и обе растут
(``EMA13(t−1) > EMA13(t−2)``, ``EMA34(t−1) > EMA34(t−2)``) — long-контекст;
short — зеркало. Триггер long на баре t: свеча медвежья (``C(t) < O(t)``),
минимум касается EMA13 (``L(t) <= EMA13(t)``), закрытие возвращается над EMA13
(``C(t) > EMA13(t)``). Вход — один MARKET, сигнал на закрытии бара t,
исполнение на следующем баре (нативная честность харнесса).

SL = EMA34(t) сигнального бара — замораживается (решение 5 trend_pullback):
стоп обязан быть тем, по которому принято решение входить, а не пересчитанным
к бару исполнения. Оценка входа — close исполняющего бара (решение 4 там же:
движок не отдаёт цену исполнения); при сайзинге на сигнальном баре за вход
берётся close сигнального бара. ``R = оценка − SL``; ``R <= 0`` или
``equity <= 0`` — входа нет. Заметим: при замороженном фильтре и триггере
``R <= 0`` на сигнальном баре алгебраически недостижим: ``C(t) > EMA13(t)``
по рекурсии EMA равносильно ``C(t) > EMA13(t−1)``, фильтр даёт
``EMA13(t−1) > EMA34(t−1)``, а ``EMA34(t)`` лежит между ``EMA34(t−1)`` и
``C(t)`` — значит, ниже закрытия (у шорта — зеркально). Гард оставлен как
страховка ядра, не как живое правило.

Fundability-skip (раунд 5, §1 — единственное новое правило): перед подачей
входного MARKET считается ``rho = R / close(бара t)`` — то же отношение и та же
цена, по которой гейт ``_guard_submit`` (engine.py:138) считает notional. Если
``rho < fund_floor`` — сигнал пропускается целиком: ни одной заявки, состояние
сделки не трогается (флэт остаётся флэтом), следующий сигнал обрабатывается как
обычно. Сравнение строгое: ``rho == fund_floor`` торгуется (на линии гейт
проходит по равенству — §11 пререгистрации). База — close сигнального (подающего)
бара, не mid и не цена исполнения. Дефолт ``Decimal("0.00025")`` = r/lev при
risk_frac 0.005 и leverage 20 (кибы к5-3, к5-4): входные заявки ровно проходимого
множества проходят гейт математически, MARGIN_CRASH на входах исключён по
построению (§3). Выходы, трейл, TP1 и механика ликвидации не затронуты.

Сайзинг: ``qty = state.equity * risk_frac / R`` (решение 7 trend_pullback —
капитал берётся из реализованного equity, у прогона без капитала сделки нет).
Брекет — на баре исполнения входа (решение 6 там же: reduce_only нельзя
поставить при флэте): STOP_MARKET reduce_only на полный остаток позиции по
замороженному SL и LIMIT reduce_only на qty/2 по оценке ``+ tp_mult·R``
(TP1 = 1.5R, киб владельца §7 к1; для шорта — зеркало).

После TP1 (позиция уменьшилась от записанного размера) стоп переезжает на
``max(старый SL, EMA13 текущего бара)`` и трейлит по закрытиям (§4, без
правила безубытка — его в заморозке нет; шорт — ``min``). Ре-брекет: новый
STOP_MARKET reduce_only на остаток. Семантика двух покоящихся reduce-only
стопов по коду брокера (broker/sim.py, закреплено тестом
``test_second_reduce_only_stop_overrides_the_mirror``): второй стоп зеркалит уровень в
позицию — ``position.stop_price`` перезаписывается, первый стоп остаётся в
очереди неактивным (``_apply_stops`` пропускает reduce-only: стоп-заявка
исполняется только зеркалом позиции); закрытие позиции снимает все покоящиеся
reduce-only заявки (``_cancel_reduced``) — двойного выхода не бывает по
построению ядра.

Полный выход (перекрывает трейлинг): ``close < EMA34`` — MARKET reduce_only
на остаток; шорт — ``close > EMA34``. Look-ahead нет: только закрытые бары,
история индикаторов сдвигается в конце on_bar.

Раунд 7 (ось «источник профита», к7-2): параметр ``be_mult`` — безубыточный
рэтчёт ДО TP1. Пока позиция жива и TP1 ещё не было (TP1 детектируется по
закрытию бара раньше проверки рэтчета), если ``close >= entry + be_mult·R``
(лонг; зеркально шорт), стоп ЕДИНОЖДЫ переносится на ``entry·(1 + 0.0002)``
(шорт — ``entry·(1 − 0.0002)``); буфер — константа кода, не PARAM. entry/R —
оценка и риск брекета (close исполняющего бара, решение 4). Дефолт ``None`` =
выключено = побайтовое поведение раунда 6. Рэтчет не отменяет трейлинг по
EMA13 после TP1: уровни монотонны (общий ``self._stop``). Без брекета (R<=0
гэп) рэтчета нет: нечего отсчитывать.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState
from strategylab.strategy.indicators import Ema, positive

_ONE = Decimal("1")

# Периоды EMA заморожены пререгистрацией §4 и не являются параметром сетки.
_EMA_FAST = 13
_EMA_SLOW = 34

# к7-2: буфер безубыточного рэтчета — доля цены, константа кода (не PARAM).
_BE_BUFFER = Decimal("0.0002")


class JaguarEmaStrategy:
    """JAGUAR: лонг/шорт на откате к EMA13 в тренде EMA13/EMA34, M5.

    Сигнал читается на закрытии 5m-бара, вход — MARKET, выходы —
    reduce_only-брекет (SL по EMA34 сигнального бара, TP1 половиной позиции
    на 1.5R), после TP1 стоп трейлит EMA13, закрытие тренда — выход по
    close за EMA34. Зеркальные шорты включены всегда: это часть класса.
    Сигналы с ``R / close(сигнального бара) < fund_floor`` пропускаются —
    ни одной заявки (раунд 5, §1). При заданном ``be_mult`` до TP1 один раз
    включается безубыточный рэтчет стопа (раунд 7, к7-2); по умолчанию выключен.
    """

    name = "jaguar_ema"

    PARAM_NAMES = ("risk_frac", "tp_mult", "fund_floor", "be_mult")

    def __init__(
        self,
        risk_frac: Decimal = Decimal("0.01"),
        tp_mult: Decimal = Decimal("1.5"),
        fund_floor: Decimal = Decimal("0.00025"),
        be_mult: Decimal | None = None,
    ) -> None:
        self._risk_frac = positive(risk_frac, "risk_frac")
        if self._risk_frac > _ONE:
            raise ValueError(f"risk_frac должен быть <= 1, получено {risk_frac}")
        self._tp_mult = positive(tp_mult, "tp_mult")
        self._fund_floor = positive(fund_floor, "fund_floor")
        self._be_mult = positive(be_mult, "be_mult") if be_mult is not None else None

        self._ema_fast = Ema(_EMA_FAST)
        self._ema_slow = Ema(_EMA_SLOW)
        # Значения EMA на t−1 и t−2: фильтр смотрит в прошлый бар, не в текущий (§4).
        self._p1_fast: Decimal | None = None
        self._p2_fast: Decimal | None = None
        self._p1_slow: Decimal | None = None
        self._p2_slow: Decimal | None = None

        # Состояние сделки.
        self._entry_pending = False
        self._stop: Decimal | None = None
        self._entry_qty: Decimal | None = None
        self._trailing = False
        self._exit_pending = False
        # Раунд 7 (к7-2): рэтчет безубытка до TP1; база/R из заморозки брекета.
        self._be_base: Decimal | None = None
        self._be_risk: Decimal | None = None
        self._be_done = False

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> JaguarEmaStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры jaguar_ema: {unknown}")
        kwargs = {name: params[name] for name in cls.PARAM_NAMES if name in params}
        return cls(**kwargs)

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Один закрытый 5m-бар: обновить EMA, отработать сделку или искать вход."""
        fast = self._ema_fast.update(bar.close)
        slow = self._ema_slow.update(bar.close)
        assert fast is not None and slow is not None  # Ema сидируется первым значением
        orders: list[Order] = []
        if self._entry_pending and state.position != 0:
            # Бар исполнения входа: оценка — close этого бара, брекет — сюда.
            self._entry_pending = False
            orders.extend(self._bracket_orders(bar, state))
        elif state.position != 0:
            orders.extend(self._manage_orders(bar, state, fast, slow))
        else:
            if not self._entry_pending:
                self._reset_trade_state()
            orders.extend(self._entry_orders(bar, state, fast, slow))
        # История сдвигается в конце: значения t становятся t−1 для следующего бара.
        self._p2_fast, self._p1_fast = self._p1_fast, fast
        self._p2_slow, self._p1_slow = self._p1_slow, slow
        return orders

    def _entry_orders(
        self, bar: Bar, state: StrategyState, fast: Decimal, slow: Decimal
    ) -> list[Order]:
        """MARKET-вход по сигналу: размер по риску risk_frac капитала и R до EMA34.

        SL и оценка замораживаются на сигнальном баре; ``R <= 0`` или
        нулевой капитал — входа нет (решения 5 и 7 trend_pullback).
        Fundability-skip (§1 раунда 5): ``rho = R / close(бара t)`` ниже
        ``fund_floor`` — сигнала как бы не было: ни одной заявки, состояние
        не трогается, следующий сигнал обрабатывается обычно. Равенство
        ``rho == fund_floor`` торгуется (строгое ``<``).
        """
        side = self._entry_signal(bar, fast)
        if side is None or state.equity <= 0:
            return []
        risk = bar.close - slow if side is Side.BUY else slow - bar.close
        if risk <= 0:
            return []
        if risk / bar.close < self._fund_floor:
            return []
        self._entry_pending = True
        self._stop = slow
        qty = state.equity * self._risk_frac / risk
        return [Order(side, OrderKind.MARKET, qty, None, None, False)]

    def _entry_signal(self, bar: Bar, fast: Decimal) -> Side | None:
        """Фильтр по t−1 (заморожен §4) и триггер откатной свечи на баре t.

        Вызывается только при флэте и сброшенном entry_pending. Индикаторы
        не готовы (нет значений t−1 и t−2) — сигнала нет.
        """
        p1_fast, p2_fast = self._p1_fast, self._p2_fast
        p1_slow, p2_slow = self._p1_slow, self._p2_slow
        if p1_fast is None or p2_fast is None or p1_slow is None or p2_slow is None:
            return None
        if p1_fast > p1_slow and p1_fast > p2_fast and p1_slow > p2_slow:
            # Long-контекст: медвежья свеча касается EMA13 и закрывается над ней.
            if bar.close < bar.open and bar.low <= fast and bar.close > fast:
                return Side.BUY
        elif p1_fast < p1_slow and p1_fast < p2_fast and p1_slow < p2_slow:
            # Short-контекст: бычья свеча касается EMA13 и закрывается под ней.
            if bar.close > bar.open and bar.high >= fast and bar.close < fast:
                return Side.SELL
        return None

    def _bracket_orders(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Брекет на баре исполнения: стоп на весь остаток, TP1 — половина на 1.5R.

        Оценка входа — close исполняющего бара (решение 4), R — от неё до
        замороженного SL. ``R <= 0`` — брекета нет: позиция живёт до выхода
        по правилу close/EMA34 (как в тренде). Записанный размер позиции —
        будущая точка детекта TP1.
        """
        stop = self._stop
        if stop is None:
            return []
        long = state.position > 0
        entry = bar.close
        qty = abs(state.position)
        risk = entry - stop if long else stop - entry
        if risk <= 0:
            return []
        self._entry_qty = qty
        if self._be_mult is not None:
            self._be_base = entry
            self._be_risk = risk
        exit_side = Side.SELL if long else Side.BUY
        target = entry + self._tp_mult * risk if long else entry - self._tp_mult * risk
        return [
            Order(exit_side, OrderKind.STOP_MARKET, qty, None, stop, True),
            Order(exit_side, OrderKind.LIMIT, qty / 2, target, None, True),
        ]

    def _manage_orders(
        self, bar: Bar, state: StrategyState, fast: Decimal, slow: Decimal
    ) -> list[Order]:
        """Жизнь позиции: полный выход по EMA34 важнее трейлинга по EMA13.

        TP1 детектируется уменьшением |позиции| от записанного размера; с
        этого момента стоп = max(старый, EMA13 закрытия) для лонга (min —
        для шорта), level никогда не уезжает назад. Новый STOP_MARKET
        reduce_only перерисовывает зеркало позиции; старый остаётся в очереди
        неактивным и снимается ядром при закрытии (см. докстринг класса).
        """
        if self._exit_pending:
            return []
        long = state.position > 0
        qty = abs(state.position)
        if (long and bar.close < slow) or (not long and bar.close > slow):
            self._exit_pending = True
            exit_side = Side.SELL if long else Side.BUY
            return [Order(exit_side, OrderKind.MARKET, qty, None, None, True)]
        if self._entry_qty is not None and qty < self._entry_qty:
            self._trailing = True
        ratchet = self._be_ratchet_order(bar, long, qty)
        if ratchet is not None:
            return [ratchet]
        stop = self._stop
        if not self._trailing or stop is None:
            return []
        new_stop = max(stop, fast) if long else min(stop, fast)
        if new_stop == stop:
            return []
        self._stop = new_stop
        exit_side = Side.SELL if long else Side.BUY
        return [Order(exit_side, OrderKind.STOP_MARKET, qty, None, new_stop, True)]

    def _be_ratchet_order(self, bar: Bar, long: bool, qty: Decimal) -> Order | None:
        """к7-2: однократный перенос стопа на безубыток с буфером, пока TP1 не было.

        Живёт только при заданном be_mult и замороженном брекете (база и R из
        _bracket_orders). Условие — close исполняющего бара за порогом
        entry + be_mult·R (шорт зеркально); уровень строго лучше текущего стопа,
        иначе заявка не нужна. После срабатывания (_be_done) не возвращается:
        рэтчет однократный, дальнейший путь — только EMA13-трейлинг после TP1.
        """
        if self._be_mult is None or self._trailing or self._be_done:
            return None
        base, risk, stop = self._be_base, self._be_risk, self._stop
        if base is None or risk is None or stop is None:
            return None
        if long:
            if bar.close < base + self._be_mult * risk:
                return None
            level = base * (_ONE + _BE_BUFFER)
            if level <= stop:
                return None
        else:
            if bar.close > base - self._be_mult * risk:
                return None
            level = base * (_ONE - _BE_BUFFER)
            if level >= stop:
                return None
        self._stop = level
        self._be_done = True
        exit_side = Side.SELL if long else Side.BUY
        return Order(exit_side, OrderKind.STOP_MARKET, qty, None, level, True)

    def _reset_trade_state(self) -> None:
        """Флэт: сделка закрыта (стоп, выход или сведение окна) — всё сброшено."""
        self._stop = None
        self._entry_qty = None
        self._trailing = False
        self._exit_pending = False
        self._be_base = None
        self._be_risk = None
        self._be_done = False
