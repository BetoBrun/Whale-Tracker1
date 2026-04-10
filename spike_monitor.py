#!/usr/bin/env python3
"""Robust spike monitor with guaranteed output files."""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_DIR, LOG_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED
from api import get_positions, get_btc_price

SPIKE_STATE_FILE = DATA_DIR / "spike_state.json"
SPIKE_ALERTS_FILE = DATA_DIR / "spike_alerts.json"
ALERTS_FILE = DATA_DIR / "alerts.json"
MIN_ADDRESS_LEN = 10
SPIKE_NOTIONAL_MIN = 500_000
SPIKE_INCREASE_PCT = 25.0

log_file = LOG_DIR / f"spike_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler()])
log = logging.getLogger("spike")


def _load(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        log.warning(f"load {path.name}: {e}")
    return default


def _save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _ensure_files():
    if not SPIKE_ALERTS_FILE.exists():
        _save(SPIKE_ALERTS_FILE, [])
    if not SPIKE_STATE_FILE.exists():
        _save(SPIKE_STATE_FILE, {"timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "addresses": [], "positions": {}})


def _load_addresses(state):
    addresses = state.get("addresses", [])
    if addresses:
        return [a for a in addresses if len(a.get("address", "")) > MIN_ADDRESS_LEN]
    alerts = _load(ALERTS_FILE, [])
    if isinstance(alerts, list) and alerts:
        positions = alerts[0].get("whale_positions", [])
        addrs = [{
            "address": w.get("address", ""),
            "display": w.get("display", ""),
            "quality_score": w.get("quality_score", 0.5),
        } for w in positions if len(w.get("address", "")) > MIN_ADDRESS_LEN]
        return addrs
    return []


def _take_snapshot(addresses):
    btc = get_btc_price()
    out = {}
    ok = 0
    for whale in addresses[:15]:
        try:
            df = get_positions(whale["address"])
            positions = {}
            if not df.empty:
                for _, r in df.iterrows():
                    key = f"{r['coin']}_{r['side']}"
                    positions[key] = {
                        "coin": r["coin"],
                        "side": r["side"],
                        "notional": float(r["notional"]),
                        "entry_px": float(r["entry_px"]),
                        "upnl": float(r.get("upnl", 0)),
                        "leverage": float(r.get("leverage", 1)),
                    }
            out[whale["address"]] = {"display": whale["display"], "qs": whale.get("quality_score", 0.5), "positions": positions}
            ok += 1
            time.sleep(0.12)
        except Exception as e:
            log.warning(f"{whale['display']}: {e}")
    return out, btc, ok


def _severity(notional):
    return "critica" if notional >= 20_000_000 else "alta" if notional >= 5_000_000 else "media"


def detect_spikes(prev, curr, btc_now, ts):
    events = []
    for addr, cw in curr.items():
        display, qs = cw["display"], cw["qs"]
        cp = cw["positions"]
        pp = prev.get(addr, {}).get("positions", {})
        for key, c in cp.items():
            p = pp.get(key)
            if not p:
                if c["notional"] >= 1_000_000:
                    events.append({"type":"NEW_POSITION","display":display,"address":addr,"qs":qs,"coin":c["coin"],"side":c["side"],"notional":c["notional"],"entry_px":c["entry_px"],"ts":ts,"btc":btc_now,"severity":_severity(c["notional"])})
                continue
            if p["notional"] > 0:
                delta = (c["notional"] - p["notional"]) / p["notional"] * 100
                if delta >= SPIKE_INCREASE_PCT and c["notional"] >= SPIKE_NOTIONAL_MIN:
                    events.append({"type":"SPIKE_LONG" if c["side"]=='LONG' else 'SPIKE_SHORT',"display":display,"address":addr,"qs":qs,"coin":c["coin"],"side":c["side"],"notional":c["notional"],"prev_notional":p["notional"],"delta_pct":round(delta,1),"entry_px":c["entry_px"],"ts":ts,"btc":btc_now,"severity":_severity(c["notional"])})
        for key, p in pp.items():
            if key not in cp and p["notional"] >= SPIKE_NOTIONAL_MIN:
                events.append({"type":"POSITION_CLOSED","display":display,"address":addr,"qs":qs,"coin":p["coin"],"side":p["side"],"notional":p["notional"],"entry_px":p["entry_px"],"ts":ts,"btc":btc_now,"severity":_severity(p["notional"])})
    return events


def run_spike_check():
    _ensure_files()
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    state = _load(SPIKE_STATE_FILE, {"timestamp": ts, "addresses": [], "positions": {}})
    prev_positions = state.get("positions", {})
    addresses = _load_addresses(state)
    if not addresses:
        log.warning("sem baleias com address; mantendo arquivos vazios válidos")
        _save(SPIKE_ALERTS_FILE, _load(SPIKE_ALERTS_FILE, []))
        _save(SPIKE_STATE_FILE, {"timestamp": ts, "addresses": [], "positions": {}})
        return
    curr_pos, btc_now, ok = _take_snapshot(addresses)
    events = detect_spikes(prev_positions, curr_pos, btc_now, ts)
    existing = _load(SPIKE_ALERTS_FILE, [])
    if not isinstance(existing, list):
        existing = []
    all_events = events + existing
    _save(SPIKE_ALERTS_FILE, all_events[:300])
    _save(SPIKE_STATE_FILE, {"timestamp": ts, "addresses": addresses, "positions": curr_pos, "ok_polled": ok, "btc": btc_now})
    log.info(f"spike check ok | addresses={len(addresses)} polled={ok} events={len(events)}")


if __name__ == '__main__':
    run_spike_check()
