#!/usr/bin/env python3
"""
market_context.py v2.0 — Contexto de mercado robusto
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_DIR, LOG_DIR, SCHEMA_VERSION, INFO_URL

# ═══════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════════════════════════

log_file = LOG_DIR / f"market_context_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("market_context_v2")

OUT_FILE = DATA_DIR / "market_context.json"
HIST_FILE = DATA_DIR / "market_context_history.json"
MAX_HIST = 200

@dataclass
class MarketContext:
    schema_version: str
    timestamp: str
    asset: str
    bias: str
    score: float
    confidence: str
    scores: Dict[str, float]
    metrics: Dict
    drivers: List[Dict]
    status: str
    _health: Dict

# ═══════════════════════════════════════════════════════════════
# CLIENTES
# ═══════════════════════════════════════════════════════════════

class HyperliquidClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def post(self, payload: Dict, timeout: int = 20) -> Optional[Dict]:
        try:
            r = self.session.post(INFO_URL, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f"API error: {e}")
            return None
    
    def get_asset_ctx(self) -> Optional[Dict]:
        data = self.post({"type": "metaAndAssetCtxs"})
        if not data or len(data) < 2:
            return None
        
        meta, ctxs = data[0], data[1]
        universe = meta.get("universe", [])
        
        result = {}
        for u, c in zip(universe, ctxs):
            name = u.get("name")
            if name:
                result[name] = {
                    "mark_px": float(c.get("markPx", 0) or 0),
                    "funding": float(c.get("funding", 0) or 0),
                    "open_interest": float(c.get("openInterest", 0) or 0),
                    "prev_day_px": float(c.get("prevDayPx", 0) or 0),
                }
        return result

class AlertsLoader:
    @staticmethod
    def load_latest() -> Optional[Dict]:
        alerts_file = DATA_DIR / "alerts.json"
        if not alerts_file.exists():
            log.warning("alerts.json not found")
            return None
        
        try:
            data = json.loads(alerts_file.read_text(encoding="utf-8"))
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
        except Exception as e:
            log.error(f"Failed to parse alerts.json: {e}")
        
        return None

# ═══════════════════════════════════════════════════════════════
# CÁLCULOS
# ═══════════════════════════════════════════════════════════════

def calculate_scores(whale_data: Optional[Dict], btc_ctx: Optional[Dict]) -> Dict:
    scores = {
        "whales": 0.0,
        "funding": 0.0,
        "price_action": 0.0,
        "basis": 0.0,
        "liq_pressure": 0.0,
    }
    
    if whale_data:
        long_pct = float(whale_data.get("long_pct", 50))
        scores["whales"] = round((long_pct - 50) / 5, 2)
    
    if btc_ctx:
        funding = float(btc_ctx.get("funding", 0))
        scores["funding"] = round(-funding * 10000, 2)
        
        mark = btc_ctx.get("mark_px", 0)
        prev = btc_ctx.get("prev_day_px", mark)
        if prev and mark:
            day_ret = (mark - prev) / prev * 100
            scores["price_action"] = round(day_ret / 2, 2)
    
    return scores

def determine_bias(total_score: float) -> tuple:
    if total_score >= 10:
        return "BULLISH_FORTE", "alta"
    elif total_score >= 5:
        return "BULLISH", "media"
    elif total_score <= -10:
        return "BEARISH_FORTE", "alta"
    elif total_score <= -5:
        return "BEARISH", "media"
    else:
        return "NEUTRO", "baixa"

# ═══════════════════════════════════════════════════════════════
# PERSISTÊNCIA
# ═══════════════════════════════════════════════════════════════

def atomic_write_json(filepath: Path, data: dict) -> bool:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    temp = filepath.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(filepath)
        return True
    except Exception as e:
        log.error(f"Failed to write {filepath}: {e}")
        if temp.exists():
            temp.unlink()
        return False

def save_context(context: MarketContext):
    atomic_write_json(OUT_FILE, asdict(context))
    
    # Histórico
    try:
        hist = []
        if HIST_FILE.exists():
            hist = json.loads(HIST_FILE.read_text(encoding="utf-8"))
        
        hist.insert(0, asdict(context))
        hist = hist[:MAX_HIST]
        atomic_write_json(HIST_FILE, hist)
    except Exception as e:
        log.error(f"Failed to save history: {e}")

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def build_context() -> MarketContext:
    ts = datetime.utcnow().isoformat()
    
    hl_client = HyperliquidClient()
    btc_ctx_data = hl_client.get_asset_ctx()
    whale_data = AlertsLoader.load_latest()
    
    btc_ctx = btc_ctx_data.get("BTC", {}) if btc_ctx_data else {}
    
    scores = calculate_scores(whale_data, btc_ctx)
    total_score = round(sum(scores.values()), 2)
    bias, confidence = determine_bias(total_score)
    
    metrics = {
        "mark_price": btc_ctx.get("mark_px", 0),
        "open_interest": btc_ctx.get("open_interest", 0),
        "funding_rate": btc_ctx.get("funding", 0),
        "day_return_pct": 0
    }
    
    if btc_ctx.get("prev_day_px"):
        metrics["day_return_pct"] = round(
            (btc_ctx.get("mark_px", 0) - btc_ctx["prev_day_px"]) / btc_ctx["prev_day_px"] * 100, 3
        )
    
    if whale_data:
        metrics["whales_long_pct"] = round(whale_data.get("long_pct", 0), 2)
        metrics["whales_short_pct"] = round(whale_data.get("short_pct", 0), 2)
        metrics["active_whales"] = whale_data.get("active_whales", 0)
    
    drivers = []
    if whale_data:
        drivers.append({
            "label": "Whale consensus",
            "value": round(whale_data.get("long_pct", 50) - whale_data.get("short_pct", 50), 2),
            "direction": 1 if whale_data.get("long_pct", 50) > 50 else -1
        })
    if btc_ctx:
        drivers.append({
            "label": "Funding",
            "value": round(btc_ctx.get("funding", 0) * 100, 4),
            "direction": -1 if btc_ctx.get("funding", 0) > 0 else 1
        })
    
    status = "ok" if (btc_ctx or whale_data) else "degraded"
    
    health = {
        "data_sources": [],
        "stale_seconds": 0,
        "fallback_used": False
    }
    if btc_ctx:
        health["data_sources"].append("hyperliquid:ok")
    else:
        health["data_sources"].append("hyperliquid:fail")
    if whale_data:
        health["data_sources"].append("alerts:ok")
    else:
        health["data_sources"].append("alerts:fail")
    
    return MarketContext(
        schema_version=SCHEMA_VERSION,
        timestamp=ts,
        asset="BTC",
        bias=bias,
        score=total_score,
        confidence=confidence,
        scores=scores,
        metrics=metrics,
        drivers=drivers,
        status=status,
        _health=health
    )

def run_market_context():
    log.info(f"{'═' * 60}")
    log.info("📊 Market Context v2.0")
    log.info(f"{'═' * 60}")
    
    context = build_context()
    save_context(context)
    
    log.info(f"Context: {context.bias} (score: {context.score})")
    log.info("✅ Complete")
    
    return context.status == "ok"

if __name__ == "__main__":
    success = run_market_context()
    sys.exit(0 if success else 1)
