# -*- coding: utf-8 -*-
"""P1 снапшот: top-3 USDT-перпов Bybit linear по 24h quote-volume после BTC/ETH.

Замороженное правило аудита (7ad653e §2/§P1): «top-3 quote-volume на Bybit linear
после BTC/ETH на момент снапшота (провенанс-файл)». Здесь только этот отбор и
провенанс; никакого чтения баров/холдаутов.

Запуск (из корня репо, сеть обязательна):
  python -X utf8 .superpowers/sdd/2026-11-03-seriesV-altcoins/meas/p1_snapshot_top3.py
Пишет ../data/p1-snapshot-provenance-<UTCdate>.json (endpoint, время, сырой top-25,
выборка, правило).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import httpx

ENDPOINT = "https://api.bybit.com/v5/market/tickers"
CATEGORY = "linear"
EXCLUDE = {"BTCUSDT", "ETHUSDT"}
SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")  # только вечные USDT-перпы: без дэтированных и inverse
SER_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    resp = httpx.get(ENDPOINT, params={"category": CATEGORY}, timeout=15.0)
    resp.raise_for_status()
    payload = resp.json()
    if payload["retCode"] != 0:
        raise SystemExit(f"Bybit retCode={payload['retCode']}: {payload.get('retMsg')}")
    rows = payload["result"]["list"]
    perps = [r for r in rows if SYMBOL_RE.match(r["symbol"]) and r["symbol"] not in EXCLUDE]
    perps.sort(key=lambda r: (float(r["turnover24h"]), r["symbol"]), reverse=True)
    top25 = [
        {
            "symbol": r["symbol"],
            "turnover24h": r["turnover24h"],
            "volume24h": r["volume24h"],
            "lastPrice": r["lastPrice"],
            "fundingRate": r.get("fundingRate"),
        }
        for r in perps[:25]
    ]
    chosen = [r["symbol"] for r in perps[:3]]
    now = dt.datetime.now(dt.UTC)
    doc = {
        "rule": "top-3 Bybit linear perpetuals by turnover24h after BTC/ETH at snapshot "
                "(GLOBAL-STRATEGY-AUDIT 7ad653e, Series V test-plan / P1)",
        "endpoint": f"{ENDPOINT}?category={CATEGORY}",
        "snapshot_utc": now.isoformat(timespec="seconds"),
        "snapshot_unix": int(now.timestamp()),
        "row_count_total": len(rows),
        "row_count_filtered": len(perps),
        "excluded": sorted(EXCLUDE),
        "top25_raw": top25,
        "chosen": chosen,
    }
    out = SER_DIR / "data" / f"p1-snapshot-provenance-{now:%Y%m%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"chosen: {chosen}")
    print(f"provenance: {out}")
    for r in top25[:8]:
        print(f"  {r['symbol']:<14} turnover24h={float(r['turnover24h']):,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
