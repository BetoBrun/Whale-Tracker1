
#!/usr/bin/env python3
"""
options_analyzer.py — análise robusta de opções BTC/ETH via Deribit.
Nunca publica BTC/ETH vazios e preserva último snapshot válido.
"""
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

from config import DATA_DIR, LOG_DIR

OPTIONS_FILE = DATA_DIR / 'options_data.json'
TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
BASE = 'https://www.deribit.com/api/v2/public'

log_file = LOG_DIR / f"options_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler()])
log = logging.getLogger('options')


def _safe_float(x, default=0.0):
    try:
        return float(x or 0)
    except Exception:
        return default


def _get(endpoint, params=None):
    r = requests.get(f"{BASE}/{endpoint}", params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get('result', [])


def empty_currency_payload(currency, error='', source='fallback'):
    return {
        'status': 'error' if error else 'empty',
        'error': error,
        'source': source,
        'currency': currency,
        'spot_price': 0,
        'total_call_oi': 0,
        'total_put_oi': 0,
        'put_call_ratio': 0,
        'call_pct': 0,
        'put_pct': 0,
        'next_expiry': None,
        'next_max_pain': 0,
        'next_total_gex': 0,
        'next_vol_skew': 0,
        'next_gex_regime': 'NEUTRO',
        'by_expiry': {},
    }


def is_meaningful_payload(payload):
    return isinstance(payload, dict) and (payload.get('spot_price', 0) > 0) and (payload.get('by_expiry') or payload.get('total_call_oi', 0) > 0 or payload.get('total_put_oi', 0) > 0)


def load_previous_valid():
    if not OPTIONS_FILE.exists():
        return None
    try:
        data = json.loads(OPTIONS_FILE.read_text(encoding='utf-8'))
        if is_meaningful_payload(data.get('BTC', {})) or is_meaningful_payload(data.get('ETH', {})):
            return data
    except Exception as e:
        log.warning(f'último options_data inválido: {e}')
    return None


def parse_instrument_name(name):
    try:
        parts = name.split('-')
        return parts[0], parts[1], int(parts[2]), parts[3]
    except Exception:
        return None, None, None, None


def get_instruments(currency='BTC'):
    instruments = _get('get_instruments', {'currency': currency, 'kind':'option', 'expired':False})
    log.info(f'  {currency}: {len(instruments)} instrumentos ativos')
    return instruments


def get_tickers_bulk():
    result = {}
    for currency in ['BTC', 'ETH']:
        try:
            summaries = _get('get_book_summary_by_currency', {'currency': currency, 'kind':'option'})
            log.info(f'  {currency}: {len(summaries)} book summaries')
            for s in summaries:
                name = s.get('instrument_name', '')
                result[name] = {
                    'open_interest': _safe_float(s.get('open_interest')),
                    'volume_24h': _safe_float(s.get('volume')),
                    'bid': _safe_float(s.get('bid_price')),
                    'ask': _safe_float(s.get('ask_price')),
                    'mid': _safe_float(s.get('mid_price')),
                    'iv': _safe_float(s.get('mark_iv')),
                    'mark_price': _safe_float(s.get('mark_price')),
                    'underlying': _safe_float(s.get('underlying_price')),
                }
            time.sleep(0.3)
        except Exception as e:
            log.warning(f'  book_summary {currency}: {e}')
    return result


def compute_max_pain(calls, puts, strikes):
    if not strikes:
        return 0
    pain = {}
    for s in strikes:
        call_loss = sum(oi * max(0, s-k) for k, oi in calls.items())
        put_loss = sum(oi * max(0, k-s) for k, oi in puts.items())
        pain[s] = call_loss + put_loss
    return min(pain, key=pain.get)


def compute_gex_by_strike(strikes_data, spot_price):
    gex = {}
    for strike, d in strikes_data.items():
        dist_pct = abs(strike - spot_price) / max(spot_price, 1)
        gamma_proxy = 0.0001 / (dist_pct + 0.005)
        call_oi = d.get('call_oi', 0)
        put_oi = d.get('put_oi', 0)
        gex[strike] = round((call_oi - put_oi) * gamma_proxy * (spot_price ** 2) * 0.01, 0)
    return gex


def compute_skew(strikes_data, spot_price):
    otm_call_ivs, otm_put_ivs = [], []
    for strike, d in strikes_data.items():
        dist = (strike - spot_price) / max(spot_price, 1)
        civ = d.get('call_iv', 0)
        piv = d.get('put_iv', 0)
        if 0.05 <= dist <= 0.35 and civ > 0:
            otm_call_ivs.append(civ)
        if -0.35 <= dist <= -0.05 and piv > 0:
            otm_put_ivs.append(piv)
    if otm_call_ivs and otm_put_ivs:
        return round(sum(otm_put_ivs)/len(otm_put_ivs) - sum(otm_call_ivs)/len(otm_call_ivs), 2)
    return 0


def get_spot_price(currency):
    try:
        idx = _get('get_index_price', {'index_name': f'{currency.lower()}_usd'})
        px = _safe_float(idx.get('index_price')) if isinstance(idx, dict) else 0
        if px > 0:
            return px
    except Exception as e:
        log.warning(f'  {currency}: falha Deribit index_price: {e}')
    try:
        r = requests.get('https://api.binance.com/api/v3/ticker/price', params={'symbol': f'{currency}USDT'}, timeout=10)
        r.raise_for_status()
        return _safe_float(r.json().get('price'))
    except Exception as e:
        log.warning(f'  {currency}: falha Binance fallback: {e}')
        return 0


def analyze_currency(currency, instruments, tickers, spot_price):
    by_expiry = defaultdict(lambda: {'calls': {}, 'puts': {}, 'strikes_data': {}})
    for inst in instruments:
        name = inst.get('instrument_name', '')
        cur, expiry, strike, opt_type = parse_instrument_name(name)
        if cur != currency or not strike:
            continue
        ticker = tickers.get(name, {})
        oi = ticker.get('open_interest', 0)
        iv = ticker.get('iv', 0)
        vol24 = ticker.get('volume_24h', 0)
        sd = by_expiry[expiry]['strikes_data']
        sd.setdefault(strike, {})
        if opt_type == 'C':
            by_expiry[expiry]['calls'][strike] = by_expiry[expiry]['calls'].get(strike, 0) + oi
            sd[strike]['call_oi'] = sd[strike].get('call_oi', 0) + oi
            sd[strike]['call_iv'] = iv
            sd[strike]['call_vol'] = sd[strike].get('call_vol', 0) + vol24
        else:
            by_expiry[expiry]['puts'][strike] = by_expiry[expiry]['puts'].get(strike, 0) + oi
            sd[strike]['put_oi'] = sd[strike].get('put_oi', 0) + oi
            sd[strike]['put_iv'] = iv
            sd[strike]['put_vol'] = sd[strike].get('put_vol', 0) + vol24

    results = {}
    all_call_oi = all_put_oi = 0
    for expiry, data in sorted(by_expiry.items()):
        calls, puts, sd = data['calls'], data['puts'], data['strikes_data']
        strikes = sorted(set(list(calls.keys()) + list(puts.keys())))
        if not strikes:
            continue
        total_call = sum(calls.values())
        total_put = sum(puts.values())
        all_call_oi += total_call
        all_put_oi += total_put
        max_pain = compute_max_pain(calls, puts, strikes)
        gex = compute_gex_by_strike(sd, spot_price)
        total_gex = sum(gex.values())
        vol_skew = compute_skew(sd, spot_price)
        strike_oi = []
        for strike in strikes:
            strike_oi.append({'strike': strike, 'call_oi': round(sd.get(strike, {}).get('call_oi', 0), 1), 'put_oi': round(sd.get(strike, {}).get('put_oi', 0), 1), 'total_oi': round(sd.get(strike, {}).get('call_oi', 0) + sd.get(strike, {}).get('put_oi', 0), 1), 'gex': gex.get(strike, 0)})
        top_calls = sorted([{'strike': s, 'oi': v} for s, v in calls.items()], key=lambda x: x['oi'], reverse=True)[:5]
        top_puts = sorted([{'strike': s, 'oi': v} for s, v in puts.items()], key=lambda x: x['oi'], reverse=True)[:5]
        results[expiry] = {
            'expiry': expiry,
            'total_call_oi': round(total_call, 1),
            'total_put_oi': round(total_put, 1),
            'put_call_ratio': round(total_put / total_call, 3) if total_call else 0,
            'max_pain': max_pain,
            'total_gex': round(total_gex, 0),
            'vol_skew': vol_skew,
            'gex_regime': 'POSITIVO' if total_gex >= 0 else 'NEGATIVO',
            'top_calls': top_calls,
            'top_puts': top_puts,
            'strikes': strike_oi,
            'strikes_count': len(strikes),
        }
    next_expiry = sorted(results.keys())[0] if results else None
    return {
        'status': 'ok',
        'error': '',
        'source': 'deribit',
        'currency': currency,
        'spot_price': round(spot_price, 2),
        'total_call_oi': round(all_call_oi, 1),
        'total_put_oi': round(all_put_oi, 1),
        'put_call_ratio': round(all_put_oi / all_call_oi, 3) if all_call_oi else 0,
        'call_pct': round(all_call_oi / (all_call_oi + all_put_oi) * 100, 1) if all_call_oi + all_put_oi else 0,
        'put_pct': round(all_put_oi / (all_call_oi + all_put_oi) * 100, 1) if all_call_oi + all_put_oi else 0,
        'next_expiry': next_expiry,
        'next_max_pain': results[next_expiry]['max_pain'] if next_expiry else 0,
        'next_total_gex': results[next_expiry]['total_gex'] if next_expiry else 0,
        'next_vol_skew': results[next_expiry]['vol_skew'] if next_expiry else 0,
        'next_gex_regime': results[next_expiry]['gex_regime'] if next_expiry else 'NEUTRO',
        'by_expiry': results,
    }


def build_final_payload(ts, partial, previous_valid=None):
    out = {'timestamp': ts}
    for currency in ['BTC', 'ETH']:
        cur = partial.get(currency)
        if is_meaningful_payload(cur):
            out[currency] = cur
            continue
        prev = (previous_valid or {}).get(currency, {})
        if is_meaningful_payload(prev):
            prev = dict(prev)
            prev['status'] = 'stale'
            prev['error'] = (cur or {}).get('error', 'using_previous_valid_snapshot') if isinstance(cur, dict) else 'using_previous_valid_snapshot'
            prev['source'] = 'previous_valid_file'
            out[currency] = prev
            log.warning(f'  {currency}: usando snapshot anterior válido')
        else:
            out[currency] = empty_currency_payload(currency, (cur or {}).get('error', 'no_valid_data_available') if isinstance(cur, dict) else 'no_valid_data_available', 'generated_fallback')
            log.warning(f'  {currency}: gerando fallback estruturado')
    return out


def run_options_analysis():
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    log.info(f'📊 Options analysis — {ts}')
    previous_valid = load_previous_valid()
    result = {'BTC': empty_currency_payload('BTC'), 'ETH': empty_currency_payload('ETH')}
    tickers = get_tickers_bulk()
    if not tickers:
        log.warning('  Nenhum book summary retornado')
    for currency in ['BTC', 'ETH']:
        try:
            log.info(f'  Analisando {currency}…')
            spot = get_spot_price(currency)
            if spot <= 0:
                msg = f'{currency}: spot price zero'
                log.warning('  ' + msg)
                result[currency] = empty_currency_payload(currency, msg, 'spot_failed')
                continue
            instruments = get_instruments(currency)
            if not instruments:
                msg = f'{currency}: sem instrumentos ativos'
                log.warning('  ' + msg)
                result[currency] = empty_currency_payload(currency, msg, 'instruments_failed')
                continue
            analysis = analyze_currency(currency, instruments, tickers, spot)
            if not analysis.get('by_expiry'):
                msg = f'{currency}: análise sem expiries úteis'
                log.warning('  ' + msg)
                result[currency] = empty_currency_payload(currency, msg, 'analysis_empty')
                continue
            result[currency] = analysis
            log.info(f"  {currency}: spot ${spot:,.0f} | next expiry {analysis.get('next_expiry')} | max pain ${analysis.get('next_max_pain', 0):,.0f} | P/C {analysis.get('put_call_ratio', 0):.2f}")
            time.sleep(0.5)
        except Exception as e:
            log.exception(f'  {currency} análise falhou: {e}')
            result[currency] = empty_currency_payload(currency, str(e), 'exception')
    final_payload = build_final_payload(ts, result, previous_valid)
    OPTIONS_FILE.write_text(json.dumps(final_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info(f'  ✅ Salvo em {OPTIONS_FILE}')
    return final_payload


if __name__ == '__main__':
    run_options_analysis()
