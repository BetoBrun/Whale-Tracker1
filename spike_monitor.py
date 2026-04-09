#!/usr/bin/env python3
"""
spike_monitor.py — Monitoramento de alta frequência (10 min).
Detecta movimentos fora da curva e envia alertas via Telegram.
FIX: sempre salva spike_alerts.json e spike_state.json.
"""

import json, sys, time, logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_DIR, LOG_DIR, INFO_URL
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED
from api import get_positions, get_btc_price

SPIKE_NOTIONAL_MIN    = 500_000
SPIKE_INCREASE_PCT    = 25.0
NEW_POSITION_MIN      = 1_000_000
CLOSED_POSITION_MIN   = 500_000
LIQ_APPROACH_PCT      = 1.5
CONSENSUS_SHIFT_DELTA = 10.0
MAX_SPIKE_ALERTS      = 200

STATE_FILE       = DATA_DIR / "spike_state.json"
SPIKE_ALERTS_FILE = DATA_DIR / "spike_alerts.json"

log_file = LOG_DIR / f"spike_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("spike")


def _load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except: return {}
    return {}

def _save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

def _load_spike_alerts():
    if SPIKE_ALERTS_FILE.exists():
        try: return json.loads(SPIKE_ALERTS_FILE.read_text(encoding="utf-8"))
        except: return []
    return []

def _save_spike_alerts(alerts):
    SPIKE_ALERTS_FILE.write_text(
        json.dumps(alerts[:MAX_SPIKE_ALERTS], ensure_ascii=False, indent=2),
        encoding="utf-8")

def _take_quick_snapshot(addresses):
    btc_price = get_btc_price()
    pos_map = {}
    for whale in addresses:
        addr, display, qs = whale["address"], whale["display"], whale.get("quality_score", 0.5)
        try:
            df = get_positions(addr)
            time.sleep(0.12)
        except Exception as e:
            log.warning(f"  ⚠ {display}: {e}")
            continue
        positions = {}
        if not df.empty:
            for _, r in df.iterrows():
                key = f"{r['coin']}_{r['side']}"
                positions[key] = {
                    "coin": r["coin"], "side": r["side"],
                    "notional": float(r["notional"]),
                    "entry_px": float(r["entry_px"]),
                    "upnl": float(r.get("upnl", 0)),
                    "leverage": float(r.get("leverage", 1)),
                }
        pos_map[addr] = {"display": display, "qs": qs, "positions": positions}
    return pos_map, btc_price

def _liq_price(entry, side, lev):
    if lev <= 1: return None
    return entry * (1 - 0.9/lev) if side == "LONG" else entry * (1 + 0.9/lev)

def _severity(notional, mid, high):
    if notional >= high: return "critica"
    if notional >= mid:  return "alta"
    return "media"

def detect_spikes(prev, curr, btc_now, ts):
    events = []
    for addr, cw in curr.items():
        display, qs = cw["display"], cw["qs"]
        cp = cw["positions"]
        pp = prev.get(addr, {}).get("positions", {})

        for key, c in cp.items():
            p = pp.get(key)
            if p is None:
                if c["notional"] >= NEW_POSITION_MIN:
                    events.append({"type":"NEW_POSITION","display":display,"qs":qs,
                        "coin":c["coin"],"side":c["side"],"notional":c["notional"],
                        "entry_px":c["entry_px"],"ts":ts,"btc":btc_now,
                        "severity":_severity(c["notional"],2_000_000,5_000_000)})
                continue
            delta = (c["notional"] - p["notional"]) / p["notional"] * 100
            if delta >= SPIKE_INCREASE_PCT and c["notional"] >= SPIKE_NOTIONAL_MIN:
                t = "SPIKE_LONG" if c["side"] == "LONG" else "SPIKE_SHORT"
                events.append({"type":t,"display":display,"qs":qs,
                    "coin":c["coin"],"side":c["side"],"notional":c["notional"],
                    "prev_notional":p["notional"],"delta_pct":round(delta,1),
                    "entry_px":c["entry_px"],"ts":ts,"btc":btc_now,
                    "severity":_severity(c["notional"],5_000_000,20_000_000)})

        for key, p in pp.items():
            if key not in cp and p["notional"] >= CLOSED_POSITION_MIN:
                events.append({"type":"POSITION_CLOSED","display":display,"qs":qs,
                    "coin":p["coin"],"side":p["side"],"notional":p["notional"],
                    "entry_px":p["entry_px"],"ts":ts,"btc":btc_now,
                    "severity":_severity(p["notional"],3_000_000,10_000_000)})

        cc = {p["coin"]:p["side"] for p in cp.values()}
        pc = {p["coin"]:p["side"] for p in pp.values()}
        for coin, cs in cc.items():
            if coin in pc and pc[coin] != cs:
                n = cp.get(f"{coin}_{cs}",{}).get("notional",0)
                events.append({"type":"SIDE_FLIP","display":display,"qs":qs,
                    "coin":coin,"side":cs,"prev_side":pc[coin],"notional":n,
                    "ts":ts,"btc":btc_now,"severity":"alta"})

        for key, c in cp.items():
            liq = _liq_price(c["entry_px"], c["side"], c["leverage"])
            if liq is None: continue
            dist = (btc_now-liq)/btc_now*100 if c["side"]=="LONG" else (liq-btc_now)/btc_now*100
            if 0 <= dist <= LIQ_APPROACH_PCT and c["notional"] >= 1_000_000:
                events.append({"type":"LIQ_APPROACH","display":display,"qs":qs,
                    "coin":c["coin"],"side":c["side"],"notional":c["notional"],
                    "liq_price":round(liq,2),"dist_pct":round(dist,2),
                    "btc":btc_now,"ts":ts,"severity":"critica"})

    if prev:
        def consensus(pm):
            tl=ts2=0
            for w in pm.values():
                for p in w["positions"].values():
                    if p["side"]=="LONG": tl+=p["notional"]
                    else: ts2+=p["notional"]
            t=tl+ts2; return tl/t*100 if t else 50
        pl, cl = consensus(prev), consensus(curr)
        delta = cl - pl
        if abs(delta) >= CONSENSUS_SHIFT_DELTA:
            events.append({"type":"CONSENSUS_SHIFT",
                "direction":"→ MAIS BULLISH" if delta>0 else "→ MAIS BEARISH",
                "prev_long_pct":round(pl,1),"curr_long_pct":round(cl,1),
                "delta":round(delta,1),"ts":ts,"btc":btc_now,
                "severity":"alta" if abs(delta)>=15 else "media"})
    return events

def _fmt(n):
    if n>=1e9: return f"${n/1e9:.1f}B"
    if n>=1e6: return f"${n/1e6:.1f}M"
    return f"${n/1e3:.0f}K"

def _format_telegram(ev):
    sev = {"critica":"🚨","alta":"⚡","media":"🔔"}.get(ev.get("severity","media"),"🔔")
    t, btc, ts = ev["type"], f"${ev.get('btc',0):,.0f}", ev.get("ts","")
    if t in ("SPIKE_LONG","SPIKE_SHORT"):
        arrow = "🟢⬆" if t=="SPIKE_LONG" else "🔴⬇"
        return (f"{sev} <b>SPIKE DE POSIÇÃO</b> {arrow}\n"
                f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
                f"<b>Ativo:</b> {ev['coin']} {ev['side']}\n"
                f"<b>Notional:</b> {_fmt(ev['notional'])} (+{ev['delta_pct']:.1f}%)\n"
                f"<b>Entrada:</b> ${ev['entry_px']:,.2f} | BTC: {btc}\n"
                f"<b>UTC:</b> {ts}")
    elif t=="NEW_POSITION":
        arrow = "🟢" if ev['side']=="LONG" else "🔴"
        return (f"{sev} <b>NOVA POSIÇÃO</b> {arrow}\n"
                f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
                f"<b>Ativo:</b> {ev['coin']} {ev['side']}\n"
                f"<b>Notional:</b> {_fmt(ev['notional'])}\n"
                f"<b>Entrada:</b> ${ev['entry_px']:,.2f} | BTC: {btc}\n"
                f"<b>UTC:</b> {ts}")
    elif t=="SIDE_FLIP":
        return (f"{sev} <b>VIRADA DE LADO!</b> 🔄\n"
                f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
                f"<b>Ativo:</b> {ev['coin']} {ev['prev_side']} → {ev['side']}\n"
                f"<b>Notional:</b> {_fmt(ev.get('notional',0))} | BTC: {btc}\n"
                f"<b>UTC:</b> {ts}")
    elif t=="POSITION_CLOSED":
        return (f"{sev} <b>POSIÇÃO FECHADA</b> ✖\n"
                f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
                f"<b>Ativo:</b> {ev['coin']} {ev['side']}\n"
                f"<b>Notional saído:</b> {_fmt(ev['notional'])} | BTC: {btc}\n"
                f"<b>UTC:</b> {ts}")
    elif t=="LIQ_APPROACH":
        return (f"🚨 <b>LIQUIDAÇÃO PRÓXIMA!</b>\n"
                f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
                f"<b>Ativo:</b> {ev['coin']} {ev['side']}\n"
                f"<b>Notional:</b> {_fmt(ev['notional'])}\n"
                f"<b>Liq. est.:</b> ${ev['liq_price']:,.2f} ({ev['dist_pct']:.2f}% distância)\n"
                f"<b>BTC:</b> {btc} | <b>UTC:</b> {ts}")
    elif t=="CONSENSUS_SHIFT":
        arrow = "📈" if ev["delta"]>0 else "📉"
        return (f"{sev} <b>VIRADA DE CONSENSO</b> {arrow}\n"
                f"<b>Mudança:</b> {ev['direction']}\n"
                f"<b>Antes:</b> {ev['prev_long_pct']:.1f}% Long\n"
                f"<b>Agora:</b> {ev['curr_long_pct']:.1f}% Long\n"
                f"<b>Delta:</b> {ev['delta']:+.1f}pp | BTC: {btc}")
    return f"⚡ {t} | {ts}"

def _send_telegram(text):
    if not (TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID): return
    import requests as req
    req.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
             json={"chat_id":TELEGRAM_CHAT_ID,"text":text,"parse_mode":"HTML",
                   "disable_web_page_preview":True}, timeout=15)


def run_spike_check():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"⚡ Spike check — {ts}")

    prev_state = _load_state()
    prev_pos   = prev_state.get("positions", {})
    addresses  = prev_state.get("addresses", [])

    # carrega addresses do alerts.json se não tiver no state
    if not addresses:
        alerts_file = DATA_DIR / "alerts.json"
        if alerts_file.exists():
            try:
                alerts = json.loads(alerts_file.read_text(encoding="utf-8"))
                if alerts:
                    addresses = [
                        {"address": w.get("address",""), "display": w["display"],
                         "quality_score": w.get("quality_score", 0.5)}
                        for w in alerts[0].get("whale_positions", [])
                        if w.get("display") and w.get("address","")
                    ]
                    # filtra addresses vazios
                    addresses = [a for a in addresses if len(a["address"]) > 10]
                    log.info(f"  📋 {len(addresses)} baleias do alerts.json")
            except Exception as e:
                log.warning(f"  ⚠ Não carregou addresses: {e}")

    if not addresses:
        log.warning("  ⚠ Sem baleias conhecidas. Rode run_once.py primeiro.")
        # SEMPRE salva arquivos vazios para não dar 404 na página
        _save_spike_alerts(_load_spike_alerts())
        _save_state({"timestamp": ts, "addresses": [], "positions": {}})
        return

    log.info(f"  📡 Coletando {len(addresses)} baleias…")
    curr_pos, btc_now = _take_quick_snapshot(addresses)
    log.info(f"  ✅ BTC ${btc_now:,.0f} | {len(curr_pos)} baleias")

    events = detect_spikes(prev_pos, curr_pos, btc_now, ts)
    log.info(f"  🔍 {len(events)} evento(s)")

    # SEMPRE salva state atualizado
    _save_state({"timestamp": ts, "addresses": addresses, "positions": curr_pos})

    # SEMPRE salva spike_alerts (mesmo vazio na primeira vez)
    existing = _load_spike_alerts()
    if events:
        new_alerts = [{"id": f"{ts}_{i}", **ev} for i, ev in enumerate(events)]
        _save_spike_alerts(new_alerts + existing)

        order = {"critica":0,"alta":1,"media":2}
        events.sort(key=lambda e: order.get(e.get("severity","media"),2))
        sent = 0
        for ev in events:
            try:
                _send_telegram(_format_telegram(ev))
                sent += 1
                time.sleep(0.5)
            except Exception as e:
                log.warning(f"  ⚠ Telegram: {e}")
        log.info(f"  📣 {sent} alertas enviados")
        for ev in events:
            log.info(f"    [{ev.get('severity','?').upper()}] {ev['type']} — {ev.get('display','?')} {ev.get('coin','')} {ev.get('side','')}")
    else:
        # salva mesmo sem eventos para o arquivo existir
        _save_spike_alerts(existing)
        log.info("  ✅ Sem eventos — state e alerts atualizados")


if __name__ == "__main__":
    run_spike_check()
