
#!/usr/bin/env python3
"""
spike_monitor.py — monitor robusto de spikes com fallback.
Gera data/spike_alerts.json e data/spike_state.json sempre válidos.
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from api import get_positions, get_btc_price
from config import DATA_DIR, LOG_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED

STATE_FILE = DATA_DIR / 'spike_state.json'
SPIKE_ALERTS_FILE = DATA_DIR / 'spike_alerts.json'
MIN_ADDR = 3
SPIKE_INCREASE_PCT = 25.0
SPIKE_NOTIONAL_MIN = 500_000
NEW_POSITION_MIN = 1_000_000
CLOSED_POSITION_MIN = 500_000
LIQ_APPROACH_PCT = 1.5
CONSENSUS_SHIFT_DELTA = 10.0
MAX_EVENTS = 500

log_file = LOG_DIR / f"spike_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler()])
log = logging.getLogger('spike')


def _safe_json_load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _atomic_write(path: Path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _load_state():
    return _safe_json_load(STATE_FILE, {'timestamp': None, 'addresses': [], 'positions': {}, 'health': {'total_polls':0,'successful_polls':0,'consecutive_failures':0,'last_successful_poll':None}})


def _save_state(d):
    _atomic_write(STATE_FILE, d)


def _load_alerts():
    fp = DATA_DIR / 'alerts.json'
    data = _safe_json_load(fp, [])
    if isinstance(data, list) and data:
        return data[0]
    return {}


def _load_spike_alerts_events():
    data = _safe_json_load(SPIKE_ALERTS_FILE, {'events': []})
    return data.get('events', []) if isinstance(data, dict) else []


def _save_spike_alerts(events, health=None):
    health = health or {'last_poll': None, 'addresses_monitored': 0, 'poll_success_rate': 0.0}
    counts24 = 0
    crit = alta = 0
    now = datetime.utcnow()
    for e in events:
        ts = e.get('ts') or e.get('timestamp')
        try:
            dt = datetime.fromisoformat(ts.replace('Z', ''))
            if (now - dt).total_seconds() <= 86400:
                counts24 += 1
        except Exception:
            pass
        if e.get('severity') == 'critica':
            crit += 1
        if e.get('severity') == 'alta':
            alta += 1
    payload = {
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'status': 'ok' if health.get('poll_success_rate', 0) > 0 else 'degraded',
        'count_total': len(events),
        'count_24h': counts24,
        'by_severity': {'critica': crit, 'alta': alta, 'media': max(len(events)-crit-alta, 0)},
        'events': events[:MAX_EVENTS],
        '_health': health,
    }
    _atomic_write(SPIKE_ALERTS_FILE, payload)


def _send_telegram(text):
    if not (TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    import requests
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage', json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}, timeout=15)
    except Exception as e:
        log.warning(f'Telegram falhou: {e}')


def _severity(notional, mid=5_000_000, high=20_000_000):
    if notional >= high:
        return 'critica'
    if notional >= mid:
        return 'alta'
    return 'media'


def _liq_price(entry, side, lev):
    if lev <= 1:
        return None
    return entry * (1 - 0.9 / lev) if side == 'LONG' else entry * (1 + 0.9 / lev)


def _take_snapshot(addresses):
    btc_now = get_btc_price()
    pos_map = {}
    success = 0
    for whale in addresses[:15]:
        try:
            df = get_positions(whale['address'])
            time.sleep(0.15)
            positions = {}
            if not df.empty:
                for _, r in df.iterrows():
                    key = f"{r['coin']}_{r['side']}"
                    positions[key] = {'coin': r['coin'], 'side': r['side'], 'notional': float(r['notional']), 'entry_px': float(r['entry_px']), 'upnl': float(r.get('upnl', 0)), 'leverage': float(r.get('leverage', 1))}
            pos_map[whale['address']] = {'display': whale['display'], 'qs': whale.get('quality_score', whale.get('qs', 0.5)), 'cs': whale.get('consistency_score', whale.get('cs', 0)), 'positions': positions}
            success += 1
        except Exception as e:
            log.warning(f"falha em {whale.get('display','?')}: {e}")
    return pos_map, btc_now, success


def detect_spikes(prev, curr, btc_now, ts):
    events = []
    for addr, cw in curr.items():
        display, qs, cs = cw['display'], cw['qs'], cw['cs']
        cp = cw['positions']
        pp = prev.get(addr, {}).get('positions', {})
        for key, c in cp.items():
            p = pp.get(key)
            if p is None and c['notional'] >= NEW_POSITION_MIN:
                events.append({'type':'NEW_POSITION','display':display,'address':addr,'qs':qs,'cs':cs,'coin':c['coin'],'side':c['side'],'notional':c['notional'],'entry_px':c['entry_px'],'ts':ts,'btc':btc_now,'severity':_severity(c['notional'],2_000_000,5_000_000)})
                continue
            if p:
                delta = (c['notional'] - p['notional']) / max(p['notional'], 1) * 100
                if delta >= SPIKE_INCREASE_PCT and c['notional'] >= SPIKE_NOTIONAL_MIN:
                    events.append({'type':'SPIKE_LONG' if c['side']=='LONG' else 'SPIKE_SHORT','display':display,'address':addr,'qs':qs,'cs':cs,'coin':c['coin'],'side':c['side'],'notional':c['notional'],'prev_notional':p['notional'],'delta_pct':round(delta,1),'entry_px':c['entry_px'],'ts':ts,'btc':btc_now,'severity':_severity(c['notional'])})
        for key, p in pp.items():
            if key not in cp and p['notional'] >= CLOSED_POSITION_MIN:
                events.append({'type':'POSITION_CLOSED','display':display,'address':addr,'qs':qs,'cs':cs,'coin':p['coin'],'side':p['side'],'notional':p['notional'],'entry_px':p['entry_px'],'ts':ts,'btc':btc_now,'severity':_severity(p['notional'],3_000_000,10_000_000)})
        cc = {p['coin']: p['side'] for p in cp.values()}
        pc = {p['coin']: p['side'] for p in pp.values()}
        for coin, side in cc.items():
            if coin in pc and pc[coin] != side:
                n = cp.get(f'{coin}_{side}', {}).get('notional', 0)
                events.append({'type':'SIDE_FLIP','display':display,'address':addr,'qs':qs,'cs':cs,'coin':coin,'side':side,'prev_side':pc[coin],'notional':n,'ts':ts,'btc':btc_now,'severity':'alta'})
        for key, c in cp.items():
            liq = _liq_price(c['entry_px'], c['side'], c.get('leverage',1))
            if liq is None:
                continue
            dist = ((btc_now-liq)/btc_now*100) if c['side']=='LONG' else ((liq-btc_now)/btc_now*100)
            if 0 <= dist <= LIQ_APPROACH_PCT and c['notional'] >= 1_000_000:
                events.append({'type':'LIQ_APPROACH','display':display,'address':addr,'qs':qs,'cs':cs,'coin':c['coin'],'side':c['side'],'notional':c['notional'],'liq_price':round(liq,2),'dist_pct':round(dist,2),'ts':ts,'btc':btc_now,'severity':'critica'})
    if prev:
        def consensus(pm):
            tl = ts2 = 0
            for w in pm.values():
                for p in w.get('positions', {}).values():
                    if p['side'] == 'LONG':
                        tl += p['notional']
                    else:
                        ts2 += p['notional']
            total = tl + ts2
            return tl / total * 100 if total else 50
        pl, cl = consensus(prev), consensus(curr)
        delta = cl - pl
        if abs(delta) >= CONSENSUS_SHIFT_DELTA:
            events.append({'type':'CONSENSUS_SHIFT','display':'SYSTEM','address':'aggregate','direction':'→ MAIS BULLISH' if delta>0 else '→ MAIS BEARISH','prev_long_pct':round(pl,1),'curr_long_pct':round(cl,1),'delta':round(delta,1),'ts':ts,'btc':btc_now,'severity':'alta' if abs(delta)>=15 else 'media'})
    return events


def _format_telegram(ev):
    sev = {'critica':'🚨','alta':'⚡','media':'🔔'}.get(ev.get('severity','media'),'🔔')
    return (
        f"{sev} <b>{ev['type']}</b>\n"
        f"<b>Baleia:</b> {ev.get('display','SYSTEM')}\n"
        f"<b>Ativo:</b> {ev.get('coin','—')} {ev.get('side','')}\n"
        f"<b>Notional:</b> ${ev.get('notional',0):,.0f}"
    )


def run_spike_check():
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    log.info(f'⚡ Spike check — {ts}')
    state = _load_state()
    prev = state.get('positions', {})
    addresses = state.get('addresses', [])
    if not addresses:
        alerts = _load_alerts()
        addresses = []
        for w in alerts.get('whale_positions', []):
            addr = w.get('address', '')
            if addr and len(addr) > 10:
                addresses.append({'address': addr, 'display': w.get('display', addr[:8]), 'quality_score': w.get('quality_score', 0.5), 'consistency_score': w.get('consistency_score', 0)})
        if addresses:
            state['addresses'] = addresses
    if len(addresses) < MIN_ADDR:
        log.warning(f'Sem baleias suficientes para spikes ({len(addresses)})')
        state.setdefault('health', {})['total_polls'] = state.get('health', {}).get('total_polls', 0) + 1
        _save_state(state)
        _save_spike_alerts(_load_spike_alerts_events(), {'last_poll': ts, 'addresses_monitored': len(addresses), 'poll_success_rate': 0.0, 'error': 'insufficient_addresses'})
        return
    curr, btc_now, success = _take_snapshot(addresses)
    events = detect_spikes(prev, curr, btc_now, ts)
    existing = _load_spike_alerts_events()
    all_events = events + existing
    health = state.get('health', {})
    health['total_polls'] = health.get('total_polls', 0) + 1
    health['successful_polls'] = health.get('successful_polls', 0) + (1 if success > 0 else 0)
    health['consecutive_failures'] = 0 if success > 0 else health.get('consecutive_failures', 0) + 1
    health['last_successful_poll'] = ts if success > 0 else health.get('last_successful_poll')
    state['timestamp'] = ts
    state['positions'] = curr
    state['addresses'] = addresses
    state['health'] = health
    _save_state(state)
    _save_spike_alerts(all_events, {'last_poll': ts, 'addresses_monitored': len(addresses), 'poll_success_rate': round(success / max(min(len(addresses), 15), 1), 2), 'btc_price': btc_now})
    for ev in events[:10]:
        _send_telegram(_format_telegram(ev))
    log.info(f'✅ spikes salvos | novos={len(events)} cobertura={success}/{min(len(addresses),15)}')


if __name__ == '__main__':
    run_spike_check()
