#!/usr/bin/env python3
"""Generate robust market context JSON for whale_alerts.html."""

import json
import logging
from datetime import datetime
from pathlib import Path
import requests

import sys
sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_DIR, LOG_DIR, INFO_URL

OUT_FILE = DATA_DIR / "market_context.json"
HIST_FILE = DATA_DIR / "market_context_history.json"
OPTIONS_FILE = DATA_DIR / "options_data.json"
ALERTS_FILE = DATA_DIR / "alerts.json"

log_file = LOG_DIR / f"market_context_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler()])
log = logging.getLogger("market_context")


def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        log.warning(f"load {path.name}: {e}")
    return default


def _save_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _post(payload):
    r = requests.post(INFO_URL, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def get_hl_ctx():
    try:
        data = _post({"type": "metaAndAssetCtxs"})
        meta, ctxs = data[0], data[1]
        out = {}
        for u, c in zip(meta.get("universe", []), ctxs):
            name = u.get("name")
            out[name] = {
                "mark_px": float(c.get("markPx", 0) or 0),
                "funding": float(c.get("funding", 0) or 0),
                "open_interest": float(c.get("openInterest", 0) or 0),
                "prev_day_px": float(c.get("prevDayPx", 0) or 0),
            }
        return out
    except Exception as e:
        log.warning(f"metaAndAssetCtxs fail: {e}")
        return {}


def build_context():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    alerts = _load_json(ALERTS_FILE, [])
    latest = alerts[0] if isinstance(alerts, list) and alerts else {}
    opt = _load_json(OPTIONS_FILE, {})
    hl = get_hl_ctx()
    btc_hl = hl.get("BTC", {})
    eth_hl = hl.get("ETH", {})

    long_pct = float(latest.get("long_pct", 50))
    short_pct = float(latest.get("short_pct", 50))
    funding = float(btc_hl.get("funding", 0))
    day_ret = 0
    if btc_hl.get("mark_px") and btc_hl.get("prev_day_px"):
        prev = btc_hl.get("prev_day_px") or 0
        if prev:
            day_ret = (btc_hl.get("mark_px", 0) - prev) / prev * 100

    whale_score = round((long_pct - 50) * 1.2, 1)
    funding_score = round(-funding * 10000, 1)
    price_action_score = round(day_ret * 2, 1)
    options_pc = float(opt.get("BTC", {}).get("put_call_ratio", 0) or 0)
    options_score = 8 if options_pc > 1.4 else 4 if options_pc > 1.15 else -4 if 0 < options_pc < 0.85 else 0
    liq_long = 0
    liq_short = 0
    liq_score = 0
    total_score = round(whale_score + funding_score + price_action_score + options_score + liq_score, 1)
    if total_score >= 18:
        bias = "BULLISH_FORTE"
    elif total_score >= 6:
        bias = "BULLISH"
    elif total_score <= -18:
        bias = "BEARISH_FORTE"
    elif total_score <= -6:
        bias = "BEARISH"
    else:
        bias = "NEUTRO"

    top_calls = opt.get("BTC", {}).get("top_calls", [])[:2]
    top_puts = opt.get("BTC", {}).get("top_puts", [])[:2]

    data = {
        "timestamp": ts,
        "market_score": {
            "total_score": total_score,
            "bias": bias,
            "components": {
                "funding": {"score": funding_score, "value": funding * 100},
                "long_short": {"score": whale_score, "value": long_pct / 100},
                "liquidations": {"score": liq_score, "long_usd": liq_long, "short_usd": liq_short},
                "spot_flow": {"score": price_action_score, "basis": day_ret},
                "options": {"score": options_score, "put_call_ratio": options_pc},
            },
        },
        "binance": {
            "BTC": {"funding_rate": funding * 100, "oi_usd": float(btc_hl.get("open_interest", 0)) * float(btc_hl.get("mark_px", 0)), "long_ratio": long_pct/100},
            "ETH": {"funding_rate": float(eth_hl.get("funding", 0))*100, "oi_usd": float(eth_hl.get("open_interest", 0))*float(eth_hl.get("mark_px", 0)), "long_ratio": 0.5},
        },
        "spot_flows": {
            "BTC": {"basis": round(day_ret, 3)},
            "ETH": {"basis": 0.0},
        },
        "liquidations": {
            "BTC": {"long_liq_usd": liq_long, "short_liq_usd": liq_short, "liq_bias": "LONG" if liq_long > liq_short else "SHORT" if liq_short > liq_long else "NEUTRO"},
            "ETH": {"long_liq_usd": 0, "short_liq_usd": 0, "liq_bias": "NEUTRO"},
        },
        "options": {
            "max_pain": opt.get("BTC", {}).get("next_max_pain", 0),
            "put_call_ratio": options_pc,
            "call_pct": opt.get("BTC", {}).get("call_pct", 50),
            "put_pct": opt.get("BTC", {}).get("put_pct", 50),
            "top_calls": top_calls,
            "top_puts": top_puts,
        },
        "hl_summary": {
            "btc_funding": funding * 100,
            "btc_oi_usd": float(btc_hl.get("open_interest", 0)) * float(btc_hl.get("mark_px", 0)),
        },
    }
    return data


def run_market_context():
    data = build_context()
    _save_json(OUT_FILE, data)
    hist = _load_json(HIST_FILE, [])
    hist.insert(0, {"timestamp": data["timestamp"], "score": data["market_score"]["total_score"], "bias": data["market_score"]["bias"]})
    hist = hist[:240]
    _save_json(HIST_FILE, hist)
    log.info(f"saved market context score={data['market_score']['total_score']} bias={data['market_score']['bias']}")


if __name__ == '__main__':
    run_market_context()
