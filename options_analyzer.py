#!/usr/bin/env python3
"""
Robust options analyzer for Deribit.
Keeps schema compatible with whale_alerts.html and never publishes BTC/ETH as empty dicts.
"""

import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_DIR, LOG_DIR

OPTIONS_FILE = DATA_DIR / "options_data.json"
TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
BASE = "https://www.deribit.com/api/v2/public"

log_file = LOG_DIR / f"options_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("options")


def _empty_currency(currency: str, error: str = "") -> dict:
    return {
        "status": "error" if error else "empty",
        "error": error,
        "spot_price": 0,
        "total_call_oi": 0,
        "total_put_oi": 0,
        "put_call_ratio": 0,
        "call_pct": 0,
        "put_pct": 0,
        "next_expiry": None,
        "next_max_pain": 0,
        "by_expiry": {},
        "top_calls": [],
        "top_puts": [],
    }


def _is_meaningful_currency(payload: dict) -> bool:
    return isinstance(payload, dict) and (payload.get("spot_price", 0) > 0 or bool(payload.get("by_expiry")))


def _load_previous_valid():
    if not OPTIONS_FILE.exists():
        return None
    try:
        data = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        if _is_meaningful_currency(data.get("BTC", {})) or _is_meaningful_currency(data.get("ETH", {})):
            return data
    except Exception as e:
        log.warning(f"could not read previous options file: {e}")
    return None


def _save_atomic(data: dict):
    tmp = OPTIONS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(OPTIONS_FILE)


def _get(endpoint, params=None, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE}/{endpoint}", params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json().get("result", [])
        except Exception as e:
            last_err = e
            time.sleep(0.6 * (attempt + 1))
    raise last_err


def get_instruments(currency="BTC"):
    instruments = _get("get_instruments", {"currency": currency, "kind": "option", "expired": False})
    log.info(f"  {currency}: {len(instruments)} instrumentos ativos")
    return instruments


def get_tickers_bulk():
    result = {}
    for currency in ["BTC", "ETH"]:
        try:
            summaries = _get("get_book_summary_by_currency", {"currency": currency, "kind": "option"})
            log.info(f"  {currency}: {len(summaries)} book summaries")
            for s in summaries:
                name = s.get("instrument_name", "")
                result[name] = {
                    "open_interest": float(s.get("open_interest", 0) or 0),
                    "volume_24h":    float(s.get("volume", 0) or 0),
                    "bid":           float(s.get("bid_price", 0) or 0),
                    "ask":           float(s.get("ask_price", 0) or 0),
                    "mid":           float(s.get("mid_price", 0) or 0),
                    "iv":            float(s.get("mark_iv", 0) or 0),
                    "mark_price":    float(s.get("mark_price", 0) or 0),
                    "underlying":    float(s.get("underlying_price", 0) or 0),
                }
            time.sleep(0.3)
        except Exception as e:
            log.warning(f"  book_summary {currency}: {e}")
    return result


def parse_instrument_name(name):
    try:
        parts = name.split("-")
        return parts[0], parts[1], int(parts[2]), parts[3]
    except Exception:
        return None, None, None, None


def compute_max_pain(calls, puts, strikes):
    if not strikes:
        return 0
    pain = {}
    for s in strikes:
        call_loss = sum(oi * max(0, s - k) for k, oi in calls.items())
        put_loss  = sum(oi * max(0, k - s) for k, oi in puts.items())
        pain[s]   = call_loss + put_loss
    return min(pain, key=pain.get)


def compute_gex_by_strike(strikes_data, spot_price):
    gex = {}
    for strike, d in strikes_data.items():
        dist_pct = abs(strike - spot_price) / spot_price if spot_price else 0
        gamma_proxy = 0.0001 / (dist_pct + 0.005)
        call_oi = d.get("call_oi", 0)
        put_oi  = d.get("put_oi", 0)
        gex[strike] = round((call_oi - put_oi) * gamma_proxy * (spot_price ** 2) * 0.01, 0)
    return gex


def compute_skew(strikes_data, spot_price):
    otm_call_ivs, otm_put_ivs = [], []
    for strike, d in strikes_data.items():
        dist = (strike - spot_price) / spot_price if spot_price else 0
        if 0.05 <= dist <= 0.35 and d.get("call_iv", 0) > 0:
            otm_call_ivs.append(d["call_iv"])
        if -0.35 <= dist <= -0.05 and d.get("put_iv", 0) > 0:
            otm_put_ivs.append(d["put_iv"])
    if otm_call_ivs and otm_put_ivs:
        return round(sum(otm_put_ivs)/len(otm_put_ivs) - sum(otm_call_ivs)/len(otm_call_ivs), 2)
    return 0


def analyze_currency(currency, instruments, tickers, spot_price):
    by_expiry = defaultdict(lambda: {"calls":{}, "puts":{}, "strikes_data":{}})
    for inst in instruments:
        name = inst.get("instrument_name","")
        cur, expiry, strike, opt_type = parse_instrument_name(name)
        if cur != currency or not strike:
            continue
        ticker = tickers.get(name, {})
        oi = ticker.get("open_interest", 0)
        iv = ticker.get("iv", 0)
        vol24 = ticker.get("volume_24h", 0)
        sd = by_expiry[expiry]["strikes_data"].setdefault(strike, {})
        if opt_type == "C":
            by_expiry[expiry]["calls"][strike] = by_expiry[expiry]["calls"].get(strike, 0) + oi
            sd["call_oi"] = sd.get("call_oi", 0) + oi
            sd["call_iv"] = iv
            sd["call_vol"] = sd.get("call_vol", 0) + vol24
        else:
            by_expiry[expiry]["puts"][strike] = by_expiry[expiry]["puts"].get(strike, 0) + oi
            sd["put_oi"] = sd.get("put_oi", 0) + oi
            sd["put_iv"] = iv
            sd["put_vol"] = sd.get("put_vol", 0) + vol24

    results = {}
    all_call_oi = all_put_oi = 0
    root_call_strikes = defaultdict(float)
    root_put_strikes = defaultdict(float)

    for expiry, data in sorted(by_expiry.items()):
        calls, puts, sd = data["calls"], data["puts"], data["strikes_data"]
        strikes = sorted(set(list(calls.keys()) + list(puts.keys())))
        if not strikes:
            continue
        total_call = sum(calls.values())
        total_put = sum(puts.values())
        total_oi = total_call + total_put
        all_call_oi += total_call
        all_put_oi += total_put
        for k,v in calls.items(): root_call_strikes[k] += v
        for k,v in puts.items(): root_put_strikes[k] += v
        max_pain = compute_max_pain(calls, puts, strikes)
        gex_by_strike = compute_gex_by_strike(sd, spot_price)
        total_gex = sum(gex_by_strike.values())
        skew = compute_skew(sd, spot_price)
        strike_oi = {s: calls.get(s,0)+puts.get(s,0) for s in strikes}
        top_strikes = sorted(strike_oi.items(), key=lambda x: x[1], reverse=True)[:8]
        nearby = sorted(strikes, key=lambda s: abs(s - spot_price))[:6]
        nearby_data = []
        for s in sorted(nearby):
            nearby_data.append({
                "strike":   s,
                "call_oi":  round(calls.get(s,0), 1),
                "put_oi":   round(puts.get(s,0), 1),
                "call_iv":  round(sd.get(s,{}).get("call_iv",0), 1),
                "put_iv":   round(sd.get(s,{}).get("put_iv",0), 1),
                "gex":      round(gex_by_strike.get(s,0), 0),
                "dist_pct": round((s - spot_price)/spot_price*100, 2) if spot_price else 0,
            })
        top_gex_pos = sorted([(k,v) for k,v in gex_by_strike.items() if v>0], key=lambda x: x[1], reverse=True)[:4]
        top_gex_neg = sorted([(k,v) for k,v in gex_by_strike.items() if v<0], key=lambda x: x[1])[:4]
        results[expiry] = {
            "expiry": expiry,
            "total_call_oi": round(total_call, 1),
            "total_put_oi": round(total_put, 1),
            "total_oi": round(total_oi, 1),
            "put_call_ratio": round(total_put/total_call, 3) if total_call else 0,
            "call_pct": round(total_call/total_oi*100, 1) if total_oi else 0,
            "put_pct": round(total_put/total_oi*100, 1) if total_oi else 0,
            "max_pain": max_pain,
            "max_pain_dist": round((max_pain-spot_price)/spot_price*100, 2) if max_pain and spot_price else 0,
            "total_gex": round(total_gex, 0),
            "gex_regime": "POSITIVO" if total_gex > 0 else "NEGATIVO",
            "vol_skew": skew,
            "skew_signal": "MEDO" if skew > 3 else "NEUTRO" if skew > -3 else "COMPLACENCIA",
            "top_strikes": [{"strike":k,"oi":round(v,1)} for k,v in top_strikes],
            "nearby_strikes": nearby_data,
            "gex_support": [{"strike":k,"gex":round(v,0)} for k,v in top_gex_pos],
            "gex_resistance": [{"strike":k,"gex":round(v,0)} for k,v in top_gex_neg],
            "strikes_count": len(strikes),
        }

    expiries_sorted = sorted(results.keys())
    next_expiry = expiries_sorted[0] if expiries_sorted else None
    top_calls = sorted(root_call_strikes.items(), key=lambda x: x[1], reverse=True)[:5]
    top_puts = sorted(root_put_strikes.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "status": "ok" if results else "empty",
        "spot_price": round(spot_price, 2) if spot_price else 0,
        "total_call_oi": round(all_call_oi, 1),
        "total_put_oi": round(all_put_oi, 1),
        "put_call_ratio": round(all_put_oi/all_call_oi, 3) if all_call_oi else 0,
        "call_pct": round(all_call_oi/(all_call_oi+all_put_oi)*100,1) if (all_call_oi+all_put_oi) else 0,
        "put_pct": round(all_put_oi/(all_call_oi+all_put_oi)*100,1) if (all_call_oi+all_put_oi) else 0,
        "next_expiry": next_expiry,
        "next_max_pain": results[next_expiry]["max_pain"] if next_expiry else 0,
        "by_expiry": results,
        "top_calls": [{"strike":k,"oi":round(v,1)} for k,v in top_calls],
        "top_puts": [{"strike":k,"oi":round(v,1)} for k,v in top_puts],
    }


def get_spot_price(currency):
    try:
        idx = _get("get_index_price", {"index_name": f"{currency.lower()}_usd"})
        px = float(idx.get("index_price", 0))
        if px > 0:
            return px
    except Exception as e:
        log.warning(f"  {currency}: deribit spot fail: {e}")
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": f"{currency}USDT"}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        log.warning(f"  {currency}: binance spot fail: {e}")
    return 0


def run_options_analysis():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"📊 Options analysis — {ts}")
    previous = _load_previous_valid() or {}
    result = {"timestamp": ts, "BTC": _empty_currency("BTC"), "ETH": _empty_currency("ETH")}
    log.info("  Baixando book summaries…")
    try:
        tickers = get_tickers_bulk()
    except Exception as e:
        log.warning(f"  tickers bulk falhou: {e}")
        tickers = {}

    for currency in ["BTC", "ETH"]:
        try:
            log.info(f"  Analisando {currency}…")
            spot = get_spot_price(currency)
            if spot == 0:
                raise RuntimeError("spot price zero")
            instruments = get_instruments(currency)
            if not instruments:
                raise RuntimeError("sem instrumentos ativos")
            analysis = analyze_currency(currency, instruments, tickers, spot)
            if not analysis.get("by_expiry"):
                raise RuntimeError("análise sem expiries úteis")
            result[currency] = analysis
            log.info(f"  {currency}: spot ${spot:,.0f} | next expiry {analysis.get('next_expiry','')} | max pain ${analysis.get('next_max_pain',0):,} | P/C {analysis.get('put_call_ratio',0):.2f}")
            time.sleep(0.8)
        except Exception as e:
            log.exception(f"  {currency} análise falhou: {e}")
            prev_cur = previous.get(currency, {}) if isinstance(previous, dict) else {}
            if _is_meaningful_currency(prev_cur):
                prev_cur = dict(prev_cur)
                prev_cur["status"] = "stale"
                prev_cur["error"] = str(e)
                result[currency] = prev_cur
            else:
                result[currency] = _empty_currency(currency, str(e))

    _save_atomic(result)
    log.info(f"  ✅ Salvo em {OPTIONS_FILE}")
    return result


if __name__ == "__main__":
    run_options_analysis()
