import json
from pathlib import Path
from config import DATA_DIR

ALERTS_FILE = DATA_DIR / "alerts.json"
MAX_ALERTS  = 72  # mantém as últimas 72 entradas (~3 dias de hora em hora)


def write_alert(snap: dict, sig: dict, all_pos: list) -> None:
    """
    Grava / atualiza data/alerts.json com o snapshot mais recente no topo.
    Chamado de tracker.py logo após save_snapshot().
    """
    entry = _build_entry(snap, sig, all_pos)

    alerts = _load()
    alerts.insert(0, entry)
    alerts = alerts[:MAX_ALERTS]

    ALERTS_FILE.write_text(
        json.dumps(alerts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load() -> list:
    if ALERTS_FILE.exists():
        try:
            return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _build_entry(snap: dict, sig: dict, all_pos: list) -> dict:
    dominant = max(sig["long_pct"], sig["short_pct"])

    if dominant >= 75:
        strength = "forte"
    elif dominant >= 68:
        strength = "moderado"
    else:
        strength = "fraco"

    # contagem de baleias coletadas vs direcionais vs MM
    total_collected = len(all_pos)
    excluded_mm     = sig.get("excluded_mm", [])
    active          = sig.get("active_whales", 0)
    sem_posicao     = total_collected - active - len(excluded_mm)

    return {
        "timestamp":      snap["timestamp"],
        "signal":         sig["signal"].lower(),
        "strength":       strength,
        "dominant_pct":   round(dominant, 2),
        "long_pct":       round(sig["long_pct"], 2),
        "short_pct":      round(sig["short_pct"], 2),
        "total_long":     round(sig["total_long"], 0),
        "total_short":    round(sig["total_short"], 0),
        "active_whales":  active,
        "btc_price":      round(snap["btc_price_t0"], 2),
        "collected":      total_collected,
        "excluded_mm":    len(excluded_mm),
        "sem_posicao":    max(sem_posicao, 0),
        "assets": [
            {
                "coin":      a["coin"],
                "direction": a["direction"],
                "long_pct":  round(a["long_pct"], 1),
                "short_pct": round(a["short_pct"], 1),
                "total_usd": round(a["total_usd"], 0),
                "conviction": a["conviction"],
            }
            for a in sig.get("asset_signals", [])[:8]
        ],
        "excluded_mm_list": [
            {
                "display": m["display"],
                "ratio":   m["ratio"],
                "n_pos":   m["n_pos"],
            }
            for m in excluded_mm[:10]
        ],
    }
