
#!/usr/bin/env python3
"""
market_context.py — gera data/market_context.json e history com fallback robusto.
Compatível com whale_alerts.html atual.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from config import DATA_DIR, LOG_DIR, INFO_URL

OUT_FILE = DATA_DIR / "market_context.json"
HIST_FILE = DATA_DIR / "market_context_history.json"
TIMEOUT = 20
MAX_HIST = 240

log_file = LOG_DIR / f"market_context_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("market_context")


def _post(payload: dict[str, Any]) -> Any:
    r = requests.post(INFO_URL, json=payload, headers={"Content-Type":"application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _safe_float(x, default=0.0):
    try:
        return float(x or 0)
    except Exception:
        return default


def _load_alerts_latest() -> dict:
    alerts = DATA_DIR / 'alerts.json'
    if not alerts.exists():
        return {}
    try:
        data = json.loads(alerts.read_text(encoding='utf-8'))
        if isinstance(data, list) and data:
            return data[0]
    except Exception as e:
        log.warning(f'alerts.json inválido: {e}')
    return {}


def _load_options_summary() -> dict:
    fp = DATA_DIR / 'options_data.json'
    if not fp.exists():
        return {}
    try:
        data = json.loads(fp.read_text(encoding='utf-8'))
        btc = data.get('BTC', {}) if isinstance(data, dict) else {}
        if not isinstance(btc, dict):
            return {}
        return {
            'put_call_ratio': _safe_float(btc.get('put_call_ratio', 0)),
            'max_pain': _safe_float(btc.get('next_max_pain', 0)),
            'call_pct': _safe_float(btc.get('call_pct', 0)),
            'put_pct': _safe_float(btc.get('put_pct', 0)),
            'top_calls': (btc.get('by_expiry', {}) or {}).get(btc.get('next_expiry'), {}).get('top_calls', []),
            'top_puts': (btc.get('by_expiry', {}) or {}).get(btc.get('next_expiry'), {}).get('top_puts', []),
        }
    except Exception as e:
        log.warning(f'options_data.json inválido: {e}')
        return {}


def _get_hl_context():
    out = {'btc_mark':0,'eth_mark':0,'btc_funding':0,'eth_funding':0,'btc_oi_usd':0,'eth_oi_usd':0,'top5':[]}
    try:
        data = _post({'type':'metaAndAssetCtxs'})
        if not isinstance(data, list) or len(data) < 2:
            return out
        meta, ctxs = data[0], data[1]
        universe = meta.get('universe', [])
        rows = []
        for u, c in zip(universe, ctxs):
            coin = u.get('name', '')
            mark = _safe_float(c.get('markPx'))
            funding = _safe_float(c.get('funding')) * 100
            oi = _safe_float(c.get('openInterest')) * mark
            rows.append({'coin': coin, 'mark': mark, 'funding': funding, 'oi_usd': oi})
            if coin == 'BTC':
                out['btc_mark'] = mark
                out['btc_funding'] = funding
                out['btc_oi_usd'] = oi
            elif coin == 'ETH':
                out['eth_mark'] = mark
                out['eth_funding'] = funding
                out['eth_oi_usd'] = oi
        out['top5'] = sorted(rows, key=lambda x: x['oi_usd'], reverse=True)[:5]
    except Exception as e:
        log.warning(f'HL context falhou: {e}')
    return out


def _get_binance_price(symbol='BTCUSDT'):
    try:
        r = requests.get('https://api.binance.com/api/v3/ticker/price', params={'symbol':symbol}, timeout=10)
        r.raise_for_status()
        return _safe_float(r.json().get('price'))
    except Exception:
        return 0.0


def _get_binance_open_interest(symbol='BTCUSDT'):
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/openInterest', params={'symbol':symbol}, timeout=10)
        r.raise_for_status()
        return _safe_float(r.json().get('openInterest'))
    except Exception:
        return 0.0


def _get_binance_long_short(symbol='BTCUSDT'):
    try:
        r = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio', params={'symbol':symbol,'period':'5m','limit':1}, timeout=10)
        r.raise_for_status()
        arr = r.json()
        if arr:
            row = arr[0]
            return _safe_float(row.get('longAccount')), _safe_float(row.get('shortAccount'))
    except Exception:
        pass
    return 0.5, 0.5


def _get_binance_funding(symbol='BTCUSDT'):
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/fundingRate', params={'symbol':symbol,'limit':1}, timeout=10)
        r.raise_for_status()
        arr = r.json()
        if arr:
            return _safe_float(arr[-1].get('fundingRate')) * 100
    except Exception:
        pass
    return 0.0


def _load_history() -> list:
    if not HIST_FILE.exists():
        return []
    try:
        data = json.loads(HIST_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def build_context() -> dict:
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    alerts = _load_alerts_latest()
    opts = _load_options_summary()
    hl = _get_hl_context()

    btc_spot = _get_binance_price('BTCUSDT') or hl['btc_mark']
    eth_spot = _get_binance_price('ETHUSDT') or hl['eth_mark']
    btc_oi = _get_binance_open_interest('BTCUSDT') * (btc_spot or 0)
    eth_oi = _get_binance_open_interest('ETHUSDT') * (eth_spot or 0)
    btc_long_ratio, btc_short_ratio = _get_binance_long_short('BTCUSDT')
    eth_long_ratio, eth_short_ratio = _get_binance_long_short('ETHUSDT')
    btc_funding = _get_binance_funding('BTCUSDT')
    eth_funding = _get_binance_funding('ETHUSDT')

    whales_long = _safe_float(alerts.get('long_pct', 50))
    whales_short = _safe_float(alerts.get('short_pct', 50))
    active_whales = int(alerts.get('active_whales', 0) or 0)

    funding_score = -12 if btc_funding > 0.03 else (-6 if btc_funding > 0.01 else (10 if btc_funding < -0.01 else 0))
    long_short_score = -10 if btc_long_ratio > 0.60 else (8 if btc_long_ratio < 0.48 else 0)
    liq_long = _safe_float(alerts.get('total_long', 0))
    liq_short = _safe_float(alerts.get('total_short', 0))
    # use whale notional imbalance as proxy if no liquidation feed
    liq_score = 8 if liq_short > liq_long * 1.15 else (-8 if liq_long > liq_short * 1.15 else 0)
    basis_1h = 0.0
    if btc_spot and hl['btc_mark']:
        basis_1h = (btc_spot - hl['btc_mark']) / hl['btc_mark'] * 100
    spot_flow_score = 8 if basis_1h > 0.05 else (-8 if basis_1h < -0.05 else 0)
    pc_ratio = _safe_float(opts.get('put_call_ratio', 1.0))
    options_score = 10 if pc_ratio > 1.2 else (-6 if 0 < pc_ratio < 0.8 else 0)
    whale_consensus_score = 15 if whales_long >= 65 else (-15 if whales_short >= 65 else (6 if whales_long >= 55 else (-6 if whales_short >= 55 else 0)))

    total_score = funding_score + long_short_score + liq_score + spot_flow_score + options_score + whale_consensus_score
    bias = 'BULLISH_FORTE' if total_score >= 25 else 'BULLISH' if total_score >= 10 else 'BEARISH_FORTE' if total_score <= -25 else 'BEARISH' if total_score <= -10 else 'NEUTRO'

    ctx = {
        'timestamp': ts,
        'score': total_score,
        'market_score': {
            'total_score': round(total_score, 1),
            'bias': bias,
            'components': {
                'funding': {'score': funding_score, 'value': round(btc_funding, 4)},
                'long_short': {'score': long_short_score, 'value': round(btc_long_ratio, 4)},
                'liquidations': {'score': liq_score, 'long_usd': round(liq_long, 0), 'short_usd': round(liq_short, 0)},
                'spot_flow': {'score': spot_flow_score, 'basis': round(basis_1h, 4)},
                'options': {'score': options_score, 'put_call_ratio': round(pc_ratio, 3)},
                'whales': {'score': whale_consensus_score, 'long_pct': round(whales_long, 2), 'short_pct': round(whales_short, 2), 'active_whales': active_whales},
            }
        },
        'binance': {
            'BTC': {'funding_rate': round(btc_funding, 4), 'oi_usd': round(btc_oi, 0), 'long_ratio': round(btc_long_ratio, 4), 'short_ratio': round(btc_short_ratio, 4), 'spot_price': round(btc_spot, 2)},
            'ETH': {'funding_rate': round(eth_funding, 4), 'oi_usd': round(eth_oi, 0), 'long_ratio': round(eth_long_ratio, 4), 'short_ratio': round(eth_short_ratio, 4), 'spot_price': round(eth_spot, 2)},
        },
        'spot_flows': {
            'BTC': {'spot_price': round(btc_spot, 2), 'basis_1h': round(basis_1h, 4), 'spot_chg_1h': 0.0},
            'ETH': {'spot_price': round(eth_spot, 2), 'basis_1h': round(((eth_spot-hl['eth_mark'])/hl['eth_mark']*100) if eth_spot and hl['eth_mark'] else 0, 4), 'spot_chg_1h': 0.0},
        },
        'liquidations': {
            'BTC': {'long_liq_usd': round(liq_long, 0), 'short_liq_usd': round(liq_short, 0), 'liq_bias': 'LONG' if liq_long > liq_short else 'SHORT' if liq_short > liq_long else 'NEUTRO'},
            'ETH': {'long_liq_usd': 0, 'short_liq_usd': 0, 'liq_bias': 'NEUTRO'},
        },
        'options': {
            'put_call_ratio': round(pc_ratio, 3),
            'max_pain': round(_safe_float(opts.get('max_pain', 0)), 0),
            'call_pct': round(_safe_float(opts.get('call_pct', 0)), 1),
            'put_pct': round(_safe_float(opts.get('put_pct', 0)), 1),
            'top_calls': opts.get('top_calls', [])[:5],
            'top_puts': opts.get('top_puts', [])[:5],
        },
        'hl_summary': {
            'btc_funding': round(hl['btc_funding'], 4),
            'eth_funding': round(hl['eth_funding'], 4),
            'btc_oi_usd': round(hl['btc_oi_usd'], 0),
            'eth_oi_usd': round(hl['eth_oi_usd'], 0),
            'top5': [{'coin': r['coin'], 'funding': round(r['funding'], 4), 'oi_usd': round(r['oi_usd'], 0)} for r in hl['top5']],
        },
    }
    return ctx


def main():
    ctx = build_context()
    OUT_FILE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    hist = _load_history()
    hist.insert(0, {'timestamp': ctx['timestamp'], 'score': ctx.get('score', 0), 'bias': ctx['market_score']['bias']})
    hist = hist[:MAX_HIST]
    HIST_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info(f"✅ market_context salvo | score={ctx['score']} bias={ctx['market_score']['bias']}")


if __name__ == '__main__':
    main()
