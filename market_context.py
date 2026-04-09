#!/usr/bin/env python3
import json, logging
from datetime import datetime
from pathlib import Path
import requests

from config import DATA_DIR, LOG_DIR

INFO_URL = "https://api.hyperliquid.xyz/info"
ALERTS_FILE = DATA_DIR / "alerts.json"
OUT_FILE = DATA_DIR / "market_context.json"
HIST_FILE = DATA_DIR / "market_context_history.json"
TIMEOUT = 20
MAX_HIST = 200

log_file = LOG_DIR / f"market_context_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("market_context")


def placeholder_context(reason="indisponível"):
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "status": "placeholder",
        "reason": reason,
        "market_score": {
            "total_score": 0,
            "bias": "NEUTRO",
            "components": {
                "funding": {"score": 0, "value": 0.0},
                "long_short": {"score": 0, "value": 1.0},
                "liquidations": {"score": 0, "long_usd": 0, "short_usd": 0},
                "spot_flow": {"score": 0, "basis": 0.0},
                "options": {"score": 0, "put_call_ratio": 1.0}
            }
        },
        "binance": {"BTC": {"funding_rate": 0.0}},
        "spot_flows": {"BTC": {"basis": 0.0}},
        "liquidations": {"BTC": {"long_liq_usd": 0, "short_liq_usd": 0}},
        "options": {"call_pct": 50, "put_pct": 50},
        "hl_summary": {"directional_whales": 0, "excluded_mm": 0, "excluded_delta_neutral": 0}
    }


def _post(payload):
    r = requests.post(INFO_URL, json=payload, timeout=TIMEOUT, headers={"Content-Type":"application/json"})
    r.raise_for_status()
    return r.json()


def _load_alerts():
    if ALERTS_FILE.exists():
        try:
            return json.loads(ALERTS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def _load_hist():
    if HIST_FILE.exists():
        try:
            return json.loads(HIST_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def _sign(v, neutral=0.0):
    if v > neutral:
        return 1
    if v < neutral:
        return -1
    return 0


def build_context():
    asset_ctx = _post({"type":"metaAndAssetCtxs"})
    meta, ctxs = asset_ctx[0], asset_ctx[1]
    uni = meta.get("universe", [])
    ctx_map = {u.get("name"): c for u, c in zip(uni, ctxs)}
    btc = ctx_map.get("BTC", {})

    alerts = _load_alerts()
    latest = alerts[0] if alerts else {}
    long_pct = float(latest.get("long_pct", 50.0) or 50.0)
    short_pct = float(latest.get("short_pct", 50.0) or 50.0)
    whale_bias = _sign(long_pct - short_pct, 0)
    whale_score = round((long_pct - 50.0) / 5.0, 2)

    funding = float(btc.get("funding", 0) or 0)
    mark = float(btc.get("markPx", 0) or 0)
    oi = float(btc.get("openInterest", 0) or 0)
    prev_day = float(btc.get("prevDayPx", mark) or mark or 1)
    day_ret = ((mark - prev_day) / prev_day * 100.0) if prev_day else 0.0

    funding_score = round((-funding * 10000), 2)
    price_score = round(day_ret / 2.0, 2)

    scores = {
        "whales": whale_score,
        "funding": funding_score,
        "price_action": price_score,
        "basis": 0.0,
        "liq_pressure": 0.0,
    }
    total_score = round(sum(scores.values()), 2)
    bias = "BULLISH" if total_score >= 2 else "BEARISH" if total_score <= -2 else "NEUTRO"

    payload = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "asset": "BTC",
        "bias": bias,
        "score": total_score,
        "scores": scores,
        "metrics": {
            "mark_price": mark,
            "open_interest": oi,
            "funding_rate": funding,
            "day_return_pct": round(day_ret, 3),
            "whales_long_pct": round(long_pct, 2),
            "whales_short_pct": round(short_pct, 2),
            "active_whales": int(latest.get("active_whales", 0) or 0),
        },
        "drivers": [
            {"label": "Whale consensus", "value": round(long_pct - short_pct, 2), "direction": whale_bias},
            {"label": "Funding", "value": round(funding * 100, 4), "direction": _sign(-funding)},
            {"label": "BTC day return", "value": round(day_ret, 3), "direction": _sign(day_ret)},
        ],
    }
    return payload


def main():
    ctx = build_context()
    OUT_FILE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    hist = _load_hist()
    hist.insert(0, ctx)
    HIST_FILE.write_text(json.dumps(hist[:MAX_HIST], ensure_ascii=False, indent=2), encoding='utf-8')
    log.info("market_context.json atualizado")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ctx = placeholder_context(str(e))
        OUT_FILE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
        HIST_FILE.write_text(json.dumps([{"ts": ctx["generated_at"], "score": 0}], ensure_ascii=False, indent=2), encoding="utf-8")
        log.exception("fallback market context gerado")
