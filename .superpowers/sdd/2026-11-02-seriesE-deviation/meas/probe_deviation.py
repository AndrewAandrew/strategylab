# -*- coding: utf-8 -*-
"""E-0 read-only зонд zone-deviation reversal — ПРЕЖДЕ заморозки решётки Series E.

Контракт — дословно E-0.1/E-1 амeнднутого DRAFT (2026-11-02(b)). Оси зонда:
  зоны-TF {4h,1d} x касания {2,3} x допуск {.005,.01,.02} x глубина d {.003,.005,.010}
  x подтверждение conf {0 — закрытие обратно; 30,60 — закрепление мин}
  x объём k {1 — фильтр выключен, 2 — vol>=2*медианы 1h И wide-spread |c-o|>=0.6*диапазон}
  x режим {а нет, б конец импульса, в свежесть зоны, г пауза после n стопов}
  x выход {V1_24, V1_48 time-box; V2_R1, V2_R15, V2_R2 bracket} x {BTC, ETH}.

Его правила в механике: пивоты по теням; зона >=touches касаний с >=10 барами
зоны-TF между соседними; вход строго после возврата закрытия («не раньше», /202);
стоп = экстремум событийного бара, буфер 0 (/202 «стоп за максимум ложного
пробоя»); тейк = entry + R*(entry-stop), пол R:R=1 (его /206); б/у на TP1 не
применим при полном выходе (E-4(g)); трение/филлы — конвенция серии A/D: вход
open следующего 1h-бара adverse + slip 0.0005, fee 0.0006*CAPITAL на ногу.

БЕЗ LOOK-AHEAD (главное отличие от реcon №2, где кластеризация шла по всему окну):
зона подтверждается на ts(touches-го касания) + W*tf_secs и участвует только в
решениях, принятых позже; свежесть (в) считает касания строго ДО события.

Константы зонда (не оси; пороги уходят за freeze, подбором не спасаются):
  W=3 — пивот: строгий экстремум тени среди +-W баров зоны-TF; тень событийного
    бара, приходящаяся в пределах W от касания, снимает пивот этого касания (так и
    остаётся в кластере следующего порога) — конвенция зонда, наследуется sim'ом;
  band-anchor: новое касаниеjoin'итcя к кластеру, только если в допуске от медианы
    кластера (цепочка не уплывает на неограниченную дистанцию);
  импульс б: движение К зоне >= 3.0% за 24h до закрытия событийного бара;
  свежесть в: последнее касание зоны <= 30 дней до события;
  пауза г: 2 выхода по стопу подряд => пропуск входов 30 дней;
  кулдаун зоны: 48 (1h-баров) между её событиями;
  stuck-горизонт: 6 ч (не вернулась за горизонт => закрепление = 999 мин, проходит
    любой conf — консервативно к «повисла надолго»).

Гейты прокси no-go (зафиксированы в DRAFT до чисел; §8 не двигается):
клетка жива, если E2-1' net$>=0 в каждой из трёх 60d треть пула 180d (proxy фолдов,
строже), E2-2 pooled annualized >= +3.0%/год, E2-5 кругов на пуле <= 75.
Ни одной живой => Series E не пишется.

float64 — QA-уровень зонда (боевой sim после freeze — Decimal, прецедент C-0/D-0).
Базы mode=ro.

Запуск:
  python probe_deviation.py            # полный прогон: stdout -> E0-...-raw.txt
  python probe_deviation.py --smoke    # S1..S9 с ручными ожиданиями + детерминизм
"""
from __future__ import annotations

import bisect
import sqlite3
import sys
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[4]  # meas -> series -> sdd -> .superpowers -> repo
END = 1790294400  # конец эпохи fit; ts >= END не читаем (R-next-2)
START = END - 180 * 86400
THIRD = 60 * 86400

CAPITAL = 10_000.0
FEE = 0.0006
SLIP = 0.0005
HURDLE = 0.03          # E2-2, заморожена в DRAFT
MAX_ROUNDS_POOL = 75   # E2-5: <=25/фолд, фолд-прокси 60d x3

W = 3
MIN_GAP = 10
TFS = (("4h", 4 * 3600), ("1d", 24 * 3600))
TOUCHES = (2, 3)
TOLS = (0.005, 0.010, 0.020)
DEPTHS = (0.003, 0.005, 0.010)
CONFS = (0, 30, 60)
VOLS = (1, 2)
EXITS = (("V1_24", 24, 0.0), ("V1_48", 48, 0.0),
         ("V2_R1", 0, 1.0), ("V2_R15", 0, 1.5), ("V2_R2", 0, 2.0))
REGIMES = ("а", "б", "в", "г")
IMP_MIN, IMP_H = 0.03, 24
FRESH_S = 30 * 86400
PAUSE_STOPS, PAUSE_S = 2, 30 * 86400
COOLDOWN = 48
STUCK_CAP_MIN = 360

SIDE = {"S": 1, "R": -1}  # long от отбоя поддержки, short от отбоя сопротивления


def agg(rows: list[tuple], secs: int) -> list[tuple]:
    """1m-строки (ts,o,h,l,c,v) -> бары бакетов: (ts, low, high, open, close, vol)."""
    b: dict[int, list] = {}
    for ts, o, h, l, c, v in rows:
        b.setdefault((ts // secs) * secs, []).append((o, h, l, c, v))
    return [(t, min(x[2] for x in vs), max(x[1] for x in vs), vs[0][0], vs[-1][3],
             sum(x[4] for x in vs)) for t, vs in sorted(b.items())]


def pivots(bars) -> list[tuple]:
    """Строгие экстремумы по теням среди +-W соседей: (i, price, kind)."""
    out = []
    for i in range(W, len(bars) - W):
        lo, hi = bars[i][1], bars[i][2]
        nb = [j for j in range(i - W, i + W + 1) if j != i]
        if all(lo < bars[j][1] for j in nb):
            out.append((i, lo, "S"))
        if all(hi > bars[j][2] for j in nb):
            out.append((i, hi, "R"))
    return out


def build_zones(piv, bucket0, tf_secs, touches, tol):
    """Кластеры касаний по времени (его MIN_GAP + допуск от медианы кластера).

    Зона подтверждается на ts(touches-го участника) + W*tf_secs. Возвращает
    [{ref, confirm_ts, kind, touch_ts:[...]}] — touch_ts только участники кластера;
    (см. inline attach в run_full) расширение (любые касания полосы позже) делает attach_touches.
    """
    out = []
    for kind in ("S", "R"):
        seq = [(i, p) for i, p, k in piv if k == kind]
        cl: list[tuple] = []
        for i, p in seq:
            if cl:
                ref = median([x[1] for x in cl])
                ok = (i - cl[-1][0] >= MIN_GAP) and abs(p - ref) / ref <= tol
            else:
                ok = False
            if ok:
                cl.append((i, p))
            else:
                if len(cl) >= touches:
                    out.append({"ref": median([x[1] for x in cl]), "kind": kind,
                                "confirm_ts": bucket0 + cl[touches - 1][0] * tf_secs + W * tf_secs,
                                "touch_ts": [bucket0 + x[0] * tf_secs for x in cl]})
                cl = [(i, p)]
        if len(cl) >= touches:
            out.append({"ref": median([x[1] for x in cl]), "kind": kind,
                        "confirm_ts": bucket0 + cl[touches - 1][0] * tf_secs + W * tf_secs,
                        "touch_ts": [bucket0 + x[0] * tf_secs for x in cl]})
    return out


def scan_events(bars1h, m1, zones, tol):
    """Кандидаты ложного пробоя по 1h-барам: глубина, stuck, объём/шейп. Без d/conf/фильтров."""
    n = len(bars1h)
    volmed = median(x[5] for x in bars1h)
    m_ts = [r[0] for r in m1]
    m_c = [float(r[4]) for r in m1]
    last_i = {}
    cands = []
    for i in range(1, n):  # i=0 не может быть решением (нужен следующий бар)
        ts, lo, hi, op, cl, v = bars1h[i]
        end_ts = ts + 3600
        best = {}
        for z in zones:
            if end_ts < z["confirm_ts"]:
                continue
            L = z["ref"]
            if z["kind"] == "S":
                depth = (L - lo) / L
                ev = depth >= DEPTHS[0] and cl > L
            else:
                depth = (hi - L) / L
                ev = depth >= DEPTHS[0] and cl < L
            if not ev:
                continue
            if last_i.get(id(z), -999_999) + COOLDOWN > i:
                continue
            if z["kind"] not in best or depth > best[z["kind"]]["depth"]:
                best[z["kind"]] = {"i": i, "ts": ts, "kind": z["kind"], "ref": L,
                                   "depth": depth, "zid": id(z)}
        for kind, c in best.items():
            z = next(zz for zz in zones if id(zz) == c["zid"])
            L = c["ref"]
            lo2 = bisect.bisect_left(m_ts, end_ts)
            hi2 = bisect.bisect_left(m_ts, end_ts + STUCK_CAP_MIN * 60 + 120)
            stuck = STUCK_CAP_MIN + 39  # sentinel 999-подобный: не пробита обратно
            for j in range(lo2, hi2):
                if (kind == "S" and m_c[j] < L) or (kind == "R" and m_c[j] > L):
                    stuck = int((m_ts[j] - end_ts) // 60)
                    break
            c["stuck"] = stuck
            c["volok"] = v >= 2 * volmed
            c["wide"] = abs(cl - op) >= 0.6 * max(hi - lo, 1e-12)
            c["confirm_before"] = z["confirm_ts"]
            c["touch_ts"] = z["touch_ts"]
            last_i[c["zid"]] = i
            cands.append(c)
    return cands


def regime_ok(c, bars1h, reg):
    if reg == "а" or reg == "г":
        return True
    if reg == "б":
        i = c["i"]
        if i < IMP_H:
            return False
        base = bars1h[i - IMP_H][4]
        now = bars1h[i][4]
        return ((base - now) / base >= IMP_MIN) if c["kind"] == "S" else ((now - base) / base >= IMP_MIN)
    # в: касание строго ДО конца событийного бара, <=30d назад
    past = [t for t in c["touch_ts"] if t <= c["ts"]]
    return bool(past) and (c["ts"] - max(past)) <= FRESH_S


def walk_exit(bars1h, j_fill, side, entry_px, stop_px, hold_h, r_mult, end_i):
    """(exit_px, reason, exit_bar). V2: стоп проверяется раньше тейка в баре (консервативно)."""
    if r_mult == 0.0:  # V1 time-box
        b = min(j_fill + hold_h, end_i)
        px = bars1h[b][4] * ((1 - SLIP) if side == 1 else (1 + SLIP))
        return px, ("tb" if b == j_fill + hold_h else "force"), b
    risk = (entry_px - stop_px) * side
    if risk <= 0:
        return None, "bad", j_fill
    take_px = entry_px + side * r_mult * risk
    for b in range(j_fill, end_i + 1):
        lo, hi = bars1h[b][1], bars1h[b][2]
        if (side == 1 and lo <= stop_px) or (side == -1 and hi >= stop_px):
            return stop_px * ((1 - SLIP) if side == 1 else (1 + SLIP)), "stop", b
        if (side == 1 and hi >= take_px) or (side == -1 and lo <= take_px):
            return take_px * ((1 - SLIP) if side == 1 else (1 + SLIP)), "take", b
    px = bars1h[end_i][4] * ((1 - SLIP) if side == 1 else (1 + SLIP))
    return px, "force", end_i


def cell_rounds(pool, bars1h, reg, conf, d, exit_name, hold_h, r_mult):
    """Последовательный проход клетки; «г» — машина пауз по выходам-стопами."""
    end_i = len(bars1h) - 1
    h_ts = [b[0] for b in bars1h]
    rounds, paused_until, consec = [], -1, 0
    for c in pool:
        if c["depth"] < d or (conf > 0 and c["stuck"] < conf):
            continue
        side = SIDE[c["kind"]]
        decision_ts = c["ts"] + 3600 + conf * 60 if conf > 0 else c["ts"] + 3600
        j_fill = bisect.bisect_right(h_ts, decision_ts - 1)  # первый бар с ts > decision
        if j_fill > end_i - 1:
            continue
        entry_px = bars1h[j_fill][3] * ((1 + SLIP) if side == 1 else (1 - SLIP))
        stop_px = bars1h[c["i"]][1] if side == 1 else bars1h[c["i"]][2]
        if reg == "г" and c["ts"] < paused_until:
            continue
        px, reason, _ = walk_exit(bars1h, j_fill, side, entry_px, stop_px, hold_h, r_mult, end_i)
        if px is None:
            continue
        net = CAPITAL * side * (px / entry_px - 1) - 2 * CAPITAL * FEE
        rounds.append({"ts": c["ts"], "net": net, "reason": reason})
        if reg == "г":
            consec = consec + 1 if reason == "stop" else 0
            if consec >= PAUSE_STOPS:
                paused_until, consec = c["ts"] + PAUSE_S, 0
    return rounds


def report(sym, tfn, touches, tol, d, conf, k, reg, en, rounds):
    n = len(rounds)
    q, nq = [0.0] * 3, [0] * 3
    for r in rounds:
        t = int((r["ts"] - START) // THIRD)
        if 0 <= t < 3:
            q[t] += r["net"]
            nq[t] += 1
    tot = sum(r["net"] for r in rounds)
    med = 100 * median(r["net"] / CAPITAL for r in rounds)
    mean = 100 * tot / n / CAPITAL
    win = 100 * sum(1 for r in rounds if r["net"] > 0) / n
    ann = tot / CAPITAL * 365 / 180
    live = all(x >= 0 for x in q) and ann >= HURDLE and n <= MAX_ROUNDS_POOL
    return (f"{sym} {tfn} tch{touches} tol{tol:.3f} d{d:.3f} conf{conf} k{k} reg{reg} {en:6s} "
            f"n={n} nq={nq[0]}/{nq[1]}/{nq[2]} med={med:+.2f}% mean={mean:+.2f}% win={win:.0f}% "
            f"pool$={tot:+.0f} ann={100 * ann:+.2f}% q$={q[0]:+.0f}/{q[1]:+.0f}/{q[2]:+.0f}"
            + (" LIVE" if live else ""))


def load_bars(db, sym):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars_1m"
        " WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts", (sym, START, END)).fetchall()
    conn.close()
    return [(float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def run_full():
    print("# E-0 зонд zone-deviation | пул 180d [%d,%d) | no-go: 0 живых => серия не пишется" % (START, END))
    print("# живая клетка: net$>=0 в каждой 60d трети И pooled ann>=+3.0%% И n_pool<=75")
    survivors = total_cells = zero_rows = 0
    for sym, dbname in (("BTCUSDT", "btcusdt.db"), ("ETHUSDT", "ethusdt.db")):
        m1 = load_bars(str(REPO / "data" / dbname), sym)
        bars1h = agg(m1, 3600)
        print(f"# {sym}: 1hбаров={len(bars1h)} 1mстрок={len(m1)}")
        for tfn, tf_secs in TFS:
            zb = agg(m1, tf_secs)
            piv = pivots(zb)
            bucket0 = zb[0][0]
            for touches in TOUCHES:
                for tol in TOLS:
                    zones = build_zones(piv, bucket0, tf_secs, touches, tol)
                    if not zones:
                        continue
                    # attach свежести: все касания полосы зоны (tol зоны), ts позже не возмут —
                    # в filter смотрит touch_ts <= ts решения
                    for z in zones:
                        ts_all = list(z["touch_ts"])
                        for i, p, k in piv:
                            if k == z["kind"] and abs(p - z["ref"]) / z["ref"] <= tol:
                                ts_all.append(bucket0 + i * tf_secs)
                        z["touch_ts"] = sorted(set(ts_all))
                    cands = scan_events(bars1h, m1, zones, tol)
                    if not cands:
                        continue
                    for k in VOLS:
                        poolk = [c for c in cands if k == 1 or (c["volok"] and c["wide"])]
                        for reg in REGIMES:
                            pool = [c for c in poolk if regime_ok(c, bars1h, reg)]
                            for conf in CONFS:
                                for d in DEPTHS:
                                    any_exit = False
                                    for en, hh, rm in EXITS:
                                        total_cells += 1
                                        rounds = cell_rounds(pool, bars1h, reg, conf, d, en, hh, rm)
                                        if not rounds:
                                            zero_rows += 1
                                            continue
                                        any_exit = True
                                        line = report(sym, tfn, touches, tol, d, conf, k, reg, en, rounds)
                                        print(line)
                                        if line.endswith(" LIVE"):
                                            survivors += 1
                                    if not any_exit:
                                        print(f"# пусто: {sym} {tfn} tch{touches} tol{tol:.3f} d{d:.3f} conf{conf} k{k} reg{reg}")
    print(f"# ИТОГ: клеток просмотрено {total_cells}, непустых {total_cells - zero_rows}, живых по no-go {survivors}")
    return survivors


def smoke():
    # S1: agg — границы бакетов, OHLCV
    rows = [(0, 1.0, 2.0, 0.5, 1.5, 10.0), (60, 1.5, 3.0, 1.2, 2.5, 5.0), (3600, 2.0, 2.2, 1.9, 2.1, 1.0)]
    b = agg(rows, 3600)
    assert len(b) == 2 and b[0][1] == 0.5 and b[0][2] == 3.0 and b[0][3] == 1.0 \
        and b[0][4] == 2.5 and b[0][5] == 15.0 and b[1][0] == 3600
    print("S1 agg OK")
    # S2: pivots — строгий экстремум тени на баре 3 (окно +-W=3 внутри массива из 8)
    lo_seq = [10, 9, 8.5, 8, 8.5, 9, 10, 10]
    bs = [(t, l, l + 2, l, l, 1.0) for t, l in enumerate(lo_seq)]
    pv = pivots(bs)
    assert pv == [(3, 8, "S")], pv
    print("S2 pivots OK")
    # S3: build_zones — касания с зазором >=10, допуск от медианы, confirm = ts2 + W*tf
    tf = 4 * 3600
    zs = build_zones([(0, 99.0, "S"), (12, 99.05, "S"), (20, 97.0, "S")], 0, tf, 2, 0.005)
    assert len(zs) == 1 and abs(zs[0]["ref"] - 99.025) < 1e-9
    assert zs[0]["confirm_ts"] == 12 * tf + 3 * tf and zs[0]["kind"] == "S"
    assert not build_zones([(0, 99.0, "S"), (5, 99.0, "S")], 0, tf, 2, 0.005)          # зазор < MIN_GAP
    assert len(build_zones([(0, 99.0, "S"), (12, 99.0, "S"), (30, 101.0, "S")], 0, tf, 2, 0.005)) == 1
    print("S3 zones/MIN_GAP/band/confirm OK")
    # S4..S6: синтетика 1m (91 час): зона support 99.0 (касания h=12 и h=52), ложный пробой h=68
    # (событие дальше W=3 бакетов от касания — строгий пивот не «съедается» самим пробоем)
    m1 = []

    def put_hr(ht, lo, hi, op, cl, vol=60.0):
        for mm in range(60):
            m1.append((ht + mm * 60, op, hi, lo, cl, vol / 60))

    for h in range(0, 91):
        t = h * 3600
        if h in (12, 52):
            lo, hi, op, cl = 99.0, 101.0, 100.0, 100.0
        elif h == 68:
            lo, hi, op, cl = 98.4, 100.2, 99.8, 100.0          # low на 0.6% ниже зоны, возврат
        elif h >= 69:
            px = 100.0 + min(h - 68, 6) * 0.4                  # рост к 102.4 и плато
            lo, hi, op, cl = px - 0.1, px + 0.1, px - 0.05, px
        else:
            lo, hi, op, cl = 99.5, 100.6, 100.0, 100.0
        put_hr(t, lo, hi, op, cl)
    m1.sort()
    bars1h = agg(m1, 3600)
    zsb = build_zones(pivots(agg(m1, tf)), 0, tf, 2, 0.005)
    assert len(zsb) == 2 and all(z["confirm_ts"] == 64 * 3600 for z in zsb), zsb  # S@99.0 и R@101.0
    cands = scan_events(bars1h, m1, zsb, 0.005)
    assert len(cands) == 1, cands
    c = cands[0]
    assert c["i"] == 68 and c["kind"] == "S" and abs(c["depth"] - (99.0 - 98.4) / 99.0) < 1e-12
    assert c["stuck"] > STUCK_CAP_MIN                          # обратно не пробила никогда
    print("S4 event без look-ahead + depth/stuck OK (d=%.5f)" % c["depth"])
    rr = cell_rounds(cands, bars1h, "а", 0, 0.003, "V1", 24, 0.0)
    e = bars1h[69][3] * (1 + SLIP)
    x = bars1h[90][4] * (1 - SLIP)                             # 69+24=93 > end => форс
    assert len(rr) == 1 and rr[0]["reason"] == "force"
    assert abs(rr[0]["net"] - (CAPITAL * (x / e - 1) - 2 * CAPITAL * FEE)) < 1e-9
    print("S5 V1 time-box round OK (%.4f$)" % rr[0]["net"])
    rr2 = cell_rounds(cands, bars1h, "а", 0, 0.003, "V2", 0, 1.0)
    stop = bars1h[68][1]
    take = e + 1.0 * (e - stop)
    r2 = rr2[0]
    assert r2["reason"] == "take"
    assert abs(r2["net"] - (CAPITAL * (take * (1 - SLIP) / e - 1) - 2 * CAPITAL * FEE)) < 1e-9
    print("S6 V2 R=1 take OK")
    # S7: d-порог (depth 0.6% < 1% => нет круга); stuck=45 => conf30 круг, conf60 нет
    assert not cell_rounds(cands, bars1h, "а", 0, 0.010, "V1", 24, 0.0)
    c7 = dict(c)
    c7["stuck"] = 45
    assert len(cell_rounds([c7], bars1h, "а", 30, 0.003, "V1", 24, 0.0)) == 1
    assert not cell_rounds([c7], bars1h, "а", 60, 0.003, "V1", 24, 0.0)
    print("S7 d/conf-гейты OK")
    # S8: режимы — б импульс (плоский до события => reject); в свежесть касаний ДО события
    assert not regime_ok(c, bars1h, "б")
    assert regime_ok(dict(c, touch_ts=[12 * tf, 52 * tf, 66 * 3600]), bars1h, "в")
    assert not regime_ok(dict(c, touch_ts=[66 * 3600 + 31 * 86400]), bars1h, "в")  # только будущий touch
    print("S8 regime б/в OK")
    # S9: детерминизм x2 по всей решётке на синтетике
    la = str([[ (r["net"], r["reason"]) for r in cell_rounds(cands, bars1h, rg, cf, dd, en, hh, rm)]
              for rg in REGIMES for cf in CONFS for dd in DEPTHS for en, hh, rm in EXITS])
    lb = str([[ (r["net"], r["reason"]) for r in cell_rounds(cands, bars1h, rg, cf, dd, en, hh, rm)]
              for rg in REGIMES for cf in CONFS for dd in DEPTHS for en, hh, rm in EXITS])
    assert la == lb
    print("S9 determinism x2 OK")
    print("SMOKE PASS")

if __name__ == "__main__":
    if "--smoke" in sys.argv:
        smoke()
    else:
        run_full()
