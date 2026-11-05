"""Симулятор zone-deviation reversal — Series E + расширение Series V (ложный пробой
HTF-зоны + возврат).

Контракт E заморожен в E-2-F `.superpowers/sdd/2026-11-02-seriesE-deviation/
seriesE-deviation-preregistration-DRAFT.md` (решётка 18 клеток, владелец «ок»).
Расширение V — V-2-SIM AMENDMENT `.superpowers/sdd/2026-11-03-seriesV-altcoins/
V2-SIM-AMENDMENT-FROZEN.md` §1 (заморожен 2026-11-05 до sim-чисел): РОВНО три добавки
к E-движку — (1) exit_mode V1_24/V2_R1/V2_R15 (семантика `_walk_exit` 1-в-1 probe
`walk_exit`), (2) regime а/в/г (свежесть 30d и пауза после 2 стопов, 1-в-1 probe
`regime_ok`/`cell_rounds`; «б» валидатор отвергает), (3) touch_ts зон/событий с attach
1-в-1 probe `run_full`. Дефолты E (regime=а, V1_48/V2_R2) обязано давать побайтно те
же net/journal/rounds; «б» и «в/г»-фильтры E-чисел не касаются.
Механика следует зонду E-0 (`meas/probe_deviation.py`) 1-в-1, кроме единственного
признанного ДО чисел ужесточения: k2-фильтр сравнивает объём события с
trailing-медианой последних <=480 1h-баров (20d; метка «30d» в E-2-F —
описка подписи, замороженное число 480 — закон), включая событийный бар,
минимум 200 истории вместе с ним, а не с медианой всего пула
(микро-look-ahead зонда). Чистые данные на входе (1m-бары, допустим
контекст до start_ts), детерминированный Decimal-расчёт, без доступа к БД и без
касания J/S-ядра (E-5, прецедент carry.py).

Модель (дословно freeze E-2-F; дефолтный regime «а» побайтно = E, «в»/«г»
включаются только параметром, «б» — артефакт черновика, отвергается):
- зона: кластер строгих пивотов тени (экстремум среди +-W=3 баров зоны-TF),
  >=touches касаний, >=MIN_GAP=10 баров зоны-TF между соседними, допуск от
  МЕДИАНЫ кластера; подтверждена с ts(touch_{touches}) + W*tf_secs — более ранние
  решения её не видят (no-lookahead);
- событие: 1h-бар пробил границу зоны глубиной >= SCAN_MIN_DEPTH=0.003 (порог
  скана зонда; d клетки — stricter gate на стадии круга) и закрылся ОБРАТНО строго
  за уровнем; на бар — лучший (максимум глубины) кандидат каждого kind; кулдаун
  зоны 48 1h-баров применяется на стадии скана (семантика зонда);
- stuck: минуты от закрытия события до первого 1m-close обратно за уровнем
  (горизонт 360 мин; иначе sentinel 399); conf {0,30,60}: требуется stuck >= conf,
  решение на ts события + 1h + conf*60, вход — open первого 1h-бара с ts >=
  решения, adverse +/- slippage;
- выходы: V1_24/V1_48 — time-box 24/48 1h-баров, close бара выхода; V2_R1/
  V2_R15/V2_R2 — тейк entry + {1; 1.5; 2}*risk, стоп-проверка раньше тейка
  внутри бара (консервативно), risk <= 0 — круг пропускается;
  форс-выход — CLOSE последнего бара фолда
  (как зонд; у carry был open — здесь зеркало зонда);
- доллар: net круга = capital*side*(exit_fill/entry_fill - 1) - 2*capital*fee;
  трение 0.0006 fee за ногу (от capital) + 0.0005 slip adverse; capital $10k/круг;
- фолд [start_ts, end_ts): события — только по барам ts >= start_ts, бары и
  любые данные с ts >= end_ts запрещены (holdout — ответственность вызывающего).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from statistics import median

from strategylab.core.bars import Bar

ZERO = Decimal("0")
ONE = Decimal("1")

# Замороженные константы E-2-F (вне осей решётки), наследуются зондом 1-в-1.
W = 3
MIN_GAP = 10
SEC_1H = 3600
COOLDOWN_1H_BARS = 48
STUCK_HORIZON_MIN = 360
STUCK_SENTINEL_MIN = 399
SCAN_MIN_DEPTH = Decimal("0.003")
VOL_TRAIL_BARS = 480
VOL_MIN_BARS = 200
VOL_MULT = Decimal("2")
WIDE_SPREAD = Decimal("0.6")
EPS_RANGE = Decimal("1E-12")
CONF_VALID = (0, 30, 60)
VOL_FILTERS = (1, 2)
V1_HOLD_HOURS = 48
V2_R_MULT = Decimal("2")
# amendment §1.1: V-режимы; семантика 1-в-1 probe EXITS/walk_exit.
EXIT_MODES = ("V1_48", "V2_R2", "V1_24", "V2_R1", "V2_R15")
TIMEBOX_HOURS_BY_MODE = {"V1_24": 24, "V1_48": V1_HOLD_HOURS}
R_MULT_BY_MODE = {"V2_R1": Decimal("1"), "V2_R15": Decimal("1.5"), "V2_R2": V2_R_MULT}
# amendment §1.2: режимы а/в/г («б» — артефакт черновика, не машина).
REGIMES = ("а", "в", "г")
FRESH_SECONDS = 30 * 86400  # regime «в»: max(ts - last touch)
PAUSE_STOPS = 2  # regime «г»: consecutive stop_losses -> pause
PAUSE_SECONDS = 30 * 86400  # regime «г»: pause length


@dataclass(frozen=True, slots=True)
class DeviationParams:
    """Параметры клетки решётки E-2-F (tol/depth/conf/k/exit — оси; остальное — константы)."""

    tol: Decimal
    depth: Decimal
    conf_min: int
    vol_filter: int  # 1 — выключен; 2 — trailing-медиана объёма И wide-spread
    exit_mode: str  # "V1_48" | "V2_R2" | "V1_24" | "V2_R1" | "V2_R15"
    zone_tf_secs: int = 4 * SEC_1H
    touches: int = 2
    capital: Decimal = Decimal("10000")
    taker_fee: Decimal = Decimal("0.0006")
    slippage: Decimal = Decimal("0.0005")
    regime: str = "а"  # amendment §1.2: "а" | "в" | "г" ("б" — ValueError)


@dataclass(frozen=True, slots=True)
class Zone:
    """Подтверждённая зона: ref — медиана кластера, kind S(upport)/R(esistance)."""

    ref: Decimal
    kind: str
    confirm_ts: int
    touch_ts: tuple[int, ...] = ()  # amendment §1.3: sorted, zone-TF


@dataclass(frozen=True, slots=True)
class Event:
    """Кандидат ложного пробоя на стадии скана (до d/conf/k фильтров клетки)."""

    i: int
    ts: int
    kind: str
    ref: Decimal
    depth: Decimal
    stuck: int
    volok: bool
    wide: bool
    touch_ts: tuple[int, ...] = ()  # amendment §1.3: verbatim from zone


@dataclass(frozen=True, slots=True)
class Round:
    """Круг клетки: цены ref — без slip, fill — adverse; net — итог долларами."""

    ts: int
    kind: str
    side: int
    entry_ts: int
    entry_ref: Decimal
    entry_fill: Decimal
    stop_px: Decimal
    exit_ts: int
    exit_ref: Decimal
    exit_fill: Decimal
    reason: str  # "stop" | "take" | "tb" | "force"
    net: Decimal


@dataclass(frozen=True, slots=True)
class JournalRow:
    """Строка cash-flow: entry/exit; cash_delta — в долларах, entry нога — минус fee."""

    ts: int
    kind: str
    side: str
    ref: Decimal
    fill: Decimal
    qty: Decimal
    reason: str
    cash_delta: Decimal
    cash_after: Decimal


@dataclass(frozen=True, slots=True)
class DeviationResult:
    rounds: list[Round]
    journal: list[JournalRow]
    rounds_count: int
    zones_count: int
    events_count: int
    price_gross: Decimal
    friction: Decimal
    net: Decimal
    equity: Decimal
    n_stops: int
    n_takes: int
    n_tb: int
    n_forced: int
    flat_fold: bool


def aggregate(bars: Sequence[Bar], secs: int) -> list[Bar]:
    """Кастомные бары бакетов (ts — низ, кратный secs) из упорядоченных 1m-баров."""
    buckets: dict[int, list[Bar]] = {}
    for b in bars:
        buckets.setdefault((b.ts // secs) * secs, []).append(b)
    out: list[Bar] = []
    for ts in sorted(buckets):
        vs = buckets[ts]
        out.append(
            Bar(
                ts=ts,
                open=vs[0].open,
                high=max(v.high for v in vs),
                low=min(v.low for v in vs),
                close=vs[-1].close,
                volume=sum((v.volume for v in vs), ZERO),
            )
        )
    return out


def find_pivots(bars: Sequence[Bar]) -> list[tuple[int, Decimal, str]]:
    """Строгие экстремумы тени среди +-W соседей: (индекс бара, цена, S|R)."""
    out: list[tuple[int, Decimal, str]] = []
    for i in range(W, len(bars) - W):
        lo, hi = bars[i].low, bars[i].high
        nb = [j for j in range(i - W, i + W + 1) if j != i]
        if all(lo < bars[j].low for j in nb):
            out.append((i, lo, "S"))
        if all(hi > bars[j].high for j in nb):
            out.append((i, hi, "R"))
    return out


def _finalize_cluster(
    cl: Sequence[tuple[int, Decimal]], touches: int, bucket0: int, tf_secs: int, kind: str
) -> Zone | None:
    if len(cl) < touches:
        return None
    return Zone(
        ref=median([x[1] for x in cl]),
        kind=kind,
        confirm_ts=bucket0 + cl[touches - 1][0] * tf_secs + W * tf_secs,
        touch_ts=tuple(bucket0 + x[0] * tf_secs for x in cl),
    )


def attach_touches(
    zones: Sequence[Zone],
    pivots: Sequence[tuple[int, Decimal, str]],
    bucket0: int,
    tf_secs: int,
    tol: Decimal,
) -> list[Zone]:
    """Amendment §1.3, 1-в-1 inline attach зонда (run_full): к каждой зоне — все
    касания полосы (тот же kind, |p-ref|/ref <= tol, допуск МЕДИАНЫ зоны, а не
    кластера); ts позже фильтрацию не возмутят — «в» смотрит touch_ts <= ts."""
    out: list[Zone] = []
    for z in zones:
        ts_all = list(z.touch_ts)
        for i, p, k in pivots:
            if k == z.kind and abs(p - z.ref) / z.ref <= tol:
                ts_all.append(bucket0 + i * tf_secs)
        out.append(replace(z, touch_ts=tuple(sorted(set(ts_all)))))
    return out


def build_zones(
    pivots: Sequence[tuple[int, Decimal, str]],
    bucket0: int,
    tf_secs: int,
    touches: int,
    tol: Decimal,
) -> list[Zone]:
    """Кластеры касаний по времени (MIN_GAP между соседними, допуск от медианы
    кластера — цепочка не уплывает). Зона подтверждена с ts(touch_{touches}) + W*tf."""
    zones: list[Zone] = []
    for kind in ("S", "R"):
        seq = [(i, p) for i, p, k in pivots if k == kind]
        cl: list[tuple[int, Decimal]] = []
        for i, p in seq:
            join = False
            if cl:
                ref = median([x[1] for x in cl])
                join = (i - cl[-1][0] >= MIN_GAP) and abs(p - ref) / ref <= tol
            if join:
                cl.append((i, p))
            else:
                zone = _finalize_cluster(cl, touches, bucket0, tf_secs, kind)
                if zone is not None:
                    zones.append(zone)
                cl = [(i, p)]
        zone = _finalize_cluster(cl, touches, bucket0, tf_secs, kind)
        if zone is not None:
            zones.append(zone)
    return zones


def _stuck_minutes(
    m_ts: Sequence[int], m_close: Sequence[Decimal], end_ts: int, kind: str, level: Decimal
) -> int:
    """Минуты от закрытия события до первого 1m-close ОБРАТНО за уровнем; за
    горизонтом 360 мин — sentinel 399 (проходит любой conf)."""
    lo_i = bisect_left(m_ts, end_ts)
    hi_i = bisect_left(m_ts, end_ts + STUCK_HORIZON_MIN * 60 + 120)
    for j in range(lo_i, hi_i):
        if (kind == "S" and m_close[j] < level) or (kind == "R" and m_close[j] > level):
            return (m_ts[j] - end_ts) // 60
    return STUCK_SENTINEL_MIN


def _trailing_volok(bars1h: Sequence[Bar], i: int) -> bool:
    """Ужесточение freeze (признано до чисел): объём >= 2*медианы последних
    <=480 1h-баров, включая событийный; при истории <200 баров — False (не стреляет)."""
    lo_i = max(0, i - VOL_TRAIL_BARS + 1)
    window = bars1h[lo_i : i + 1]
    if len(window) < VOL_MIN_BARS:
        return False
    return bars1h[i].volume >= VOL_MULT * median([b.volume for b in window])


def _wide_spread(b: Bar) -> bool:
    """Shape-фильтр VSA-кульминации: |close-open| >= 0.6*(high-low), как в зонде."""
    return abs(b.close - b.open) >= WIDE_SPREAD * max(b.high - b.low, EPS_RANGE)


def _scan_events(
    bars1h: Sequence[Bar],
    m_ts: Sequence[int],
    m_close: Sequence[Decimal],
    zones: Sequence[Zone],
    first_i: int,
) -> list[Event]:
    """Кандидаты по 1h-барам с ts >= start_ts (i=0 не может быть решением — нужен
    следующий бар). На бар — глубочайшая зона каждого kind; кулдаун 48×1h — здесь же
    (семантика скана зонда, независима от d/conf/k клетки)."""
    cands: list[Event] = []
    last_i: dict[int, int] = {}
    n = len(bars1h)
    for i in range(max(1, first_i), n):
        b = bars1h[i]
        close_ts = b.ts + SEC_1H
        best: dict[str, tuple[Decimal, int]] = {}
        for zi, z in enumerate(zones):
            if close_ts < z.confirm_ts:
                continue
            if z.kind == "S":
                depth = (z.ref - b.low) / z.ref
                fired = depth >= SCAN_MIN_DEPTH and b.close > z.ref
            else:
                depth = (b.high - z.ref) / z.ref
                fired = depth >= SCAN_MIN_DEPTH and b.close < z.ref
            if not fired:
                continue
            if last_i.get(zi, -999_999) + COOLDOWN_1H_BARS > i:
                continue
            cur = best.get(z.kind)
            if cur is None or depth > cur[0]:
                best[z.kind] = (depth, zi)
        for kind, (depth, zi) in best.items():
            cands.append(
                Event(
                    i=i,
                    ts=b.ts,
                    kind=kind,
                    ref=zones[zi].ref,
                    depth=depth,
                    stuck=_stuck_minutes(m_ts, m_close, close_ts, kind, zones[zi].ref),
                    volok=_trailing_volok(bars1h, i),
                    wide=_wide_spread(b),
                    touch_ts=zones[zi].touch_ts,
                )
            )
            last_i[zi] = i
    return cands


def _exit_fill(price: Decimal, side: int, params: DeviationParams) -> Decimal:
    return price * ((ONE - params.slippage) if side == 1 else (ONE + params.slippage))


def _walk_exit(
    bars1h: Sequence[Bar],
    params: DeviationParams,
    j_fill: int,
    side: int,
    entry_px: Decimal,
    stop_px: Decimal,
    end_i: int,
) -> tuple[Decimal, Decimal, str, int] | None:
    """(exit_ref, exit_fill, reason, exit_i), семантика probe walk_exit 1-в-1.
    V2-ветка: стоп проверяется раньше тейка внутри бара (консервативно, как зонд).
    Форс — close последнего бара окна."""
    hold_h = TIMEBOX_HOURS_BY_MODE.get(params.exit_mode)
    if hold_h is not None:  # V1 time-box (r_mult == 0.0 у зонда)
        hold_i = j_fill + hold_h
        b = min(hold_i, end_i)
        exit_kind = "tb" if b == hold_i else "force"
        ref = bars1h[b].close
        return ref, _exit_fill(ref, side, params), exit_kind, b
    risk = (entry_px - stop_px) * side
    if risk <= ZERO:
        return None
    take_px = entry_px + side * R_MULT_BY_MODE[params.exit_mode] * risk
    for b in range(j_fill, end_i + 1):
        lo, hi = bars1h[b].low, bars1h[b].high
        if (side == 1 and lo <= stop_px) or (side == -1 and hi >= stop_px):
            return stop_px, _exit_fill(stop_px, side, params), "stop", b
        if (side == 1 and hi >= take_px) or (side == -1 and lo <= take_px):
            return take_px, _exit_fill(take_px, side, params), "take", b
    ref = bars1h[end_i].close
    return ref, _exit_fill(ref, side, params), "force", end_i


def _is_fresh(ts: int, touch_ts: Sequence[int]) -> bool:
    """Amendment §1.2 «в», 1-в-1 probe regime_ok: касание строго ДО конца
    событийного бара (t <= ts), не дальше FRESH_SECONDS назад."""
    past = [t for t in touch_ts if t <= ts]
    return bool(past) and (ts - max(past)) <= FRESH_SECONDS


def _play_rounds(
    events: Sequence[Event], bars1h: Sequence[Bar], params: DeviationParams
) -> list[Round]:
    """Последовательный проход клетки: d/conf/k-гейты, решение +conf*60, вход open
    следующего 1h-бара adverse. Кулдаун уже учтён на скане; круги независимы.
    «в» — фильтр свежести по touch_ts; «г» — машина пауз probe cell_rounds
    (2 подряд стопа -> пауза PAUSE_SECONDS от ts события, строгое <)."""
    end_i = len(bars1h) - 1
    if end_i < 1:
        return []
    h_ts = [b.ts for b in bars1h]
    friction_legs = 2 * params.capital * params.taker_fee
    rounds: list[Round] = []
    paused_until = -1
    consec = 0
    for e in events:
        if params.regime == "в" and not _is_fresh(e.ts, e.touch_ts):
            continue
        if e.depth < params.depth:
            continue
        if params.conf_min > 0 and e.stuck < params.conf_min:
            continue
        if params.vol_filter == 2 and not (e.volok and e.wide):
            continue
        side = 1 if e.kind == "S" else -1
        decision_ts = e.ts + SEC_1H + params.conf_min * 60
        j_fill = bisect_right(h_ts, decision_ts - 1)
        if j_fill > end_i - 1:
            continue
        entry_ref = bars1h[j_fill].open
        entry_fill = entry_ref * ((ONE + params.slippage) if side == 1 else (ONE - params.slippage))
        stop_px = bars1h[e.i].low if side == 1 else bars1h[e.i].high
        if params.regime == "г" and e.ts < paused_until:
            continue
        walked = _walk_exit(bars1h, params, j_fill, side, entry_fill, stop_px, end_i)
        if walked is None:
            continue
        exit_ref, exit_fill, exit_kind, exit_i = walked
        net = params.capital * side * (exit_fill / entry_fill - ONE) - friction_legs
        rounds.append(
            Round(
                ts=e.ts,
                kind=e.kind,
                side=side,
                entry_ts=bars1h[j_fill].ts,
                entry_ref=entry_ref,
                entry_fill=entry_fill,
                stop_px=stop_px,
                exit_ts=bars1h[exit_i].ts,
                exit_ref=exit_ref,
                exit_fill=exit_fill,
                reason=exit_kind,
                net=net,
            )
        )
        if params.regime == "г":
            consec = consec + 1 if exit_kind == "stop" else 0
            if consec >= PAUSE_STOPS:
                paused_until, consec = e.ts + PAUSE_SECONDS, 0
    return rounds


def run_deviation(
    bars: Sequence[Bar], params: DeviationParams, start_ts: int, end_ts: int
) -> DeviationResult:
    """Прогон одной клетки-фолда. bars — 1m с допустимым контекстом до start_ts;
    любые ts >= end_ts — ошибка (данные фолда обязано резать снаружи)."""
    if params.exit_mode not in EXIT_MODES:
        raise ValueError(f"exit_mode обязан быть одним из {EXIT_MODES}: {params.exit_mode!r}")
    if params.conf_min not in CONF_VALID:
        raise ValueError(f"conf_min обязан быть одним из {CONF_VALID}: {params.conf_min}")
    if params.vol_filter not in VOL_FILTERS:
        raise ValueError(f"vol_filter обязан быть одним из {VOL_FILTERS}: {params.vol_filter}")
    if params.regime not in REGIMES:
        raise ValueError(f"regime обязан быть одним из {REGIMES}: {params.regime!r}")
    if params.touches < 2:
        raise ValueError("touches обязан быть >= 2 (иначе зоны не из чего строить)")
    if params.zone_tf_secs < SEC_1H or params.zone_tf_secs % SEC_1H:
        raise ValueError("zone_tf_secs обязан быть кратен 1h и не меньше 1h")
    if params.tol <= ZERO:
        raise ValueError("tol обязан быть положительным")
    if params.depth < SCAN_MIN_DEPTH:
        raise ValueError(f"depth обязан быть >= порога скана зонда {SCAN_MIN_DEPTH}")
    if params.capital <= ZERO:
        raise ValueError("capital обязан быть положительным")
    if params.taker_fee < ZERO or params.slippage < ZERO:
        raise ValueError("taker_fee и slippage обязаны быть неотрицательными")
    if end_ts <= start_ts:
        raise ValueError(f"пустое/инверсное окно: start {start_ts} end {end_ts}")
    if any(b.ts >= end_ts for b in bars):
        raise ValueError(f"бар за пределами окна: bar.ts >= end_ts ({end_ts})")
    ordered = tuple(sorted(bars, key=lambda b: b.ts))
    if len({b.ts for b in ordered}) != len(ordered):
        raise ValueError("дубликат bar.ts")

    m_ts = [b.ts for b in ordered]
    m_close = [b.close for b in ordered]
    bars1h = aggregate(ordered, SEC_1H)
    zone_bars = aggregate(ordered, params.zone_tf_secs)
    zones: list[Zone] = []
    if zone_bars:
        pivots = find_pivots(zone_bars)
        zones = attach_touches(
            build_zones(
                pivots,
                zone_bars[0].ts,
                params.zone_tf_secs,
                params.touches,
                params.tol,
            ),
            pivots,
            zone_bars[0].ts,
            params.zone_tf_secs,
            params.tol,
        )
    first_i = bisect_left([b.ts for b in bars1h], start_ts)
    events = _scan_events(bars1h, m_ts, m_close, zones, first_i)
    rounds = _play_rounds(events, bars1h, params)

    journal: list[JournalRow] = []
    cash = ZERO
    price_gross = ZERO
    n_stops = n_takes = n_tb = n_forced = 0
    fee_leg = params.taker_fee * params.capital
    for r in rounds:
        qty = params.capital / r.entry_fill
        price_pnl = r.net + 2 * fee_leg
        price_gross += price_pnl
        cash -= fee_leg
        journal.append(
            JournalRow(
                r.entry_ts, "entry", "long" if r.side == 1 else "short",
                r.entry_ref, r.entry_fill, qty, "", -fee_leg, cash,
            )
        )
        delta = price_pnl - fee_leg
        cash += delta
        journal.append(
            JournalRow(
                r.exit_ts, "exit", "long" if r.side == 1 else "short",
                r.exit_ref, r.exit_fill, qty, r.reason, delta, cash,
            )
        )
        if r.reason == "stop":
            n_stops += 1
        elif r.reason == "take":
            n_takes += 1
        elif r.reason == "tb":
            n_tb += 1
        else:
            n_forced += 1

    friction = 2 * fee_leg * len(rounds)
    return DeviationResult(
        rounds=rounds,
        journal=journal,
        rounds_count=len(rounds),
        zones_count=len(zones),
        events_count=len(events),
        price_gross=price_gross,
        friction=friction,
        net=cash,
        equity=params.capital + cash,
        n_stops=n_stops,
        n_takes=n_takes,
        n_tb=n_tb,
        n_forced=n_forced,
        flat_fold=not rounds,
    )
