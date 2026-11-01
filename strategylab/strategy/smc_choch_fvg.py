"""SMC CHoCH+FVG: разворот 1m на сломе структуры с limit-входом в середину FVG.

Спека: .superpowers/sdd/2026-10-29-next-series/round4-preregistration.md, §6
(класс S, заморожено 2026-11-01; аменд 2026-11-01 — свежесть FVG, owner «ок»).
Торговля на первичных 1m-ячейках: агрегаций
и HTF-фильтров нет (§6: «первичные ячейки БЕЗ HTF-фильтра»), «сильные уровни»
и «ближайшая цель» не реализуются (незарегистрируемо).

Механика (заморожена §6 с датированным амендом 2026-11-01: клауза свежести FVG)
--------------------------------

Экстремумы w=2: локальный минимум в баре t (L(t) < L(t±j), j=1..2, строго)
считается подтверждённым только на закрытии бара t+2; пики симметрично
(H(t) > H(t±j)). Стратегия ведёт два списка: кандидаты (левое крыло
выполнено, ждут правое) и подтверждённые. Любое использование экстремума —
только из подтверждённых; до t+2 экстремум мёртв для всех правил.

Структура: подтверждённые экстремумы складываются в зигзаг чередующихся
trough/peak (одноимённые подряд консолидируются: новый экстремум
сильнее — заменяет хвост, слабее — отбрасывается). Нисходящая структура:
последние два trough зигзага строго убывают; восходящая: последние два
peak строго возрастают. CHoCH long: close бара > последнего
подтверждённого peak при нисходящей структуре. Зеркало шорта: close <
последнего подтверждённого trough при восходящей.

FVG (1m): бычий в баре t: L(t) > H(t−2); медвежий: H(t) < L(t−2).

Сигнал: S = max(бар CHoCH, бар FVG) + 2 (задержка подтверждения). Свежесть FVG
— по аменду §6 от 2026-11-01 (owner «ок»): FVG образует пару с CHoCH только
если его бар НЕ РАНЬШЕ бара подтверждения CHoCH (f.bar >= c.bar); FVG раньше
CHoCH — вырожденный вход, пары нет. В коде дополнительно стоит возрастной
страж S − f.bar <= 5: под амендом он идентично истинен (допустимые пары имеют
S = f.bar+2, возраст ровно 2) и сохранён только потому, что торговую логику
не меняем (см. решение 7). Одна лимитка на
сделку; вход не раньше close_{S+1} (движок: ордер, поданный на закрытии S,
исполняется не раньше apply_bar следующего бара — §6 «вход не раньше
close_{S+1}» выполняется ядром нативно).

Entry: LIMIT BUY по mid = (H(t−2)+L(t))/2 (середина FVG),
expires_in_bars = ttl_bars (default 60); qty = equity·risk_frac/R,
R = mid − SL, SL = min(L(t−2..t))·0.9995 (буфер 0.05%). R <= 0,
equity <= 0, R/close(бара S) < fund_floor (раунд 5, §1 и решение 10) или
позиция не флэт — не торговать. Шорт-зеркало: SELL LIMIT,
mid = (L(t−2)+H(t))/2, SL = max(H(t−2..t))·1.0005.

Исполнение входа (entry_pending, позиция != 0) — брекет на баре исполнения:
STOP_MARKET reduce_only на остаток по SL (заморожен с сигнального бара) и
LIMIT reduce_only на половину объёма по entry_est + tp_mult·R (tp_mult
default 4; для шорта — entry_est − tp_mult·R).

После TP1 (|позиция| уменьшился) — перестановка стопа в безубыток:
STOP_MARKET reduce_only по entry_est (шорт — BUY-стоп по entry_est сверху).

Runner-выходы (остаток): обратный подтверждённый CHoCH (появление
CHoCH-условия противоположного направления, §6) — MARKET reduce_only
остаток; hard-cap max_hold баров (default 240) от бара исполнения —
MARKET reduce_only. Позиция флэт — состояние сделки сброшено полностью.

Решения, которые здесь зафиксированы
------------------------------------

1. Фрактальных экстремумов в пакете не было: sweep_liquidity ведёт rolling
   min/max окна диапазона (не пиоты), trend_pullback — swing low окна.
   Определения экстремумов реализованы здесь по §6 и только здесь; второй
   набор формул не плодится — в пакете их просто не было.

2. Нет lookahead: на баре t обработаны только бары <= t; экстремум центра t
   попадает в подтверждённые на закрытии t+2; события CHoCH/FVG фиксируются
   на своих барах и дальше не пересматриваются; entry-уровни mid/SL/R/R
   замораживаются в момент появления FVG и на баре S не пересчитываются.

3. Оценка входа — сама цена лимитки, а не close исполняющего бара (отличие
   от trend_pullback/sweep, где вход MARKET и движок цену исполнения не
   отдаёт): sim.py исполняет LIMIT ровно по limit_price на касании
   (`_limit_fill`, maker-комиссия, без проскальзывания), поэтому
   entry_est = mid — фактическая цена входа, а не оценка. TP1 = mid ±
   tp_mult·R и безубыток по mid тогда точны; R тот же, что в сайзинге.

4. Семантика reduce_only в sim.py проверена и закреплена тестом:
   STOP_MARKET reduce_only зеркалит только уровень в позицию
   (`_mirror_reduced`), повторная постановка (BE вместо SL) ПЕРЕЗАПИСЫВАЕТ
   зеркало стопа; первая resting-заявка остаётся в очереди мёртвым грузом
   (`_apply_stops` пропускает reduce_only) и снимается при флэте
   (`_cancel_reduced`). LIMIT reduce_only зеркалит уровень и объём
   (`take_profit_qty`) — TP1 исполняется половиной, после частичного
   исполнения зеркало цели и resting-лимитка того же уровня снимаются
   (`_drop_resting_target`), остаток живёт без цели до новой постановки.
   Сведение позиции снимает все reduce_only — двойного выхода нет;
   reduce_only больше позиции брокер отклоняет (submit ValueError), поэтому
   объемы выходов берутся из фактической позиции.

5. TIF: неисполненная entry-лимитка молча списывается ядром на входе
   apply_bar с seq > born + ttl (feat/limit-order-expiry, семантика
   limit_pullback); лимитка, поданная на баре S, жива на барах S+1..S+ttl.
   Стратегия чистит entry_pending на баре S+ttl+1 (заявка там уже мертва),
   окно длиннее TTL — window-cancel не участвует (§6).

6. Вилка PESSIMISTIC первична (как вся серия): бар, где достигнуты и SL, и
   TP1, исполняет стоп; выходной MARKET reduce_only, поданный на баре t,
   конкурирует со стопом на баре t+1 — `_apply_exits` идёт раньше
   `_apply_markets`, при закрытии стопа маркет-заявка тихо отбрасывается
   (`_effective_qty` = None при флэте). Обратный CHoCH и hard-cap могут не
   сработать раньше стопа — это свойство вилки, не баг стратегии.

7. Интерпретации замороженного текста (громко, без тихих адаптаций):
   - hard-cap считается от бара ИСПОЛНЕНИЯ входа и применяется к позиции на
     любой стадии (до TP1 тоже): §6 привязывает «холд 240 минут» к входу,
     и держать сделку дольше 240 баров без TP1 означало бы нарушить cap.
   - обратный CHoCH закрывает только runner после TP1 («остаток»): до TP1
     позиция выходит по SL/TP1/hard-cap.
   - событие обратного CHoCH для выхода должно появиться строго после бара
     исполнения (bar > exec_bar).
   - правило свежести FVG — это §6 с датированным амендом 2026-11-01 (клауза
     f.bar >= c.bar: FVG, сформированный раньше CHoCH, пару не образует;
     первоначальная буква §6 её не содержала — дыра спеки, закрытая амендом
     до чисел). Вторая конъюнкция в коде (S − f.bar <= 5) ни из §6, ни из
     аменда не переводилась: под амендом она идентично истинна (S = f.bar+2,
     возраст ровно 2) и оставлена лишь потому, что торговая логика не
     меняется; ни на что не влияет. CHoCH может быть старше FVG без
     возрастных ограничений (ни §6, ни аменд их не вводят).
   - оба CHoCH-события на одном баре невозможны, оба FVG на одном баре
     невозможны; при гипотетическом совпадении приоритет лонга (детерминизм).

8. Консольный контракт как в пакете: капитал из `state.equity`
   (накопленный реализованный P&L движка); при equity <= 0 заявка не
   подаётся. Margin-гейт движка (`_guard_submit`) на entry-лимитку при
   leverage харнесса (10) проверяется прогоном — rejection не обрабатывается
   на уровне стратегии (MarginError валит прогон, §3: это СЧАСТЬЕ замера).

9. Сайзинг шорта зеркален: qty = equity·risk_frac/(SL−mid), SL сверху.
   Все выходы reduce_only, объём — фактический остаток позиции; новых
   входов, пока entry_pending или позиция открыта, нет (одна лимитка на
   сделку, §6).

10. Fundability-skip (раунд 5, §1 — единственное новое правило): перед подачей
    entry-лимитки на баре S считается ``rho = R / close(бара S)`` — R заморожен
    с бара FVG, знаменатель же ТА ЖЕ цена подающего бара, по которой
    ``_guard_submit`` (engine.py:138) считает notional лимитки. Это НЕ mid и НЕ
    база бара FVG: после пробоя close_S стоит выше mid, поэтому close-база
    строже mid-базы и отсекает дополнительные сигналы (builder-check Q1 — при
    отлёте close_S над mid required может превысить marked, тот самый MarginError,
    который харнесс не ловит). При ``rho < fund_floor`` сигнал пропускается
    целиком: ни одной заявки, торговое состояние не трогается (машина остаётся
    в сброшенном флэте), пара CHoCH+FVG расходуется по обычному пути отказа и
    следующая пара обрабатывается как обычно. Сравнение строгое:
    ``rho == fund_floor`` торгуется (§11: на линии гейт проходит по равенству).
    Дефолт ``Decimal("0.00025")`` = r/lev при risk_frac 0.005 и leverage 20
    (кибы к5-3, к5-4) — MARGIN_CRASH на входах исключён по построению (§3).
    Брекет, TP1, безубыток, runner-выходы и hard-cap не затронуты.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState
from strategylab.strategy.indicators import positive, positive_int

_ONE = Decimal("1")
_TWO = Decimal("2")
_HALF = Decimal("0.5")
_ZERO = Decimal("0")

# Замороженные §6 константы (не параметры сетки):
_W = 2  # полуширина фрактала: экстремум центра t проверяется на t±1, t±2
_CONFIRM_DELAY = 2  # подтверждение экстремума центра t приходит на close t+2
_FVG_AGE_LIMIT = 5  # никогда не срабатывает (ампенд §6 2026-11-01): возраст пары = 2, реш. 7
_SL_LONG = Decimal("0.9995")  # буфер 0.05% под окном FVG (лонг)
_SL_SHORT = Decimal("1.0005")  # зеркало: буфер 0.05% над окном FVG (шорт)

_TROUGH = "trough"
_PEAK = "peak"

# Ограничатели памяти длинных 1m-прогонов: хвост подтверждений нужен только
# для последних экстремумов и последних двух одноимённых.
_CONFIRMED_KEEP = 10_000
_ZIGZAG_KEEP = 64


@dataclass(frozen=True, slots=True)
class Extreme:
    """Подтверждённый фрактальный экстремум: вид, бар центра, цена центра."""

    kind: str
    bar: int
    price: Decimal


@dataclass(frozen=True, slots=True)
class Fvg:
    """Fair Value Gap: bar — правый бар окна (t−2..t), mid — середина дыры,
    sl — замороженный стоп, risk — положительное расстояние mid..sl."""

    bar: int
    mid: Decimal
    sl: Decimal
    risk: Decimal


class SmcChochFvgStrategy:
    """SMC CHoCH+FVG 1m: слом подтверждённой структуры, лимитка в середину FVG.

    Сигнал решается на закрытии бара S = max(бар CHoCH, бар FVG) + 2, вход —
    одна LIMIT-заявка с TIF, выходы — reduce_only-брекет (SL, TP1 половина),
    безубыток после TP1, затем runner: обратный CHoCH или hard-cap.
    Пары с ``R / close(бара S) < fund_floor`` пропускаются — ни одной заявки
    (раунд 5, §1, решение 10).
    """

    name = "smc_choch_fvg"

    PARAM_NAMES = (
        "ttl_bars",
        "risk_frac",
        "tp_mult",
        "max_hold",
        "fund_floor",
    )

    def __init__(
        self,
        ttl_bars: Decimal = Decimal("60"),
        risk_frac: Decimal = Decimal("0.01"),
        tp_mult: Decimal = Decimal("4"),
        max_hold: Decimal = Decimal("240"),
        fund_floor: Decimal = Decimal("0.00025"),
    ) -> None:
        self._ttl = positive_int(ttl_bars, "ttl_bars")
        self._risk_frac = positive(risk_frac, "risk_frac")
        if self._risk_frac > _ONE:
            raise ValueError(f"risk_frac должен быть <= 1, получено {risk_frac}")
        self._tp_mult = positive(tp_mult, "tp_mult")
        self._max_hold = positive_int(max_hold, "max_hold")
        self._fund_floor = positive(fund_floor, "fund_floor")

        # Структурный слой: два списка экстремумов (§6) + производные.
        self._cand: list[Extreme] = []
        self._confirmed: deque[Extreme] = deque(maxlen=_CONFIRMED_KEEP)
        self._zig: deque[Extreme] = deque(maxlen=_ZIGZAG_KEEP)
        # (high, low) двух предыдущих баров до текущего: для левого крыла
        # фрактала и FVG (t−2 — старейший элемент).
        self._hl: deque[tuple[Decimal, Decimal]] = deque(maxlen=_W)
        self._descending = False
        self._ascending = False
        self._last_peak: Decimal | None = None
        self._last_trough: Decimal | None = None
        self._bull_choch_bar: int | None = None
        self._bear_choch_bar: int | None = None
        self._bull_fvg: Fvg | None = None
        self._bear_fvg: Fvg | None = None

        # Слой сделки: замороженные сигнальные уровни + прогресс выхода.
        self._entry_pending = False
        self._signal_bar: int | None = None
        self._long_trade = False
        self._mid: Decimal | None = None
        self._sl: Decimal | None = None
        self._risk: Decimal | None = None
        self._bracket_qty: Decimal | None = None
        self._exec_bar: int | None = None
        self._tp1_done = False
        self._exit_pending = False

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> SmcChochFvgStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры smc_choch_fvg: {unknown}")
        kwargs = {name: params[name] for name in cls.PARAM_NAMES if name in params}
        return cls(**kwargs)

    # -- диагностика (только чтение; на решения не влияют) ------------------

    @property
    def candidates(self) -> tuple[Extreme, ...]:
        """Кандидаты в экстремумы (левое крыло выполнено, правое ещё живёт)."""
        return tuple(self._cand)

    @property
    def confirmed_extremes(self) -> tuple[Extreme, ...]:
        """Подтверждённые экстремумы по порядку баров центра (хвост keeping)."""
        return tuple(self._confirmed)

    @property
    def descending(self) -> bool:
        """Нисходящая структура: последние два подтверждённых trough убывают."""
        return self._descending

    @property
    def ascending(self) -> bool:
        """Восходящая структура: последние два подтверждённых peak растут."""
        return self._ascending

    @property
    def bull_choch_bar(self) -> int | None:
        """Бар последнего незакрытого бычьего CHoCH-события (для сигнала)."""
        return self._bull_choch_bar

    @property
    def bear_choch_bar(self) -> int | None:
        """Бар последнего незакрытого медвежьего CHoCH-события."""
        return self._bear_choch_bar

    @property
    def bull_fvg(self) -> Fvg | None:
        """Последний бычий FVG (перезаписывается новым; замораживает mid/SL/R)."""
        return self._bull_fvg

    @property
    def bear_fvg(self) -> Fvg | None:
        """Последний медвежий FVG (зеркало)."""
        return self._bear_fvg

    @property
    def in_trade(self) -> bool:
        """Сделка жива: лимитка в очереди или открытая позиция нашего входа."""
        return self._entry_pending or self._bracket_qty is not None

    # -- основной поток ------------------------------------------------------

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Один 1m-бар: фракталы/события -> менеджмент позиции -> сигнал входа."""
        index = state.bar_index
        self._update_fractals(index, bar.high, bar.low)
        self._update_events(index, bar)
        self._hl.append((bar.high, bar.low))

        orders: list[Order] = []
        if state.position == 0:
            # Флэт: торговое состояние сброшено целиком; entry-лимитка,
            # не дожившая до исполнения, мертва в ядре на этом баре (§6, TIF).
            if self._bracket_qty is not None:
                self._reset_trade()
            elif (
                self._entry_pending
                and self._signal_bar is not None
                and index > self._signal_bar + self._ttl
            ):
                self._reset_trade()
        elif self._entry_pending:
            # Вход исполнен этим баром: reduce_only при флэте запрещён ядром,
            # брекет ставится здесь, объёмом фактической позиции.
            self._entry_pending = False
            self._signal_bar = None
            orders.extend(self._bracket_orders(state))
        elif self._bracket_qty is not None:
            orders.extend(self._manage_trade(state))

        if (
            state.position == 0
            and not self._entry_pending
            and self._bracket_qty is None
        ):
            entry = self._entry_order(bar, state)
            if entry is not None:
                orders.append(entry)
        return orders

    # -- структурный слой -----------------------------------------------------

    def _update_fractals(self, index: int, high: Decimal, low: Decimal) -> None:
        """Продвинуть кандидатов (правое крыло), подтвердить на t+2, завести новых.

        Кандидат центра c переживает соседям справа строго в пределах своего
        вида (trough: lows выше; peak: highs ниже); на баре c+2 выживший
        подтверждается, погибший забывается. Новый кандидат — левое крыло на
        текущем баре (сравнивается с t−1 и t−2 из `_hl` до добавления бара).
        """
        survivors: list[Extreme] = []
        for cand in self._cand:
            holds = (low > cand.price) if cand.kind == _TROUGH else (high < cand.price)
            if not holds:
                continue
            if index - cand.bar >= _CONFIRM_DELAY:
                self._confirm(cand)
            else:
                survivors.append(cand)
        self._cand = survivors
        if len(self._hl) >= _W:
            if low < self._hl[-1][1] and low < self._hl[-2][1]:
                self._cand.append(Extreme(_TROUGH, index, low))
            if high > self._hl[-1][0] and high > self._hl[-2][0]:
                self._cand.append(Extreme(_PEAK, index, high))

    def _confirm(self, extreme: Extreme) -> None:
        """Экстремум стал подтверждённым: в список и в зигзаг структуры.

        Одноимённые подряд консолидируются в один зигзаг-пивот: сильный
        (trough ниже / peak выше) заменяет хвост, слабый отбрасывается —
        иначе зигзаг не чередуется, а §6 определяет структуру по
        чередующимся trough/peak.
        """
        self._confirmed.append(extreme)
        if self._zig and self._zig[-1].kind == extreme.kind:
            tail = self._zig[-1]
            stronger = (
                extreme.price < tail.price
                if extreme.kind == _TROUGH
                else extreme.price > tail.price
            )
            if stronger:
                self._zig[-1] = extreme
        else:
            self._zig.append(extreme)
        self._recompute_structure()

    def _recompute_structure(self) -> None:
        """Флаги структуры и цены последних peak/trough из вершин зигзага."""
        troughs = [x.price for x in self._zig if x.kind == _TROUGH]
        peaks = [x.price for x in self._zig if x.kind == _PEAK]
        self._descending = len(troughs) >= 2 and troughs[-1] < troughs[-2]
        self._ascending = len(peaks) >= 2 and peaks[-1] > peaks[-2]
        self._last_peak = peaks[-1] if peaks else None
        self._last_trough = troughs[-1] if troughs else None

    def _update_events(self, index: int, bar: Bar) -> None:
        """Зафиксировать CHoCH/FVG-события закрытого бара (все входы — бары <= t)."""
        peak = self._last_peak
        if self._descending and peak is not None and bar.close > peak:
            self._bull_choch_bar = index
        trough = self._last_trough
        if self._ascending and trough is not None and bar.close < trough:
            self._bear_choch_bar = index
        if len(self._hl) >= _W:
            prev2_high = self._hl[-2][0]
            prev2_low = self._hl[-2][1]
            if bar.low > prev2_high:  # бычий FVG: L(t) > H(t−2), строго
                window_low = min(prev2_low, self._hl[-1][1], bar.low)
                mid = (prev2_high + bar.low) / _TWO
                sl = window_low * _SL_LONG
                self._bull_fvg = Fvg(index, mid, sl, mid - sl)
            if bar.high < prev2_low:  # медвежий FVG: H(t) < L(t−2), зеркало
                window_high = max(prev2_high, self._hl[-1][0], bar.high)
                mid = (prev2_low + bar.high) / _TWO
                sl = window_high * _SL_SHORT
                self._bear_fvg = Fvg(index, mid, sl, sl - mid)

    def _fresh_pair(self, choch_bar: int | None, fvg: Fvg | None, index: int) -> bool:
        """Пара CHoCH+FVG сигналит ровно на S = max(бары)+2 при свежем FVG.

        Свежесть = аменд §6 от 2026-11-01: fvg.bar >= choch.bar (бар FVG не
        раньше бара CHoCH). Вторая конъюнкция S − fvg.bar <= 5 под амендом
        идентично истинна (решение 7). События при этом самые свежие: новые
        CHoCH/FVG перезаписывают старых.
        """
        if choch_bar is None or fvg is None:
            return False
        if fvg.bar < choch_bar:
            return False
        if index != max(choch_bar, fvg.bar) + _CONFIRM_DELAY:
            return False
        return index - fvg.bar <= _FVG_AGE_LIMIT

    # -- торговый слой ----------------------------------------------------------

    def _entry_order(self, bar: Bar, state: StrategyState) -> Order | None:
        """Лимитка в середину FVG: qty = equity·risk_frac/R; уровни заморожены.

        Пара расходуется при совпадении S (S — единственный бар пары): после
        подачи (или отказа) события забываются, новая сделка требует новой
        пары. Отказ: R <= 0 (для корректного FVG невозможно: mid > H(t−2) >=
        min lows окна > SL с буфером — оставлено как защита от вырожденных
        данных) или equity <= 0 (капитала нет — как во всём пакете).
        Fundability-skip (§1 раунда 5, решение 10): ``rho = R / close(бара S)``
        ниже ``fund_floor`` — тот же путь отказа: ни одной заявки, торговое
        состояние не трогается, следующая пара обрабатывается обычно.
        Сравнение строгое: ``rho == fund_floor`` торгуется.
        """
        index = state.bar_index
        long_sig = self._fresh_pair(self._bull_choch_bar, self._bull_fvg, index)
        short_sig = (not long_sig) and self._fresh_pair(
            self._bear_choch_bar, self._bear_fvg, index
        )
        if not long_sig and not short_sig:
            return None
        fvg = self._bull_fvg if long_sig else self._bear_fvg
        if long_sig:
            self._bull_choch_bar = None
            self._bull_fvg = None
        else:
            self._bear_choch_bar = None
            self._bear_fvg = None
        if fvg is None or fvg.risk <= 0 or state.equity <= 0:
            return None
        if fvg.risk / bar.close < self._fund_floor:
            return None
        side = Side.BUY if long_sig else Side.SELL
        self._entry_pending = True
        self._signal_bar = index
        self._long_trade = long_sig
        self._mid = fvg.mid
        self._sl = fvg.sl
        self._risk = fvg.risk
        qty = state.equity * self._risk_frac / fvg.risk
        return Order(side, OrderKind.LIMIT, qty, fvg.mid, None, False, self._ttl)

    def _bracket_orders(self, state: StrategyState) -> list[Order]:
        """Брекет на баре исполнения: стоп (замороженный SL) + половина на TP1.

        entry_est = mid: sim.py исполняет касательную LIMIT ровно по цене
        лимита (maker, без проскальзывания), поэтому цели и безубыток
        считаются от фактической цены входа, а R берётся замороженным с
        сигнального бара (стоп и размер — одним решением, §6).
        """
        mid = self._mid
        sl = self._sl
        risk = self._risk
        qty = abs(state.position)
        if mid is None or sl is None or risk is None or qty <= 0 or risk <= 0:
            return []
        self._bracket_qty = qty
        self._exec_bar = state.bar_index
        exit_side = Side.SELL if self._long_trade else Side.BUY
        tp1 = mid + self._tp_mult * risk if self._long_trade else mid - self._tp_mult * risk
        return [
            Order(exit_side, OrderKind.STOP_MARKET, qty, None, sl, True),
            Order(exit_side, OrderKind.LIMIT, qty * _HALF, tp1, None, True),
        ]

    def _manage_trade(self, state: StrategyState) -> list[Order]:
        """После брекета: TP1 -> безубыток; runner -> обратный CHoCH / hard-cap."""
        orders: list[Order] = []
        index = state.bar_index
        exec_bar = self._exec_bar
        mid = self._mid
        if exec_bar is None or mid is None or self._bracket_qty is None:
            return orders
        remaining = abs(state.position)
        exit_side = Side.SELL if self._long_trade else Side.BUY
        if not self._tp1_done and _ZERO < remaining < self._bracket_qty:
            # Единственный способ уменьшиться, не уйдя в ноль, — TP1: стопы и
            # левидация берут весь остаток (семантика sim.py, решение 4).
            self._tp1_done = True
            orders.append(
                Order(exit_side, OrderKind.STOP_MARKET, remaining, None, mid, True)
            )
        if self._exit_pending or remaining <= 0:
            return orders
        reverse_now = (
            self._bear_choch_bar == index
            if self._long_trade
            else self._bull_choch_bar == index
        )
        reverse_hit = reverse_now and self._tp1_done and index > exec_bar
        capped = index - exec_bar >= self._max_hold
        if reverse_hit or capped:
            self._exit_pending = True
            orders.append(
                Order(exit_side, OrderKind.MARKET, remaining, None, None, True)
            )
        return orders

    def _reset_trade(self) -> None:
        """Позиция флэт (или entry мёртв): торговое состояние — полностью в ноль.

        Структурный слой (подтверждённые экстремумы, события) не трогается:
        это состояние рынка, а не сделки.
        """
        self._entry_pending = False
        self._signal_bar = None
        self._long_trade = False
        self._mid = None
        self._sl = None
        self._risk = None
        self._bracket_qty = None
        self._exec_bar = None
        self._tp1_done = False
        self._exit_pending = False
