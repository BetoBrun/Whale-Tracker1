#!/usr/bin/env python3
"""
options_analyzer.py — Análise de opções BTC e ETH via Deribit API pública.

Calcula e salva: data/options_data.json
  - Max Pain por vencimento
  - Gamma Exposure (GEX) por strike
  - Put/Call Ratio e skew de volatilidade implícita
  - Open Interest concentrado por strike (suporte/resistência de opções)
  - Níveis de hedging dos market makers
"""

import json, time, logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_DIR, LOG_DIR

OPTIONS_FILE = DATA_DIR / "options_data.json"
TIMEOUT      = 20
HEADERS      = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

log_file = LOG_DIR / f"options_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger("options")

BASE = "https://www.deribit.com/api/v2/public"


def _get(endpoint, params=None):
    r = requests.get(f"{BASE}/{endpoint}", params=params,
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("result", [])


# ══════════════════════════════════════════════════════════════
# STEP 1 — busca todos os instrumentos de opção ativos
# ══════════════════════════════════════════════════════════════

def get_instruments(currency="BTC"):
    instruments = _get("get_instruments", {
        "currency": currency, "kind": "option", "expired": False
    })
    log.info(f"  {currency}: {len(instruments)} instrumentos ativos")
    return instruments


# ══════════════════════════════════════════════════════════════
# STEP 2 — busca ticker de cada instrumento (IV, OI, delta, gamma)
# ══════════════════════════════════════════════════════════════

def get_tickers_bulk(instruments):
    """
    Busca tickers em lote via book_summary_by_currency.
    Mais eficiente que chamar ticker individual por instrumento.
    """
    result = {}
    # book_summary retorna dados de todos os instrumentos de uma currency
    for currency in ["BTC", "ETH"]:
        try:
            summaries = _get("get_book_summary_by_currency", {
                "currency": currency, "kind": "option"
            })
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


# ══════════════════════════════════════════════════════════════
# STEP 3 — parse e cálculo por vencimento
# ══════════════════════════════════════════════════════════════

def parse_instrument_name(name):
    """BTC-28MAR26-70000-C → (currency, expiry_str, strike, type)"""
    try:
        parts = name.split("-")
        currency = parts[0]
        expiry   = parts[1]
        strike   = int(parts[2])
        opt_type = parts[3]   # C ou P
        return currency, expiry, strike, opt_type
    except:
        return None, None, None, None


def compute_max_pain(calls, puts, strikes):
    """
    Max pain: strike onde a soma de (call OI × max(0, strike-S)) +
    (put OI × max(0, S-strike)) é mínima para S = cada strike.
    """
    if not strikes:
        return 0
    pain = {}
    for s in strikes:
        call_loss = sum(oi * max(0, s - k) for k, oi in calls.items())
        put_loss  = sum(oi * max(0, k - s) for k, oi in puts.items())
        pain[s]   = call_loss + put_loss
    return min(pain, key=pain.get)


def compute_gex_by_strike(strikes_data, spot_price):
    """
    GEX aproximado por strike:
    GEX = gamma × OI × spot² × 0.01
    gamma ≈ 0.0001 × (1 / (|strike - spot| / spot + 0.01))  [proxy sem BS]
    
    GEX positivo = market makers compram quedas → suporte
    GEX negativo = market makers vendem altas → resistência
    
    Retorna dict {strike: gex_value} ordenado.
    """
    gex = {}
    for strike, d in strikes_data.items():
        dist_pct = abs(strike - spot_price) / spot_price
        gamma_proxy = 0.0001 / (dist_pct + 0.005)  # proxy simples
        call_oi = d.get("call_oi", 0)
        put_oi  = d.get("put_oi", 0)
        # MMs são short calls e long puts → GEX = (call_oi - put_oi) × gamma × spot²
        gex_val = (call_oi - put_oi) * gamma_proxy * (spot_price ** 2) * 0.01
        gex[strike] = round(gex_val, 0)
    return gex


def compute_skew(strikes_data, spot_price, target_delta_pct=0.25):
    """
    Skew de vol: diferença entre IV de puts OTM e calls OTM no mesmo delta.
    Usa strikes a ±25% do spot como proxy de 25-delta.
    Skew negativo = puts mais caros (medo de queda).
    """
    otm_call_ivs = []
    otm_put_ivs  = []
    for strike, d in strikes_data.items():
        dist = (strike - spot_price) / spot_price
        if 0.05 <= dist <= 0.35 and d.get("call_iv", 0) > 0:
            otm_call_ivs.append(d["call_iv"])
        if -0.35 <= dist <= -0.05 and d.get("put_iv", 0) > 0:
            otm_put_ivs.append(d["put_iv"])
    if otm_call_ivs and otm_put_ivs:
        return round(sum(otm_put_ivs)/len(otm_put_ivs) - sum(otm_call_ivs)/len(otm_call_ivs), 2)
    return 0


# ══════════════════════════════════════════════════════════════
# STEP 4 — processa tudo por currency e vencimento
# ══════════════════════════════════════════════════════════════

def analyze_currency(currency, instruments, tickers, spot_price):
    # agrupa por vencimento
    by_expiry = defaultdict(lambda: {"calls":{}, "puts":{}, "strikes_data":{}})

    for inst in instruments:
        name = inst.get("instrument_name","")
        cur, expiry, strike, opt_type = parse_instrument_name(name)
        if cur != currency or not strike: continue

        ticker = tickers.get(name, {})
        oi     = ticker.get("open_interest", 0)
        iv     = ticker.get("iv", 0)
        vol24  = ticker.get("volume_24h", 0)

        if opt_type == "C":
            by_expiry[expiry]["calls"][strike] = by_expiry[expiry]["calls"].get(strike,0) + oi
            if strike not in by_expiry[expiry]["strikes_data"]:
                by_expiry[expiry]["strikes_data"][strike] = {}
            by_expiry[expiry]["strikes_data"][strike]["call_oi"] = \
                by_expiry[expiry]["strikes_data"][strike].get("call_oi",0) + oi
            by_expiry[expiry]["strikes_data"][strike]["call_iv"]  = iv
            by_expiry[expiry]["strikes_data"][strike]["call_vol"] = \
                by_expiry[expiry]["strikes_data"][strike].get("call_vol",0) + vol24
        else:
            by_expiry[expiry]["puts"][strike] = by_expiry[expiry]["puts"].get(strike,0) + oi
            if strike not in by_expiry[expiry]["strikes_data"]:
                by_expiry[expiry]["strikes_data"][strike] = {}
            by_expiry[expiry]["strikes_data"][strike]["put_oi"] = \
                by_expiry[expiry]["strikes_data"][strike].get("put_oi",0) + oi
            by_expiry[expiry]["strikes_data"][strike]["put_iv"]  = iv
            by_expiry[expiry]["strikes_data"][strike]["put_vol"] = \
                by_expiry[expiry]["strikes_data"][strike].get("put_vol",0) + vol24

    results = {}
    all_call_oi = all_put_oi = 0

    for expiry, data in sorted(by_expiry.items()):
        calls  = data["calls"]
        puts   = data["puts"]
        sd     = data["strikes_data"]
        strikes = sorted(set(list(calls.keys()) + list(puts.keys())))

        if not strikes: continue

        total_call = sum(calls.values())
        total_put  = sum(puts.values())
        all_call_oi += total_call
        all_put_oi  += total_put
        total_oi    = total_call + total_put

        # max pain
        max_pain = compute_max_pain(calls, puts, strikes)

        # GEX
        gex_by_strike = compute_gex_by_strike(sd, spot_price)
        total_gex = sum(gex_by_strike.values())

        # skew
        skew = compute_skew(sd, spot_price)

        # top strikes por OI total
        strike_oi = {s: calls.get(s,0)+puts.get(s,0) for s in strikes}
        top_strikes = sorted(strike_oi.items(), key=lambda x: x[1], reverse=True)[:8]

        # strikes mais próximos do spot
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
                "dist_pct": round((s - spot_price)/spot_price*100, 2),
            })

        # GEX levels — top positivos e negativos
        top_gex_pos = sorted([(k,v) for k,v in gex_by_strike.items() if v>0],
                              key=lambda x: x[1], reverse=True)[:4]
        top_gex_neg = sorted([(k,v) for k,v in gex_by_strike.items() if v<0],
                              key=lambda x: x[1])[:4]

        results[expiry] = {
            "expiry":         expiry,
            "total_call_oi":  round(total_call, 1),
            "total_put_oi":   round(total_put, 1),
            "total_oi":       round(total_oi, 1),
            "put_call_ratio": round(total_put/total_call, 3) if total_call else 0,
            "call_pct":       round(total_call/total_oi*100, 1) if total_oi else 0,
            "put_pct":        round(total_put/total_oi*100, 1) if total_oi else 0,
            "max_pain":       max_pain,
            "max_pain_dist":  round((max_pain-spot_price)/spot_price*100, 2) if max_pain else 0,
            "total_gex":      round(total_gex, 0),
            "gex_regime":     "POSITIVO" if total_gex > 0 else "NEGATIVO",
            "vol_skew":       skew,
            "skew_signal":    "MEDO" if skew > 3 else "NEUTRO" if skew > -3 else "COMPLACENCIA",
            "top_strikes":    [{"strike":k,"oi":round(v,1)} for k,v in top_strikes],
            "nearby_strikes": nearby_data,
            "gex_support":    [{"strike":k,"gex":round(v,0)} for k,v in top_gex_pos],
            "gex_resistance": [{"strike":k,"gex":round(v,0)} for k,v in top_gex_neg],
            "strikes_count":  len(strikes),
        }

    # resumo geral da currency
    pc_total = round(all_put_oi/all_call_oi, 3) if all_call_oi else 0

    # próximo vencimento (mais relevante)
    expiries_sorted = sorted(results.keys())
    next_expiry = expiries_sorted[0] if expiries_sorted else None

    return {
        "spot_price":      spot_price,
        "total_call_oi":   round(all_call_oi, 1),
        "total_put_oi":    round(all_put_oi, 1),
        "put_call_ratio":  pc_total,
        "call_pct":        round(all_call_oi/(all_call_oi+all_put_oi)*100,1) if (all_call_oi+all_put_oi) else 0,
        "put_pct":         round(all_put_oi/(all_call_oi+all_put_oi)*100,1) if (all_call_oi+all_put_oi) else 0,
        "next_expiry":     next_expiry,
        "next_max_pain":   results[next_expiry]["max_pain"] if next_expiry else 0,
        "by_expiry":       results,
    }


# ══════════════════════════════════════════════════════════════
# STEP 5 — busca spot price via Deribit
# ══════════════════════════════════════════════════════════════

def get_spot_price(currency):
    try:
        idx = _get("get_index_price", {"index_name": f"{currency.lower()}_usd"})
        return float(idx.get("index_price", 0))
    except:
        # fallback Binance
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": f"{currency}USDT"}, timeout=10)
            return float(r.json()["price"])
        except:
            return 0


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════


def build_fallback_options_data(reason="fallback generated"):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    fallback = {
        "timestamp": ts,
        "status": "fallback",
        "message": reason,
        "source": "static_fallback",
        "BTC": {
            "spot_price": 72000,
            "total_call_oi": 12000.0,
            "total_put_oi": 15000.0,
            "put_call_ratio": 1.25,
            "call_pct": 44.4,
            "put_pct": 55.6,
            "next_expiry": "NEXT",
            "next_max_pain": 70000,
            "by_expiry": {
                "NEXT": {
                    "expiry": "NEXT",
                    "total_call_oi": 4200.0,
                    "total_put_oi": 5600.0,
                    "total_oi": 9800.0,
                    "put_call_ratio": 1.33,
                    "call_pct": 42.9,
                    "put_pct": 57.1,
                    "max_pain": 70000,
                    "max_pain_dist": -2.8,
                    "total_gex": 12500000,
                    "gex_regime": "POSITIVO",
                    "vol_skew": 4.2,
                    "skew_signal": "MEDO",
                    "top_strikes": [{"strike":68000,"oi":1700.0},{"strike":70000,"oi":2400.0},{"strike":72000,"oi":2100.0},{"strike":75000,"oi":1600.0}],
                    "nearby_strikes": [
                        {"strike":68000,"call_oi":800.0,"put_oi":900.0,"call_iv":58.0,"put_iv":63.0,"gex":2200000.0,"dist_pct":-5.6},
                        {"strike":70000,"call_oi":1100.0,"put_oi":1300.0,"call_iv":57.0,"put_iv":62.0,"gex":4100000.0,"dist_pct":-2.8},
                        {"strike":72000,"call_oi":1200.0,"put_oi":900.0,"call_iv":56.0,"put_iv":59.0,"gex":3600000.0,"dist_pct":0.0},
                        {"strike":75000,"call_oi":900.0,"put_oi":700.0,"call_iv":55.0,"put_iv":58.0,"gex":1800000.0,"dist_pct":4.2}
                    ],
                    "gex_support": [{"strike":70000,"gex":4100000.0},{"strike":72000,"gex":3600000.0}],
                    "gex_resistance": [{"strike":75000,"gex":-1900000.0},{"strike":78000,"gex":-1200000.0}],
                    "strikes_count": 18
                }
            }
        },
        "ETH": {
            "spot_price": 3600,
            "total_call_oi": 18000.0,
            "total_put_oi": 14000.0,
            "put_call_ratio": 0.78,
            "call_pct": 56.2,
            "put_pct": 43.8,
            "next_expiry": "NEXT",
            "next_max_pain": 3500,
            "by_expiry": {
                "NEXT": {
                    "expiry": "NEXT",
                    "total_call_oi": 6200.0,
                    "total_put_oi": 4800.0,
                    "total_oi": 11000.0,
                    "put_call_ratio": 0.77,
                    "call_pct": 56.4,
                    "put_pct": 43.6,
                    "max_pain": 3500,
                    "max_pain_dist": -2.8,
                    "total_gex": -6200000,
                    "gex_regime": "NEGATIVO",
                    "vol_skew": 1.1,
                    "skew_signal": "NEUTRO",
                    "top_strikes": [{"strike":3400,"oi":2100.0},{"strike":3500,"oi":2600.0},{"strike":3600,"oi":2300.0},{"strike":3800,"oi":1800.0}],
                    "nearby_strikes": [
                        {"strike":3400,"call_oi":900.0,"put_oi":1200.0,"call_iv":64.0,"put_iv":66.0,"gex":1200000.0,"dist_pct":-5.6},
                        {"strike":3500,"call_oi":1200.0,"put_oi":1400.0,"call_iv":63.0,"put_iv":65.0,"gex":1700000.0,"dist_pct":-2.8},
                        {"strike":3600,"call_oi":1400.0,"put_oi":900.0,"call_iv":62.0,"put_iv":63.0,"gex":-2100000.0,"dist_pct":0.0},
                        {"strike":3800,"call_oi":1300.0,"put_oi":500.0,"call_iv":61.0,"put_iv":61.0,"gex":-2400000.0,"dist_pct":5.6}
                    ],
                    "gex_support": [{"strike":3500,"gex":1700000.0},{"strike":3400,"gex":1200000.0}],
                    "gex_resistance": [{"strike":3800,"gex":-2400000.0},{"strike":3600,"gex":-2100000.0}],
                    "strikes_count": 20
                }
            }
        }
    }
    return fallback


def write_options_payload(payload):
    OPTIONS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"  ✅ Salvo em {OPTIONS_FILE}")


def run_options_analysis():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"📊 Options analysis — {ts}")

    result = {"timestamp": ts, "status": "ok", "message": "live derivit analysis", "BTC": {}, "ETH": {}}

    try:
        log.info("  Baixando book summaries…")
        tickers = get_tickers_bulk([])

        ok_count = 0
        for currency in ["BTC", "ETH"]:
            try:
                log.info(f"  Analisando {currency}…")
                spot = get_spot_price(currency)
                if spot == 0:
                    log.warning(f"  {currency}: spot price zero, pulando")
                    continue
                instruments = get_instruments(currency)
                analysis    = analyze_currency(currency, instruments, tickers, spot)
                result[currency] = analysis
                if analysis.get("by_expiry"):
                    ok_count += 1

                next_e = analysis.get("next_expiry","")
                mp     = analysis.get("next_max_pain", 0)
                pc     = analysis.get("put_call_ratio", 0)
                log.info(f"  {currency}: spot ${spot:,.0f} | next expiry {next_e} | max pain ${mp:,} | P/C {pc:.2f}")
                time.sleep(1)
            except Exception as e:
                log.error(f"  {currency} análise falhou: {e}")

        if ok_count == 0:
            fallback = build_fallback_options_data("Deribit/Binance indisponíveis; exibindo fallback estático")
            write_options_payload(fallback)
            return fallback

        if ok_count < 2:
            result["status"] = "partial"
            result["message"] = "partial options analysis; one currency missing"

        write_options_payload(result)
        return result

    except Exception as e:
        log.error(f"  análise global falhou: {e}")
        fallback = build_fallback_options_data(f"fallback após erro: {e}")
        write_options_payload(fallback)
        return fallback


if __name__ == "__main__":
    run_options_analysis()
