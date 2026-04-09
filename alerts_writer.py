import json
from pathlib import Path
from config import DATA_DIR

ALERTS_FILE = DATA_DIR / "alerts.json"
MAX_ALERTS  = 72


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
    btc_now         = snap["btc_price_t0"]

    # ── posições individuais + métricas estratégicas + performance ──────────
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
                entry_px = float(r.get("entry_px", r.get("entry", 0)))
                notional = float(r.get("notional", 0))
                upnl     = float(r.get("upnl", r.get("unrealized_pnl", 0)))
                size     = float(r.get("size", 0))
                lev      = float(r.get("leverage", 1))
                side     = str(r.get("side", ""))
                coin     = str(r.get("coin", ""))
                upnl_pct = float(r.get("upnl_pct",
                                       (upnl / notional * 100) if notional else 0))
                if entry_px <= 0:
                    continue

                dist_pct = None
                if coin == "BTC" and btc_now:
                    dist_pct = ((btc_now - entry_px) / entry_px * 100
                                if side == "LONG"
                                else (entry_px - btc_now) / entry_px * 100)

                liq_price = None
                if lev > 1:
                    liq_price = round(
                        entry_px * (1 - 0.9 / lev) if side == "LONG"
                        else entry_px * (1 + 0.9 / lev), 2)

                rows.append({
                    "coin":      coin,
                    "side":      side,
                    "size":      round(size, 4),
                    "notional":  round(notional, 0),
                    "entry_px":  round(entry_px, 2),
                    "upnl":      round(upnl, 0),
                    "upnl_pct":  round(upnl_pct, 2),
                    "leverage":  round(lev, 1),
                    "liq_price": liq_price,
                    "dist_pct":  round(dist_pct, 2) if dist_pct is not None else None,
                })
        except Exception:
            pass

        if not rows:
            continue

        total_not  = sum(r["notional"] for r in rows)
        long_not   = sum(r["notional"] for r in rows if r["side"] == "LONG")
        short_not  = sum(r["notional"] for r in rows if r["side"] == "SHORT")
        bias       = "LONG" if long_not >= short_not else "SHORT"
        total_upnl = sum(r["upnl"] for r in rows)

        # dados de performance da baleia (vindos do leaderboard)
        perf = {
            "pnl_month":         entry.get("pnl_month", 0),
            "pnl_week":          entry.get("pnl_week", 0),
            "pnl_day":           entry.get("pnl_day", 0),
            "pnl_quarter":       entry.get("pnl_quarter", 0),
            "quarter_estimated": entry.get("quarter_estimated", True),
            "roi_month":         entry.get("roi_month", 0),
            "roi_week":          entry.get("roi_week", 0),
            "roi_quarter":       entry.get("roi_quarter", 0),
            "consistency_score": entry.get("consistency_score", 0),
        }

        whale_positions.append({
            "display":        entry["display"],
            "address":        entry.get("address", ""),
            "quality_score":  round(float(entry.get("quality_score", 0)), 3),
            "consistency_score": perf["consistency_score"],
            "total_notional": round(total_not, 0),
            "long_notional":  round(long_not, 0),
            "short_notional": round(short_not, 0),
            "total_upnl":     round(total_upnl, 0),
            "bias":           bias,
            "n_positions":    len(rows),
            "performance":    perf,
            "positions":      sorted(rows, key=lambda x: x["notional"], reverse=True),
        })

    whale_positions.sort(key=lambda x: x["total_notional"], reverse=True)

    # ── clusters de entradas ──────────────────────────────────────────────────
    entry_clusters = _compute_entry_clusters(whale_positions, btc_now)

    # ── ranking de consistência trimestral ────────────────────────────────────
    top_consistent = _rank_by_consistency(whale_positions)

    return {
        "timestamp":        snap["timestamp"],
        "signal":           sig["signal"].lower(),
        "strength":         strength,
        "dominant_pct":     round(dominant, 2),
        "long_pct":         round(sig["long_pct"], 2),
        "short_pct":        round(sig["short_pct"], 2),
        "total_long":       round(sig["total_long"], 0),
        "total_short":      round(sig["total_short"], 0),
        "active_whales":    active,
        "btc_price":        round(btc_now, 2),
        "collected":        total_collected,
        "excluded_mm":      len(excluded_mm),
        "sem_posicao":      sem_posicao,
        "assets": [
            {"coin": a["coin"], "direction": a["direction"],
             "long_pct": round(a["long_pct"], 1), "short_pct": round(a["short_pct"], 1),
             "total_usd": round(a["total_usd"], 0), "conviction": a["conviction"]}
            for a in sig.get("asset_signals", [])[:8]
        ],
        "excluded_mm_list": [
            {"display": m["display"], "ratio": m["ratio"], "n_pos": m["n_pos"]}
            for m in excluded_mm[:10]
        ],
        "whale_positions":   whale_positions[:20],
        "entry_clusters":    entry_clusters,
        "top_consistent":    top_consistent,
    }


def _rank_by_consistency(whale_positions: list) -> list:
    """
    Retorna as baleias ordenadas por consistency_score (3 meses).
    Inclui apenas as que têm posição aberta.
    """
    ranked = sorted(
        whale_positions,
        key=lambda w: w.get("consistency_score", 0),
        reverse=True
    )
    result = []
    for i, w in enumerate(ranked[:15]):
        p = w.get("performance", {})
        result.append({
            "rank":              i + 1,
            "display":           w["display"],
            "quality_score":     w["quality_score"],
            "consistency_score": w.get("consistency_score", 0),
            "bias":              w["bias"],
            "total_notional":    w["total_notional"],
            "pnl_month":         p.get("pnl_month", 0),
            "pnl_week":          p.get("pnl_week", 0),
            "pnl_day":           p.get("pnl_day", 0),
            "pnl_quarter":       p.get("pnl_quarter", 0),
            "quarter_estimated": p.get("quarter_estimated", True),
            "roi_month":         p.get("roi_month", 0),
            "roi_week":          p.get("roi_week", 0),
            "top_coins": [
                {"coin": pos["coin"], "side": pos["side"], "notional": pos["notional"]}
                for pos in sorted(w.get("positions", []),
                                  key=lambda x: x["notional"], reverse=True)[:3]
            ],
        })
    return result


def _compute_entry_clusters(whale_positions: list, btc_now: float) -> list:
    from collections import defaultdict
    coin_data = defaultdict(lambda: {"long": [], "short": []})

    for w in whale_positions:
        qs = w.get("quality_score", 0.5)
        cs = w.get("consistency_score", 0)
        for p in w.get("positions", []):
            coin  = p["coin"]
            side  = p["side"]
            entry = p["entry_px"]
            not_  = p["notional"]
            if entry <= 0:
                continue
            coin_data[coin][side.lower()].append({
                "entry":   entry,
                "notional": not_,
                "qs":      qs,
                "cs":      cs,
                "display": w["display"],
            })

    clusters = []
    for coin, sides in coin_data.items():
        for side_key, positions in sides.items():
            if not positions:
                continue
            total_not = sum(p["notional"] for p in positions)
            if total_not < 500_000:
                continue
            avg_entry = sum(p["entry"] * p["notional"] for p in positions) / total_not
            avg_qs    = sum(p["qs"] * p["notional"] for p in positions) / total_not
            avg_cs    = sum(p["cs"] * p["notional"] for p in positions) / total_not
            n_whales  = len(positions)

            if coin == "BTC":
                dist = (btc_now - avg_entry) / avg_entry * 100
                if side_key == "long":
                    zone_type = "SUPORTE" if avg_entry < btc_now else "ABOVE"
                else:
                    zone_type = "RESISTENCIA" if avg_entry > btc_now else "BELOW"
            else:
                dist      = None
                zone_type = "LONG_ZONE" if side_key == "long" else "SHORT_ZONE"

            if n_whales >= 8 and total_not >= 20_000_000:
                cluster_str = "forte"
            elif n_whales >= 4 or total_not >= 5_000_000:
                cluster_str = "moderado"
            else:
                cluster_str = "fraco"

            clusters.append({
                "coin":      coin,
                "side":      side_key.upper(),
                "avg_entry": round(avg_entry, 2),
                "n_whales":  n_whales,
                "total_not": round(total_not, 0),
                "avg_qs":    round(avg_qs, 3),
                "avg_cs":    round(avg_cs, 1),
                "zone_type": zone_type,
                "dist_pct":  round(dist, 2) if dist is not None else None,
                "strength":  cluster_str,
                "whales":    [p["display"] for p in
                              sorted(positions, key=lambda x: x["notional"], reverse=True)][:5],
            })

    clusters.sort(key=lambda x: (x["coin"] != "BTC", x["coin"] != "ETH", -x["total_not"]))
    return clusters
