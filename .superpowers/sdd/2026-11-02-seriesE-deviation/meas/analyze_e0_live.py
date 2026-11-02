# -*- coding: utf-8 -*-
"""Экран LIVE-клеток E-0-зонда: отделяем evidence от лотереи малых n.

Вход: E0-deviation-probe-raw.txt (рядом, в папке серии) или путь первым аргументом.
Правило evidence-floor ЗАФИКСИРОВАНО ДО разбора по осям: клетка учитывается как
свидетельство, только если в КАЖДОЙ из трёх 60d-третей >=2 круга И всего n>=10.
(Мотивировка — размер выборки: без сделок в трети гейт «net>=0» проходит как 0>=0;
это НЕ отбор по доходности.) ВЕРДИКТ-раздел «экран» воспроизводится этим скриптом.
"""
import io
import re
import sys
import collections
from pathlib import Path
from statistics import median

DEFAULT = Path(__file__).resolve().parents[1] / "E0-deviation-probe-raw.txt"
PAT = re.compile(
    r"(\S+) (\S+) tch(\d) tol(\S+) d(\S+) conf(\d+) k(\d) reg(\S) (\S+)\s+"
    r"n=(\d+) nq=(\d+)/(\d+)/(\d+) med=(\S+)% mean=(\S+)% win=(\d+)% "
    r"pool\$=(\S+) ann=(\S+)% q\$=(\S+)/(\S+)/(\S+) LIVE$")


def parse(raw_path):
    rows = []
    for l in io.open(raw_path, encoding="utf-8-sig").read().splitlines():
        m = PAT.match(l.strip())
        if not m:
            continue
        rows.append(dict(
            sym=m[1], tf=m[2], tch=int(m[3]), tol=float(m[4]), d=float(m[5]),
            conf=int(m[6]), k=int(m[7]), reg=m[8], ex=m[9].strip(),
            n=int(m[10]), nq=(int(m[11]), int(m[12]), int(m[13])),
            pool=float(m[17].replace("+", "")),
            ann=float(m[18].replace("%", "").replace("+", "")),
            q=[float(x.replace("+", "")) for x in (m[19], m[20], m[21])]))
    return rows


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT)
    rows = parse(raw)
    print("разобрано LIVE-строк:", len(rows))
    ns = sorted(r["n"] for r in rows)
    print("n: min", ns[0], "median", median(ns), "max", ns[-1])
    print("LIVE n<=3:", sum(1 for r in rows if r["n"] <= 3), "из", len(rows))
    for mn in (5, 10, 15, 20):
        print(f"LIVE n>={mn}:", sum(1 for r in rows if r["n"] >= mn))

    hard = [r for r in rows if min(r["nq"]) >= 2 and r["n"] >= 10]  # evidence floor
    print("evidence floor (min(nq)>=2 & n>=10):", len(hard))
    print("floor & n>=15:", sum(1 for r in hard if r["n"] >= 15))

    def dist(rs, key):
        return dict(sorted(collections.Counter(r[key] for r in rs).items()))

    for key in ("sym", "tf", "tch", "tol", "d", "conf", "k", "reg", "ex"):
        print("floor by", key, ":", dist(hard, key))

    bases = collections.Counter(
        (r["sym"], r["tf"], r["tch"], r["tol"], r["d"], r["conf"], r["k"], r["reg"])
        for r in hard)
    print("уникальных конфиг-баз (без выхода) во floor:", len(bases),
          "| макс. выходов на базу:", max(bases.values()))
    print("--- базы floor (sym tf tch tol d conf k reg) x число выходов:")
    for b, cnt in sorted(bases.items()):
        print("%s %s tch%d tol%.3f d%.3f conf%d k%d reg%s x%d" % (b + (cnt,)))

    tot = sum(r["pool"] for r in hard)
    t1 = sum(r["q"][0] for r in hard)
    t3 = sum(r["q"][2] for r in hard)
    print("floor суммарно $ по третям: T1=%.0f T2=%.0f T3=%.0f" % (t1, tot - t1 - t3, t3))
    share = [r["q"][2] / max(sum(r["q"]), 1e-9) for r in hard if sum(r["q"]) > 0]
    print("доля $ из третьей трети на клетку: med %.2f, >0.6: %d/%d"
          % (median(share), sum(1 for x in share if x > 0.6), len(share)))
    ann = sorted(r["ann"] for r in hard)
    print("floor ann: min %+.2f med %+.2f max %+.2f" % (ann[0], median(ann), ann[-1]))

    key = lambda r: (r["sym"], r["tf"], r["tch"], r["tol"], r["d"], r["conf"], r["k"], r["ex"])
    pa = {key(r): r["pool"] for r in hard if r["reg"] == "а"}
    pg = {key(r): r["pool"] for r in hard if r["reg"] == "г"}
    same = sum(1 for k in pg if k in pa and abs(pa[k] - pg[k]) < 1)
    print("floor: regг пул == regа пул (пауза не сработала):", same, "из", len(pg))

    print("--- floor по ann (все):")
    for r in sorted(hard, key=lambda x: -x["ann"]):
        print("%s %s tch%d tol%.3f d%.3f conf%d k%d reg%s %s n=%d nq=%s ann=%+.1f%% q=%.0f/%.0f/%.0f"
              % (r["sym"][:3], r["tf"], r["tch"], r["tol"], r["d"], r["conf"], r["k"],
                 r["reg"], r["ex"], r["n"], r["nq"], r["ann"], *r["q"]))


if __name__ == "__main__":
    main()
