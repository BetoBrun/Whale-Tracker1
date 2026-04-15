import json
from pathlib import Path
from config import DATA_DIR

ALERTS_FILE = DATA_DIR / "alerts.json"
MAX_ALERTS  = 72

def write_alert(snap, sig, all_pos):
    entry  = _build(snap, sig, all_pos)
    alerts = _load()
    alerts.insert(0, entry)
    ALERTS_FILE.write_text(json.dumps(alerts[:MAX_ALERTS], ensure_ascii=False, indent=2), encoding="utf-8")

def _load():
    if ALERTS_FILE.exists():
        try: return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        except: return []
    return []

def _build(snap, sig, all_pos):
    dominant   = max(sig["long_pct"], sig["short_pct"])
    strength   = "forte" if dominant>=75 else "moderado" if dominant>=68 else "fraco"
    exc_set    = {mm["display"] for mm in sig.get("excluded_mm",[])}
    btc_now    = snap["btc_price_t0"]

    whale_positions = []
    for entry in all_pos:
        if entry["display"] in exc_set: continue
        df = entry.get("positions")
        if df is None or (hasattr(df,"empty") and df.empty): continue
        rows = []
        try:
            for _, r in df.iterrows():
                ep   = float(r.get("entry_px", r.get("entry",0)))
                not_ = float(r.get("notional",0))
                upnl = float(r.get("upnl",0))
                size = float(r.get("size",0))
                lev  = float(r.get("leverage",1))
                side = str(r.get("side",""))
                coin = str(r.get("coin",""))
                upnl_pct = float(r.get("upnl_pct",(upnl/not_*100) if not_ else 0))
                if ep<=0: continue
                dist_pct = None
                if coin=="BTC" and btc_now:
                    dist_pct = ((btc_now-ep)/ep*100 if side=="LONG" else (ep-btc_now)/ep*100)
                liq_price = None
                if lev>1:
                    liq_price = round(ep*(1-0.9/lev) if side=="LONG" else ep*(1+0.9/lev),2)
                rows.append({"coin":coin,"side":side,"size":round(size,4),
                    "notional":round(not_,0),"entry_px":round(ep,2),
                    "upnl":round(upnl,0),"upnl_pct":round(upnl_pct,2),
                    "leverage":round(lev,1),"liq_price":liq_price,
                    "dist_pct":round(dist_pct,2) if dist_pct is not None else None})
        except: pass
        if not rows: continue
        total_not  = sum(r["notional"] for r in rows)
        long_not   = sum(r["notional"] for r in rows if r["side"]=="LONG")
        short_not  = sum(r["notional"] for r in rows if r["side"]=="SHORT")
        whale_positions.append({
            "display":           entry["display"],
            "address":           entry.get("address",""),   # ← campo crítico para PRO
            "quality_score":     round(float(entry.get("quality_score",0)),3),
            "consistency_score": entry.get("consistency_score",0),
            "total_notional":    round(total_not,0),
            "long_notional":     round(long_not,0),
            "short_notional":    round(short_not,0),
            "total_upnl":        round(sum(r["upnl"] for r in rows),0),
            "bias":              "LONG" if long_not>=short_not else "SHORT",
            "n_positions":       len(rows),
            "performance":       {
                "pnl_month": entry.get("pnl_month",0),
                "pnl_week":  entry.get("pnl_week",0),
                "pnl_day":   entry.get("pnl_day",0),
                "pnl_quarter": entry.get("pnl_quarter",0),
                "quarter_estimated": entry.get("quarter_estimated",True),
                "roi_month": entry.get("roi_month",0),
                "roi_week":  entry.get("roi_week",0),
                "consistency_score": entry.get("consistency_score",0),
            },
            "positions": sorted(rows, key=lambda x: x["notional"], reverse=True),
        })

    whale_positions.sort(key=lambda x: x["total_notional"], reverse=True)
    return {
        "timestamp":      snap["timestamp"],
        "signal":         sig["signal"].lower(),
        "strength":       strength,
        "dominant_pct":   round(dominant,2),
        "long_pct":       round(sig["long_pct"],2),
        "short_pct":      round(sig["short_pct"],2),
        "total_long":     round(sig["total_long"],0),
        "total_short":    round(sig["total_short"],0),
        "active_whales":  sig.get("active_whales",0),
        "btc_price":      round(btc_now,2),
        "collected":      len(all_pos),
        "excluded_mm":    len(sig.get("excluded_mm",[])),
        "sem_posicao":    max(len(all_pos)-sig.get("active_whales",0)-len(sig.get("excluded_mm",[])),0),
        "assets": [{"coin":a["coin"],"direction":a["direction"],
            "long_pct":round(a["long_pct"],1),"short_pct":round(a["short_pct"],1),
            "total_usd":round(a["total_usd"],0),"conviction":a["conviction"]}
            for a in sig.get("asset_signals",[])[:8]],
        "excluded_mm_list": [{"display":m["display"],"ratio":m["ratio"],"n_pos":m["n_pos"]}
            for m in sig.get("excluded_mm",[])[:10]],
        "whale_positions": whale_positions[:20],
        "entry_clusters":  _clusters(whale_positions, btc_now),
        "top_consistent":  _top_consistent(whale_positions),
    }

def _top_consistent(wps):
    ranked = sorted(wps, key=lambda w: w.get("consistency_score",0), reverse=True)
    result = []
    for i,w in enumerate(ranked[:15]):
        p = w.get("performance",{})
        result.append({
            "rank": i+1, "display": w["display"], "address": w.get("address",""),
            "quality_score": w["quality_score"],
            "consistency_score": w.get("consistency_score",0),
            "bias": w["bias"], "total_notional": w["total_notional"],
            "pnl_month": p.get("pnl_month",0), "pnl_week": p.get("pnl_week",0),
            "pnl_day": p.get("pnl_day",0), "pnl_quarter": p.get("pnl_quarter",0),
            "quarter_estimated": p.get("quarter_estimated",True),
            "roi_month": p.get("roi_month",0), "roi_week": p.get("roi_week",0),
            "top_coins": sorted(w.get("positions",[]),key=lambda x:x["notional"],reverse=True)[:3]
        })
    return result

def _clusters(wps, btc_now):
    from collections import defaultdict
    coin_data = defaultdict(lambda: {"long":[],"short":[]})
    for w in wps:
        qs = w.get("quality_score",0.5)
        cs = w.get("consistency_score",0)
        for p in w.get("positions",[]):
            if p["entry_px"]<=0: continue
            coin_data[p["coin"]][p["side"].lower()].append(
                {"entry":p["entry_px"],"notional":p["notional"],"qs":qs,"cs":cs,"display":w["display"]})
    clusters = []
    for coin, sides in coin_data.items():
        for side_key, positions in sides.items():
            if not positions: continue
            total_not = sum(p["notional"] for p in positions)
            if total_not < 500_000: continue
            avg_entry = sum(p["entry"]*p["notional"] for p in positions)/total_not
            avg_cs    = sum(p["cs"]*p["notional"] for p in positions)/total_not
            n         = len(positions)
            if coin=="BTC":
                dist = (btc_now-avg_entry)/avg_entry*100
                zone = "SUPORTE" if side_key=="long" and avg_entry<btc_now else "RESISTENCIA" if side_key=="short" and avg_entry>btc_now else "LONG_ZONE" if side_key=="long" else "SHORT_ZONE"
            else:
                dist = None
                zone = "LONG_ZONE" if side_key=="long" else "SHORT_ZONE"
            strength = "forte" if n>=8 and total_not>=20e6 else "moderado" if n>=4 or total_not>=5e6 else "fraco"
            clusters.append({"coin":coin,"side":side_key.upper(),"avg_entry":round(avg_entry,2),
                "n_whales":n,"total_not":round(total_not,0),"avg_cs":round(avg_cs,1),
                "zone_type":zone,"dist_pct":round(dist,2) if dist is not None else None,
                "strength":strength,"whales":[p["display"] for p in sorted(positions,key=lambda x:x["notional"],reverse=True)][:5]})
    clusters.sort(key=lambda x:(x["coin"]!="BTC",x["coin"]!="ETH",-x["total_not"]))
    return clusters
