# -*- coding: utf-8 -*-
"""Смоук probe_carry.py на синтетике с РУЧНЫМИ ожиданиями (прецедент C-0).

Фолд [0, 3600), минуты ts=0..3540. Цены flat, спот/перп раздельно.
"""
import importlib.util
import sys
from pathlib import Path

SRC = Path("C:/Users/egoro/Documents/GitHub/strategylab/.superpowers/sdd/2026-11-02-seriesD-levered-carry/meas/probe_carry.py")
spec = importlib.util.spec_from_file_location("probe_carry", SRC)
assert spec and spec.loader
pc = importlib.util.module_from_spec(spec)
sys.modules["probe_carry"] = pc  # dataclass+annotations требуют регистрации до exec
spec.loader.exec_module(pc)

FEE, SLIP, CAP = pc.FEE, pc.SLIP, pc.CAPITAL


def venue(ts_open: list[tuple[int, float]], hi: dict[int, float] | None = None,
          lo: dict[int, float] | None = None) -> "pc.Venue":
    return pc.Venue([(t, o, (hi or {}).get(t, o), (lo or {}).get(t, o)) for t, o in ts_open])


flat = venue([(t, 100.0) for t in range(0, 3600, 60)])
spot_flat = venue([(t, 101.0) for t in range(0, 3600, 60)])

# ── 1. V1 простой шорт-холд: 3 расчётки +0.0001, вход 660@99.95, форс-выход 3540@100.05
# carry: +1 на 1800, +1 на 3000 (входная 600 не начисляется); price −10; fee 0.0006*10k*2=12
f = [(600, 0.0001), (1800, 0.0001), (3000, 0.0001)]
r = pc.simulate(flat, None, f, 0, 3600, 0.0, 1, CAP)
net = r.price + r.carry - FEE * (CAP * 1) * r.legs
assert abs(r.carry - 2.0) < 1e-6 and abs(r.price + 10.0) < 1e-6 and r.legs == 2, r
assert abs(net - (-20.0)) < 1e-9, net
assert r.positions == 1 and r.hold_s == 3540 - 660 and r.liq == 0
assert abs(r.mae_pct - 0.05) < 1e-9  # high=100 vs fill 99.95 → 5$ от 10k

# ── 2. V1 флип шорт→лонг→шорт: расчётки +1e-4, −1e-4, +1e-4
r = pc.simulate(flat, None, [(600, 0.0001), (1800, -0.0001), (3000, 0.0001)], 0, 3600, 0.0, 1, CAP)
# carry: на 1800 короткая нога получает r=−1e-4 → −1; на 3000 длинная платит +1e-4 → −1
# 6 ног (3 холда), price −30 (каждый круг −10 при flat), fee 36 → net −68
assert r.legs == 6 and r.positions == 3 and abs(r.carry + 2.0) < 1e-9, r
net = r.price + r.carry - FEE * CAP * r.legs
assert abs(net - (-68.0)) < 1e-9, (net, r.price, r.carry)

# ── 3. Ликвидация: lev=10 (n0=100k, q=1000, маржа 10k), spike high=110 на 1980 (сетка 60с!)
spiky = venue([(t, 100.0) for t in range(0, 3600, 60)], hi={1980: 110.0})
r = pc.simulate(spiky, None, f, 0, 3600, 0.0, 10, CAP)
assert r.liq == 1 and abs(r.mae_pct - 10.05) < 1e-9, (r.liq, r.mae_pct)
net = r.price + r.carry - FEE * (CAP * 10) * r.legs
assert abs(r.carry - 20.0) < 1e-9 and abs(net - (-200.0)) < 1e-6, (r.carry, net)

# ── 4. V2 хедж: перп 100, спот 101 (бэквардация −99bp на входе), обе ноги платят slip
# вход 660: перп 99.95, спот 101.0505 (qs=99.0099); выход 3540: перп buy 100.05, спот sell 100.9495
# price = 100*(99.95−100.05) + 99.0099*(100.9495−101.0505) = −10 −10 = −20
r = pc.simulate(flat, spot_flat, [(600, 0.0001), (1800, 0.0001)], 0, 3600, 0.0, 1, CAP)
assert r.legs == 4 and r.n_hedge_opens == 1 and abs(r.carry - 1.0) < 1e-9, r
net = r.price + r.carry - FEE * CAP * r.legs
assert abs(r.price + 20.0) < 1e-6 and abs(net - (-43.0)) < 1e-6, (r.price, net)
assert abs(r.basis_open - (100 / 101 - 1)) < 1e-12 and abs(r.basis_close - (100 / 101 - 1)) < 1e-12

# ── 5. Мёртвая зона: δ=0.00005, r=+0.00002 → флэт всегда, ни одной ноги
r = pc.simulate(flat, None, [(600, 0.00002), (1800, 0.00002)], 0, 3600, 0.00005, 1, CAP)
assert r.legs == 0 and r.positions == 0 and r.carry == 0 and r.price == 0, r

# ── 6. V2 без спот-бара в окне → хедж ОТКАЗАН (голый шорт не открывается)
spot_outside = venue([(t, 101.0) for t in range(100_000, 100_120, 60)])
r = pc.simulate(flat, spot_outside, [(600, 0.0001)], 0, 3600, 0.0, 1, CAP)
assert r.legs == 0 and r.positions == 0 and r.n_hedge_opens == 0, r

# ── 7. Входная расчётка не начисляет carry; interior-расчётка при той же стороне — начисляет
r = pc.simulate(flat, None, [(600, 0.0001), (3480, 0.0001)], 0, 3600, 0.0, 1, CAP)
# 600: решение входа (ent_ts=660 > 600) — carry нет; 3480: interior холда → +1; форс-выход 3540
assert abs(r.carry - 1.0) < 1e-9 and r.legs == 2, (r.carry, r.legs)

print("SMOKE OK: carry-знак, флипы, MAE/ликвидация, две ноги V2-хеджа, δ-зона, "
      "отказ хеджа, interior-расчётки — ручные ожидания бьются")
