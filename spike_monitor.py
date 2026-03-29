#!/usr/bin/env python3
"""
spike_monitor.py — Monitoramento de alta frequência (5-10 min)

Detecta movimentos fora da curva das baleias e envia alertas via Telegram.
Roda separado do tracker principal (que é horário).

Eventos detectados:
  - SPIKE_LONG / SPIKE_SHORT   : aumento repentino de posição (> threshold)
  - NEW_POSITION               : baleia que não tinha posição abriu uma grande
  - SIDE_FLIP                  : baleia virou de lado (LONG→SHORT ou vice-versa)
  - POSITION_CLOSED            : baleia fechou posição grande
  - CONSENSUS_SHIFT            : consenso geral mudou de direção em 1 ciclo
  - LIQ_APPROACH               : preço se aproximou < 1.5% do liq_price estimado
"""

import json
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    DATA_DIR, LOG_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED,
)
from api import get_leaderboard, get_positions, get_btc_price

# ── configurações do spike monitor ───────────────────────────────────────────
SPIKE_NOTIONAL_MIN     = 500_000    # notional mínimo para registrar spike ($)
SPIKE_INCREASE_PCT     = 25.0       # aumento % de notional que aciona alerta
NEW_POSITION_MIN       = 1_000_000  # tamanho mínimo de nova posição ($)
CLOSED_POSITION_MIN    = 500_000    # tamanho mínimo de posição fechada ($)
LIQ_APPROACH_PCT       = 1.5        # % de proximidade ao liq_price para alertar
CONSENSUS_SHIFT_DELTA  = 10.0       # mudança mínima em % long/short para alertar
MAX_SPIKE_ALERTS       = 200        # histórico máximo no JSON

STATE_FILE       = DATA_DIR / "spike_state.json"    # estado anterior das posições
SPIKE_ALERTS_FILE = DATA_DIR / "spike_alerts.json"  # feed de alertas rápidos

log_file = LOG_DIR / f"spike_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("spike")


# ══════════════════════════════════════════════════════════════════════════════
# STATE — salva e carrega posições anteriores para comparar
# ══════════════════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _load_spike_alerts() -> list:
    if SPIKE_ALERTS_FILE.exists():
        try:
            return json.loads(SPIKE_ALERTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_spike_alerts(alerts: list) -> None:
    SPIKE_ALERTS_FILE.write_text(
        json.dumps(alerts[:MAX_SPIKE_ALERTS], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT RÁPIDO — só posições, sem rebuildar leaderboard inteiro
# ══════════════════════════════════════════════════════════════════════════════

def _take_quick_snapshot(addresses: list[dict]) -> tuple[dict, float]:
    """
    Coleta posições das baleias conhecidas sem rebaixar o leaderboard.
    Retorna (pos_map, btc_price).
    pos_map = {address: {"display": ..., "positions": {coin+side: notional}}}
    """
    btc_price = get_btc_price()
    pos_map = {}

    for whale in addresses:
        addr    = whale["address"]
        display = whale["display"]
        qs      = whale.get("quality_score", 0.5)
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
                    "coin":     r["coin"],
                    "side":     r["side"],
                    "notional": float(r["notional"]),
                    "entry_px": float(r["entry_px"]),
                    "upnl":     float(r.get("upnl", 0)),
                    "leverage": float(r.get("leverage", 1)),
                }

        pos_map[addr] = {
            "display":   display,
            "qs":        qs,
            "positions": positions,
        }

    return pos_map, btc_price


# ══════════════════════════════════════════════════════════════════════════════
# DETECÇÃO DE EVENTOS
# ══════════════════════════════════════════════════════════════════════════════

def _liq_price(entry: float, side: str, lev: float) -> float | None:
    if lev <= 1:
        return None
    if side == "LONG":
        return entry * (1 - 0.9 / lev)
    return entry * (1 + 0.9 / lev)


def detect_spikes(prev: dict, curr: dict, btc_now: float, ts: str) -> list[dict]:
    events = []

    # ── por baleia ────────────────────────────────────────────────────────────
    for addr, curr_whale in curr.items():
        display = curr_whale["display"]
        qs      = curr_whale["qs"]
        curr_pos = curr_whale["positions"]
        prev_pos = prev.get(addr, {}).get("positions", {})

        # 1. SPIKE — posição existente aumentou muito
        for key, cp in curr_pos.items():
            pp = prev_pos.get(key)
            if pp is None:
                # posição nova
                if cp["notional"] >= NEW_POSITION_MIN:
                    events.append({
                        "type":     "NEW_POSITION",
                        "display":  display,
                        "qs":       qs,
                        "coin":     cp["coin"],
                        "side":     cp["side"],
                        "notional": cp["notional"],
                        "entry_px": cp["entry_px"],
                        "ts":       ts,
                        "btc":      btc_now,
                        "severity": _severity(cp["notional"], 2_000_000, 5_000_000),
                    })
                continue

            delta_pct = (cp["notional"] - pp["notional"]) / pp["notional"] * 100
            if (delta_pct >= SPIKE_INCREASE_PCT
                    and cp["notional"] >= SPIKE_NOTIONAL_MIN):
                spike_type = "SPIKE_LONG" if cp["side"] == "LONG" else "SPIKE_SHORT"
                events.append({
                    "type":      spike_type,
                    "display":   display,
                    "qs":        qs,
                    "coin":      cp["coin"],
                    "side":      cp["side"],
                    "notional":  cp["notional"],
                    "prev_notional": pp["notional"],
                    "delta_pct": round(delta_pct, 1),
                    "entry_px":  cp["entry_px"],
                    "ts":        ts,
                    "btc":       btc_now,
                    "severity":  _severity(cp["notional"], 5_000_000, 20_000_000),
                })

        # 2. POSIÇÃO FECHADA
        for key, pp in prev_pos.items():
            if key not in curr_pos and pp["notional"] >= CLOSED_POSITION_MIN:
                events.append({
                    "type":     "POSITION_CLOSED",
                    "display":  display,
                    "qs":       qs,
                    "coin":     pp["coin"],
                    "side":     pp["side"],
                    "notional": pp["notional"],
                    "entry_px": pp["entry_px"],
                    "ts":       ts,
                    "btc":      btc_now,
                    "severity": _severity(pp["notional"], 3_000_000, 10_000_000),
                })

        # 3. SIDE FLIP — tinha LONG em X, agora tem SHORT em X (ou vice-versa)
        curr_coins = {p["coin"]: p["side"] for p in curr_pos.values()}
        prev_coins = {p["coin"]: p["side"] for p in prev_pos.values()}
        for coin, curr_side in curr_coins.items():
            prev_side = prev_coins.get(coin)
            if prev_side and prev_side != curr_side:
                not_ = curr_pos.get(f"{coin}_{curr_side}", {}).get("notional", 0)
                events.append({
                    "type":      "SIDE_FLIP",
                    "display":   display,
                    "qs":        qs,
                    "coin":      coin,
                    "side":      curr_side,
                    "prev_side": prev_side,
                    "notional":  not_,
                    "ts":        ts,
                    "btc":       btc_now,
                    "severity":  "alta",
                })

        # 4. LIQ APPROACH — para posições existentes
        for key, cp in curr_pos.items():
            liq = _liq_price(cp["entry_px"], cp["side"], cp["leverage"])
            if liq is None:
                continue
            if cp["side"] == "LONG":
                dist_pct = (btc_now - liq) / btc_now * 100
            else:
                dist_pct = (liq - btc_now) / btc_now * 100

            if 0 <= dist_pct <= LIQ_APPROACH_PCT and cp["notional"] >= 1_000_000:
                events.append({
                    "type":      "LIQ_APPROACH",
                    "display":   display,
                    "qs":        qs,
                    "coin":      cp["coin"],
                    "side":      cp["side"],
                    "notional":  cp["notional"],
                    "liq_price": round(liq, 2),
                    "dist_pct":  round(dist_pct, 2),
                    "btc":       btc_now,
                    "ts":        ts,
                    "severity":  "critica",
                })

    # 5. CONSENSUS SHIFT — compara % long/short agregado
    def _consensus(pos_map):
        tl = ts_ = 0
        for w in pos_map.values():
            for p in w["positions"].values():
                if p["side"] == "LONG":
                    tl += p["notional"]
                else:
                    ts_ += p["notional"]
        total = tl + ts_
        return (tl / total * 100) if total else 50

    if prev:
        prev_long_pct = _consensus(prev)
        curr_long_pct = _consensus(curr)
        delta = curr_long_pct - prev_long_pct
        if abs(delta) >= CONSENSUS_SHIFT_DELTA:
            direction = "→ MAIS BULLISH" if delta > 0 else "→ MAIS BEARISH"
            events.append({
                "type":           "CONSENSUS_SHIFT",
                "direction":      direction,
                "prev_long_pct":  round(prev_long_pct, 1),
                "curr_long_pct":  round(curr_long_pct, 1),
                "delta":          round(delta, 1),
                "ts":             ts,
                "btc":            btc_now,
                "severity":       "alta" if abs(delta) >= 15 else "media",
            })

    return events


def _severity(notional: float, mid: float, high: float) -> str:
    if notional >= high:
        return "critica"
    if notional >= mid:
        return "alta"
    return "media"


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

def _fmt(n: float) -> str:
    if n >= 1e9: return f"${n/1e9:.1f}B"
    if n >= 1e6: return f"${n/1e6:.1f}M"
    return f"${n/1e3:.0f}K"


def _format_spike_telegram(ev: dict) -> str:
    sev_emoji = {"critica": "🚨", "alta": "⚡", "media": "🔔"}.get(ev.get("severity","media"), "🔔")
    t = ev["type"]
    btc = f"${ev.get('btc',0):,.0f}"
    ts  = ev.get("ts","")

    if t in ("SPIKE_LONG", "SPIKE_SHORT"):
        arrow = "🟢⬆" if t == "SPIKE_LONG" else "🔴⬇"
        return (
            f"{sev_emoji} <b>SPIKE DE POSIÇÃO</b> {arrow}\n"
            f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
            f"<b>Ativo:</b> {ev['coin']} {ev['side']}\n"
            f"<b>Notional:</b> {_fmt(ev['notional'])} (+{ev['delta_pct']:.1f}%)\n"
            f"<b>Anterior:</b> {_fmt(ev['prev_notional'])}\n"
            f"<b>Entrada:</b> ${ev['entry_px']:,.2f} | BTC: {btc}\n"
            f"<b>UTC:</b> {ts}"
        )
    elif t == "NEW_POSITION":
        arrow = "🟢" if ev['side'] == "LONG" else "🔴"
        return (
            f"{sev_emoji} <b>NOVA POSIÇÃO ABERTA</b> {arrow}\n"
            f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
            f"<b>Ativo:</b> {ev['coin']} {ev['side']}\n"
            f"<b>Notional:</b> {_fmt(ev['notional'])}\n"
            f"<b>Entrada:</b> ${ev['entry_px']:,.2f} | BTC: {btc}\n"
            f"<b>UTC:</b> {ts}"
        )
    elif t == "SIDE_FLIP":
        return (
            f"{sev_emoji} <b>VIRADA DE LADO!</b> 🔄\n"
            f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
            f"<b>Ativo:</b> {ev['coin']}\n"
            f"<b>Mudança:</b> {ev['prev_side']} → {ev['side']}\n"
            f"<b>Notional novo:</b> {_fmt(ev.get('notional',0))}\n"
            f"<b>BTC:</b> {btc} | <b>UTC:</b> {ts}"
        )
    elif t == "POSITION_CLOSED":
        arrow = "⬆ fechou LONG" if ev['side'] == "LONG" else "⬇ fechou SHORT"
        return (
            f"{sev_emoji} <b>POSIÇÃO FECHADA</b> ✖\n"
            f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
            f"<b>Ativo:</b> {ev['coin']} ({arrow})\n"
            f"<b>Notional saído:</b> {_fmt(ev['notional'])}\n"
            f"<b>Entrada era:</b> ${ev['entry_px']:,.2f} | BTC: {btc}\n"
            f"<b>UTC:</b> {ts}"
        )
    elif t == "LIQ_APPROACH":
        return (
            f"🚨 <b>LIQUIDAÇÃO PRÓXIMA!</b> ⚠️\n"
            f"<b>Baleia:</b> {ev['display']} [Q={ev['qs']:.3f}]\n"
            f"<b>Ativo:</b> {ev['coin']} {ev['side']}\n"
            f"<b>Notional em risco:</b> {_fmt(ev['notional'])}\n"
            f"<b>Liq. estimada:</b> ${ev['liq_price']:,.2f} ({ev['dist_pct']:.2f}% de distância)\n"
            f"<b>BTC atual:</b> {btc} | <b>UTC:</b> {ts}"
        )
    elif t == "CONSENSUS_SHIFT":
        arrow = "📈" if ev["delta"] > 0 else "📉"
        return (
            f"{sev_emoji} <b>VIRADA DE CONSENSO</b> {arrow}\n"
            f"<b>Mudança:</b> {ev['direction']}\n"
            f"<b>Antes:</b> {ev['prev_long_pct']:.1f}% Long\n"
            f"<b>Agora:</b> {ev['curr_long_pct']:.1f}% Long\n"
            f"<b>Delta:</b> {ev['delta']:+.1f}pp | BTC: {btc}\n"
            f"<b>UTC:</b> {ev['ts']}"
        )
    return f"⚡ Evento: {t} | {ts}"


def _send_telegram(text: str) -> None:
    if not (TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    import requests as req
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    req.post(url, json={
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_spike_check() -> None:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"⚡ Spike check — {ts}")

    # carrega estado anterior (lista de addresses conhecidas)
    prev_state = _load_state()
    prev_pos   = prev_state.get("positions", {})
    addresses  = prev_state.get("addresses", [])

    # se não temos addresses ainda, tenta carregar do alerts.json
    if not addresses:
        alerts_file = DATA_DIR / "alerts.json"
        if alerts_file.exists():
            try:
                alerts = json.loads(alerts_file.read_text(encoding="utf-8"))
                if alerts:
                    addresses = [
                        {"address": w.get("address", ""),
                         "display": w["display"],
                         "quality_score": w.get("quality_score", 0.5)}
                        for w in alerts[0].get("whale_positions", [])
                        if w.get("display")
                    ]
                    # salva addresses para próximo ciclo sem precisar do alerts.json
                    log.info(f"  📋 {len(addresses)} baleias carregadas do alerts.json")
            except Exception as e:
                log.warning(f"  ⚠ Não conseguiu carregar addresses: {e}")

    if not addresses:
        log.warning("  ⚠ Nenhuma baleia conhecida. Rode run_once.py primeiro.")
        return

    # coleta posições atuais
    log.info(f"  📡 Coletando posições de {len(addresses)} baleias…")
    curr_pos, btc_now = _take_quick_snapshot(addresses)
    log.info(f"  ✅ BTC ${btc_now:,.0f} | {len(curr_pos)} baleias com dados")

    # detecta spikes
    events = detect_spikes(prev_pos, curr_pos, btc_now, ts)
    log.info(f"  🔍 {len(events)} evento(s) detectado(s)")

    # salva state atual
    _save_state({
        "timestamp": ts,
        "addresses": addresses,
        "positions": curr_pos,
    })

    if not events:
        return

    # salva spike alerts
    existing = _load_spike_alerts()
    new_alerts = [{"id": f"{ts}_{i}", **ev} for i, ev in enumerate(events)]
    _save_spike_alerts(new_alerts + existing)

    # envia Telegram — agrupa por severidade, critica primeiro
    order = {"critica": 0, "alta": 1, "media": 2}
    events.sort(key=lambda e: order.get(e.get("severity","media"), 2))

    sent = 0
    for ev in events:
        msg = _format_spike_telegram(ev)
        try:
            _send_telegram(msg)
            sent += 1
            time.sleep(0.5)   # evita flood no Telegram
        except Exception as e:
            log.warning(f"  ⚠ Telegram falhou: {e}")

    log.info(f"  📣 {sent} alertas enviados")
    for ev in events:
        log.info(f"    [{ev.get('severity','?').upper()}] {ev['type']} "
                 f"— {ev.get('display','?')} {ev.get('coin','')} {ev.get('side','')}")


if __name__ == "__main__":
    run_spike_check()
