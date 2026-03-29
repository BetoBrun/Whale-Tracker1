import json
from pathlib import Path
from config import DATA_DIR

ALERTS_FILE = DATA_DIR / "alerts.json"
MAX_ALERTS  = 72   # ~3 dias de hora em hora


def write_alert(snap: dict, sig: dict, all_pos: list) -> None:
    entry  = _build_entry(snap, sig, all_pos)
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
    strength = ("forte" if dominant >= 75 else
                "moderado" if dominant >= 68 else "fraco")

    excluded_set    = {mm["display"] for mm in sig.get("excluded_mm", [])}
    total_collected = len(all_pos)
    active          = sig.get("active_whales", 0)
    excluded_mm     = sig.get("excluded_mm", [])
    sem_posicao     = max(total_collected - active - len(excluded_mm), 0)

    # ── posições individuais das baleias direcionais ──────────────────────────
    whale_positions = []
    for entry in all_pos:
        if entry["display"] in excluded_set:
            continue
        df = entry.get("positions")
        if df is None or (hasattr(df, "empty") and df.empty):
            continue

        rows = []
        try:
            for _, r in df.iterrows():
                rows.append({
                    "coin":     str(r.get("coin", "")),
                    "side":     str(r.get("side", "")),
                    "size":     float(r.get("size", 0)),
                    "notional": float(r.get("notional", 0)),
                    "entry":    float(r.get("entry_price", r.get("entry", 0))),
                    "pnl":      float(r.get("unrealized_pnl", r.get("pnl", 0))),
                    "leverage": float(r.get("leverage", 0)),
                })
        except Exception:
            pass

        if not rows:
            continue

        total_notional = sum(r["notional"] for r in rows)
        long_not  = sum(r["notional"] for r in rows if r["side"] == "LONG")
        short_not = sum(r["notional"] for r in rows if r["side"] == "SHORT")
        bias = "LONG" if long_not >= short_not else "SHORT"

        whale_positions.append({
            "display":        entry["display"],
            "quality_score":  round(float(entry.get("quality_score", 0)), 3),
            "total_notional": round(total_notional, 0),
            "long_notional":  round(long_not, 0),
            "short_notional": round(short_not, 0),
            "bias":           bias,
            "n_positions":    len(rows),
            "positions":      sorted(rows, key=lambda x: x["notional"], reverse=True),
        })

    # ordena por notional total desc
    whale_positions.sort(key=lambda x: x["total_notional"], reverse=True)

    return {
        "timestamp":       snap["timestamp"],
        "signal":          sig["signal"].lower(),
        "strength":        strength,
        "dominant_pct":    round(dominant, 2),
        "long_pct":        round(sig["long_pct"], 2),
        "short_pct":       round(sig["short_pct"], 2),
        "total_long":      round(sig["total_long"], 0),
        "total_short":     round(sig["total_short"], 0),
        "active_whales":   active,
        "btc_price":       round(snap["btc_price_t0"], 2),
        "collected":       total_collected,
        "excluded_mm":     len(excluded_mm),
        "sem_posicao":     sem_posicao,
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
            {"display": m["display"], "ratio": m["ratio"], "n_pos": m["n_pos"]}
            for m in excluded_mm[:10]
        ],
        "whale_positions": whale_positions[:20],  # top 20 baleias
    }
