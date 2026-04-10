#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import math

from config import DATA_DIR, LOG_DIR

ALERTS_FILE = DATA_DIR / "alerts.json"
OUT_FILE = DATA_DIR / "hl_native_insights.json"
SCHEMA_VERSION = "1.0.0"
TARGET_COINS = ["BTC", "ETH", "HYPE"]
POSITION_TIERS = [
    ("Shrimp", 0, 250_000),
    ("Fish", 250_000, 1_000_000),
    ("Dolphin", 1_000_000, 5_000_000),
    ("Apex_Predator", 5_000_000, 10_000_000),
    ("Small_Whale", 10_000_000, 25_000_000),
    ("Whale", 25_000_000, 50_000_000),
    ("Tidal_Whale", 50_000_000, 100_000_000),
    ("Leviathan", 100_000_000, float("inf")),
]
PNL_TIERS = [
    ("Giga_Rekt", float("-inf"), -10_000_000),
    ("Full_Rekt", -10_000_000, -1_000_000),
    ("Semi_Rekt", -1_000_000, -100_000),
    ("Exit_Liquidity", -100_000, 0),
    ("Humble_Earner", 0, 100_000),
    ("Grinder", 100_000, 1_000_000),
    ("Smart_Money", 1_000_000, 10_000_000),
    ("Money_Printer", 10_000_000, float("inf")),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def sign_side(size: float) -> str:
    return "LONG" if size > 0 else "SHORT"


def normalize_position_key(p: Dict[str, Any]) -> str:
    coin = str(p.get("coin") or p.get("symbol") or "").upper().strip()
    side = str(p.get("side") or sign_side(to_float(p.get("size")))).upper()
    return f"{coin}:{side}"


def pct_distance(a: float, b: float) -> float:
    if a <= 0:
        return 999999.0
    return abs(a - b) / a * 100.0


def _find_tier(value: float, tiers: List[Tuple[str, float, float]]) -> str:
    for name, mn, mx in tiers:
        if mn <= value < mx:
            return name
    return tiers[-1][0]


def load_alerts() -> List[Dict[str, Any]]:
    if not ALERTS_FILE.exists():
        return []
    try:
        data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def positions_for_coin(alert_snap: Dict[str, Any], symbol: str) -> List[Dict[str, Any]]:
    symbol = symbol.upper()
    out = []
    for w in alert_snap.get("whale_positions", []):
        for p in w.get("positions", []):
            if str(p.get("coin", "")).upper() != symbol:
                continue
            side = str(p.get("side") or sign_side(to_float(p.get("size")))).upper()
            out.append({
                "user": w.get("address", ""),
                "display": w.get("display", ""),
                "symbol": symbol,
                "position_size": to_float(p.get("size")),
                "entry_price": to_float(p.get("entry_px")),
                "mark_price": to_float(alert_snap.get("btc_price")) if symbol == "BTC" else 0.0,
                "liq_price": to_float(p.get("liq_price")),
                "leverage": to_float(p.get("leverage")),
                "margin_balance": 0.0,
                "position_value_usd": round(to_float(p.get("notional")), 2),
                "unrealized_pnl": round(to_float(p.get("upnl")), 2),
                "funding_fee": 0.0,
                "margin_mode": "unknown",
                "create_time": None,
                "update_time": None,
                "side": side,
                "quality_score": w.get("quality_score"),
                "consistency_score": w.get("consistency_score"),
            })
    out.sort(key=lambda x: x["position_value_usd"], reverse=True)
    return out


def compute_positions_by_address(alert_snap: Dict[str, Any], user_address: str) -> Dict[str, Any]:
    wallet = next((w for w in alert_snap.get("whale_positions", []) if w.get("address", "").lower() == user_address.lower()), None)
    if not wallet:
        return {
            "margin_summary": {},
            "cross_margin_summary": {},
            "cross_maintenance_margin_used": 0,
            "withdrawable": 0,
            "asset_positions": [],
        }
    asset_positions = []
    for p in wallet.get("positions", []):
        asset_positions.append({
            "type": "oneWay",
            "position": {
                "coin": str(p.get("coin", "")).upper(),
                "szi": to_float(p.get("size")),
                "leverage": {"type": "unknown", "value": to_float(p.get("leverage"))},
                "entry_px": to_float(p.get("entry_px")),
                "position_value": round(to_float(p.get("notional")), 2),
                "unrealized_pnl": round(to_float(p.get("upnl")), 2),
                "return_on_equity": 0.0,
                "max_leverage": to_float(p.get("leverage")),
                "cum_funding": {"all_time": 0.0, "since_open": 0.0, "since_change": 0.0},
            }
        })
    return {
        "margin_summary": {},
        "cross_margin_summary": {},
        "cross_maintenance_margin_used": 0,
        "withdrawable": 0,
        "asset_positions": asset_positions,
    }


def compute_whale_alerts(prev_snap: Dict[str, Any], curr_snap: Dict[str, Any], symbol: str, min_notional_usd: float = 1_000_000, increase_threshold_pct: float = 20.0) -> List[Dict[str, Any]]:
    symbol = symbol.upper()
    def index_snap(snap: Dict[str, Any]):
        idx = {}
        for w in snap.get("whale_positions", []):
            identifier = w.get("address") or w.get("display")
            for p in w.get("positions", []):
                if str(p.get("coin", "")).upper() != symbol:
                    continue
                idx[(identifier, normalize_position_key(p))] = {"wallet": w, "position": p}
        return idx
    prev_idx = index_snap(prev_snap)
    curr_idx = index_snap(curr_snap)
    alerts = []
    for key in set(prev_idx) | set(curr_idx):
        prev_item = prev_idx.get(key)
        curr_item = curr_idx.get(key)
        ident = key[0]
        if prev_item is None and curr_item is not None:
            p = curr_item["position"]
            pos_usd = to_float(p.get("notional"))
            if pos_usd >= min_notional_usd:
                alerts.append({
                    "user": curr_item["wallet"].get("address", ident),
                    "display": curr_item["wallet"].get("display", ""),
                    "symbol": symbol,
                    "position_size": to_float(p.get("size")),
                    "entry_price": to_float(p.get("entry_px")),
                    "liq_price": to_float(p.get("liq_price")),
                    "position_value_usd": round(pos_usd, 2),
                    "position_action": 1,
                    "action_label": "OPEN",
                    "create_time": curr_snap.get("timestamp"),
                })
            continue
        if prev_item is not None and curr_item is None:
            p = prev_item["position"]
            pos_usd = to_float(p.get("notional"))
            if pos_usd >= min_notional_usd:
                alerts.append({
                    "user": prev_item["wallet"].get("address", ident),
                    "display": prev_item["wallet"].get("display", ""),
                    "symbol": symbol,
                    "position_size": to_float(p.get("size")),
                    "entry_price": to_float(p.get("entry_px")),
                    "liq_price": to_float(p.get("liq_price")),
                    "position_value_usd": round(pos_usd, 2),
                    "position_action": 2,
                    "action_label": "CLOSE",
                    "create_time": curr_snap.get("timestamp"),
                })
            continue
        if prev_item and curr_item:
            prev_usd = to_float(prev_item["position"].get("notional"))
            curr_usd = to_float(curr_item["position"].get("notional"))
            if prev_usd > 0:
                delta_pct = (curr_usd - prev_usd) / prev_usd * 100.0
                if curr_usd >= min_notional_usd and delta_pct >= increase_threshold_pct:
                    alerts.append({
                        "user": curr_item["wallet"].get("address", ident),
                        "display": curr_item["wallet"].get("display", ""),
                        "symbol": symbol,
                        "position_size": to_float(curr_item["position"].get("size")),
                        "entry_price": to_float(curr_item["position"].get("entry_px")),
                        "liq_price": to_float(curr_item["position"].get("liq_price")),
                        "position_value_usd": round(curr_usd, 2),
                        "position_action": 1,
                        "action_label": "INCREASE",
                        "delta_pct": round(delta_pct, 2),
                        "create_time": curr_snap.get("timestamp"),
                    })
                elif prev_usd >= min_notional_usd and delta_pct <= -increase_threshold_pct:
                    alerts.append({
                        "user": curr_item["wallet"].get("address", ident),
                        "display": curr_item["wallet"].get("display", ""),
                        "symbol": symbol,
                        "position_size": to_float(curr_item["position"].get("size")),
                        "entry_price": to_float(curr_item["position"].get("entry_px")),
                        "liq_price": to_float(curr_item["position"].get("liq_price")),
                        "position_value_usd": round(curr_usd, 2),
                        "position_action": 2,
                        "action_label": "REDUCE",
                        "delta_pct": round(delta_pct, 2),
                        "create_time": curr_snap.get("timestamp"),
                    })
    alerts.sort(key=lambda x: x.get("position_value_usd", 0), reverse=True)
    return alerts[:200]


def compute_wallet_position_distribution(alert_snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    wallet_rows = []
    for w in alert_snap.get("whale_positions", []):
        total_long = to_float(w.get("long_notional"))
        total_short = to_float(w.get("short_notional"))
        total_pos = total_long + total_short
        total_upnl = to_float(w.get("total_upnl"))
        wallet_rows.append({
            "position_usd": total_pos,
            "long_position_usd": total_long,
            "short_position_usd": total_short,
            "unrealized_pnl": total_upnl,
            "has_position": total_pos > 0,
        })
    all_count = len(wallet_rows)
    grouped = {}
    for row in wallet_rows:
        grouped.setdefault(_find_tier(row["position_usd"], POSITION_TIERS), []).append(row)
    out = []
    for tier_name, mn, mx in POSITION_TIERS:
        rows = grouped.get(tier_name, [])
        pos_rows = [r for r in rows if r["has_position"]]
        long_usd = sum(r["long_position_usd"] for r in pos_rows)
        short_usd = sum(r["short_position_usd"] for r in pos_rows)
        total_usd = long_usd + short_usd
        profit_rows = sum(1 for r in pos_rows if r["unrealized_pnl"] > 0)
        loss_rows = sum(1 for r in pos_rows if r["unrealized_pnl"] < 0)
        bias_score = safe_div(long_usd - short_usd, total_usd, 0.0)
        if bias_score > 0.4:
            remark = "very_bullish"
        elif bias_score > 0.1:
            remark = "bullish"
        elif bias_score < -0.4:
            remark = "bearish"
        elif bias_score < -0.1:
            remark = "slightly_bearish"
        else:
            remark = "indecisive"
        out.append({
            "group_name": tier_name,
            "all_address_count": len(rows),
            "position_address_count": len(pos_rows),
            "position_address_percent": round(safe_div(len(pos_rows) * 100, max(all_count, 1)), 2),
            "bias_score": round(bias_score, 4),
            "bias_remark": remark,
            "minimum_amount": mn,
            "maximum_amount": None if math.isinf(mx) else mx,
            "long_position_usd": round(long_usd, 2),
            "short_position_usd": round(short_usd, 2),
            "long_position_usd_percent": round(safe_div(long_usd * 100, total_usd), 2),
            "short_position_usd_percent": round(safe_div(short_usd * 100, total_usd), 2),
            "position_usd": round(total_usd, 2),
            "profit_address_count": profit_rows,
            "loss_address_count": loss_rows,
            "profit_address_percent": round(safe_div(profit_rows * 100, max(len(pos_rows), 1)), 2),
            "loss_address_percent": round(safe_div(loss_rows * 100, max(len(pos_rows), 1)), 2),
        })
    return out


def compute_wallet_pnl_distribution(alert_snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    wallet_rows = []
    for w in alert_snap.get("whale_positions", []):
        total_long = to_float(w.get("long_notional"))
        total_short = to_float(w.get("short_notional"))
        total_pos = total_long + total_short
        total_upnl = to_float(w.get("total_upnl"))
        wallet_rows.append({
            "pnl_usd": total_upnl,
            "position_usd": total_pos,
            "long_position_usd": total_long,
            "short_position_usd": total_short,
            "has_position": total_pos > 0,
        })
    grouped = {}
    for row in wallet_rows:
        grouped.setdefault(_find_tier(row["pnl_usd"], PNL_TIERS), []).append(row)
    out = []
    all_count = len(wallet_rows)
    for tier_name, mn, mx in PNL_TIERS:
        rows = grouped.get(tier_name, [])
        pos_rows = [r for r in rows if r["has_position"]]
        long_usd = sum(r["long_position_usd"] for r in pos_rows)
        short_usd = sum(r["short_position_usd"] for r in pos_rows)
        total_usd = long_usd + short_usd
        profit_rows = sum(1 for r in pos_rows if r["pnl_usd"] > 0)
        loss_rows = sum(1 for r in pos_rows if r["pnl_usd"] < 0)
        bias_score = safe_div(long_usd - short_usd, total_usd, 0.0)
        if bias_score > 0.4:
            remark = "very_bullish"
        elif bias_score > 0.1:
            remark = "bullish"
        elif bias_score < -0.4:
            remark = "bearish"
        elif bias_score < -0.1:
            remark = "slightly_bearish"
        else:
            remark = "indecisive"
        out.append({
            "group_name": tier_name,
            "all_address_count": len(rows),
            "position_address_count": len(pos_rows),
            "position_address_percent": round(safe_div(len(pos_rows) * 100, max(all_count, 1)), 2),
            "bias_score": round(bias_score, 4),
            "bias_remark": remark,
            "minimum_amount": None if math.isinf(mn) else mn,
            "maximum_amount": None if math.isinf(mx) else mx,
            "long_position_usd": round(long_usd, 2),
            "short_position_usd": round(short_usd, 2),
            "long_position_usd_percent": round(safe_div(long_usd * 100, total_usd), 2),
            "short_position_usd_percent": round(safe_div(short_usd * 100, total_usd), 2),
            "position_usd": round(total_usd, 2),
            "profit_address_count": profit_rows,
            "loss_address_count": loss_rows,
            "profit_address_percent": round(safe_div(profit_rows * 100, max(len(pos_rows), 1)), 2),
            "loss_address_percent": round(safe_div(loss_rows * 100, max(len(pos_rows), 1)), 2),
        })
    return out


def compute_long_short_account_ratio_history(alerts_history: List[Dict[str, Any]], symbol: str) -> List[Dict[str, Any]]:
    symbol = symbol.upper()
    history = []
    for snap in alerts_history:
        long_count = 0
        short_count = 0
        for wallet in snap.get("whale_positions", []):
            sides = set()
            for p in wallet.get("positions", []):
                if str(p.get("coin", "")).upper() != symbol:
                    continue
                sides.add(str(p.get("side") or sign_side(to_float(p.get("size")))).upper())
            if "LONG" in sides:
                long_count += 1
            if "SHORT" in sides:
                short_count += 1
        total = long_count + short_count
        history.append({
            "time": snap.get("timestamp"),
            "global_account_long_count": long_count,
            "global_account_short_count": short_count,
            "global_account_total_count": total,
            "global_account_long_percent": round(safe_div(long_count * 100, total), 2),
            "global_account_short_percent": round(safe_div(short_count * 100, total), 2),
            "global_account_long_short_ratio": round(safe_div(long_count, max(short_count, 1)), 4),
        })
    return history


def build_trade_insight_for_coin(curr_snap: Dict[str, Any], prev_snap: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    positions = positions_for_coin(curr_snap, symbol)
    alerts = compute_whale_alerts(prev_snap, curr_snap, symbol)
    long_count = sum(1 for p in positions if to_float(p["position_size"]) > 0)
    short_count = sum(1 for p in positions if to_float(p["position_size"]) < 0)
    long_usd = sum(p["position_value_usd"] for p in positions if to_float(p["position_size"]) > 0)
    short_usd = sum(p["position_value_usd"] for p in positions if to_float(p["position_size"]) < 0)
    total_usd = long_usd + short_usd
    avg_pnl_pct = 0.0
    if positions:
        vals = []
        for p in positions:
            base = max(abs(to_float(p["position_value_usd"])), 1.0)
            vals.append(to_float(p["unrealized_pnl"]) / base * 100.0)
        avg_pnl_pct = sum(vals) / len(vals)
    near_liq = 0
    for p in positions:
        mark = to_float(p.get("mark_price"))
        liq = to_float(p.get("liq_price"))
        if mark > 0 and liq > 0 and pct_distance(mark, liq) <= 3:
            near_liq += 1
    score = 0.0
    open_or_increase_long = sum(1 for a in alerts if a["position_action"] == 1 and to_float(a["position_size"]) > 0)
    open_or_increase_short = sum(1 for a in alerts if a["position_action"] == 1 and to_float(a["position_size"]) < 0)
    score += (open_or_increase_long - open_or_increase_short) * 8
    if total_usd > 0:
        score += ((long_usd - short_usd) / total_usd) * 35
    denom = long_count + short_count
    if denom > 0:
        score += ((long_count - short_count) / denom) * 20
    if near_liq > 0:
        dominant_is_long = long_usd >= short_usd
        score += -8 if dominant_is_long else 8
    bias = "NEUTRO"
    if score >= 20:
        bias = "BULLISH"
    elif score <= -20:
        bias = "BEARISH"
    confidence = 0.0
    confidence += min(len(positions) / 20, 1.0) * 0.5
    confidence += min(len(alerts) / 10, 1.0) * 0.3
    confidence += 0.2 if total_usd > 5_000_000 else 0.0
    confidence = round(min(confidence, 1.0), 2)
    notes = []
    if open_or_increase_long > open_or_increase_short:
        notes.append("Fluxo recente das baleias favorece LONG.")
    elif open_or_increase_short > open_or_increase_long:
        notes.append("Fluxo recente das baleias favorece SHORT.")
    if long_usd > short_usd:
        notes.append("Notional agregado está inclinado para LONG.")
    elif short_usd > long_usd:
        notes.append("Notional agregado está inclinado para SHORT.")
    if near_liq > 0:
        notes.append(f"{near_liq} posição(ões) perto de liquidação, aumentando chance de aceleração.")
    if avg_pnl_pct > 0:
        notes.append("Em média, as posições monitoradas estão em lucro.")
    elif avg_pnl_pct < 0:
        notes.append("Em média, as posições monitoradas estão em perda.")
    return {
        "symbol": symbol,
        "bias": bias,
        "score": round(score, 2),
        "confidence": confidence,
        "positions_count": len(positions),
        "alerts_count": len(alerts),
        "long_accounts": long_count,
        "short_accounts": short_count,
        "long_position_usd": round(long_usd, 2),
        "short_position_usd": round(short_usd, 2),
        "avg_unrealized_pnl_pct_proxy": round(avg_pnl_pct, 2),
        "near_liq_count": near_liq,
        "notes": notes,
    }


def build_output(alerts_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    current = alerts_history[0] if alerts_history else {}
    previous = alerts_history[1] if len(alerts_history) > 1 else {"whale_positions": []}
    out = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source": "hyperliquid_native",
        "status": "ok" if alerts_history else "empty",
        "assets": {},
        "position_distribution": compute_wallet_position_distribution(current) if current else [],
        "pnl_distribution": compute_wallet_pnl_distribution(current) if current else [],
    }
    for coin in TARGET_COINS:
        out["assets"][coin] = {
            "trade_insight": build_trade_insight_for_coin(current, previous, coin) if current else {},
            "whale_alerts": compute_whale_alerts(previous, current, coin) if current else [],
            "whale_positions": positions_for_coin(current, coin) if current else [],
            "long_short_ratio_history": compute_long_short_account_ratio_history(alerts_history[:120], coin) if alerts_history else [],
        }
    return out


def main():
    alerts_history = load_alerts()
    payload = build_output(alerts_history)
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ salvo: {OUT_FILE}")


if __name__ == "__main__":
    main()
