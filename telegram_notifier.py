import json
from datetime import datetime, timedelta
from pathlib import Path

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
    TELEGRAM_ONLY_DIRECTIONAL,
    TELEGRAM_DEDUP_WINDOW_H,
    STATE_FILE,
)


def telegram_configured() -> bool:
    return bool(TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _load_state() -> dict:
    if Path(STATE_FILE).exists():
        try:
            return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def should_send(snap: dict, sig: dict) -> bool:
    if not telegram_configured():
        return False
    if TELEGRAM_ONLY_DIRECTIONAL and sig["signal"] not in ("BULLISH", "BEARISH"):
        return False

    state = _load_state()
    last_signal = state.get("signal")
    last_ts_raw = state.get("timestamp")

    if not last_ts_raw:
        return True

    try:
        last_ts = datetime.fromisoformat(last_ts_raw)
        current_ts = datetime.strptime(snap["timestamp"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True

    recent = current_ts - last_ts < timedelta(hours=TELEGRAM_DEDUP_WINDOW_H)
    if recent and last_signal == sig["signal"]:
        return False
    return True


def format_message(snap: dict, sig: dict) -> str:
    emoji = {
        "BULLISH": "🟢",
        "BEARISH": "🔴",
        "NEUTRO": "🟡",
        "SEM_DADOS": "⚫",
    }.get(sig["signal"], "❓")

    dominant = max(sig["long_pct"], sig["short_pct"])
    strength = (
        "FORTE" if dominant >= 75 else
        "MODERADO" if dominant >= 68 else
        "FRACO"
    )

    lines = [
        f"{emoji} <b>Whale Tracker</b>",
        f"<b>Sinal:</b> {sig['signal']}",
        f"<b>Força:</b> {strength} ({dominant:.1f}%)",
        f"<b>BTC:</b> ${snap['btc_price_t0']:,.2f}",
        f"<b>Long:</b> {sig['long_pct']:.2f}% · ${sig['total_long']:,.0f}",
        f"<b>Short:</b> {sig['short_pct']:.2f}% · ${sig['total_short']:,.0f}",
        f"<b>Baleias direcionais:</b> {sig['active_whales']}",
        f"<b>Timestamp UTC:</b> {snap['timestamp']}",
    ]

    top_assets = sig.get("asset_signals", [])[:5]
    if top_assets:
        lines.append("")
        lines.append("<b>Top ativos:</b>")
        for a in top_assets:
            arrow = "⬆" if a["direction"] == "LONG" else "⬇"
            lines.append(
                f"• {a['coin']}: {arrow} {a['direction']} | "
                f"L {a['long_pct']:.1f}% / S {a['short_pct']:.1f}% | "
                f"${a['total_usd']:,.0f}"
            )

    excluded = sig.get("excluded_mm", [])
    if excluded:
        lines.append("")
        lines.append(f"<b>Excluídos MM/delta-neutro:</b> {len(excluded)}")

    return "\n".join(lines)


def send_message(text: str) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def notify_if_needed(snap: dict, sig: dict) -> bool:
    if not should_send(snap, sig):
        return False
    message = format_message(snap, sig)
    send_message(message)
    ts = datetime.strptime(snap["timestamp"], "%Y-%m-%d %H:%M:%S")
    _save_state({"signal": sig["signal"], "timestamp": ts.isoformat()})
    return True
