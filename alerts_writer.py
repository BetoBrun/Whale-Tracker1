#!/usr/bin/env python3
"""
alerts_writer.py

Escreve snapshots consolidados em data/alerts.json
no formato esperado pelo frontend e pelo tracker.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ALERTS_FILE = DATA_DIR / "alerts.json"
SCHEMA_VERSION = "2.0.0"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def _safe_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _load_existing_alerts() -> List[Dict[str, Any]]:
    if not ALERTS_FILE.exists():
        return []
    try:
        data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _atomic_write_alerts(alerts: List[Dict[str, Any]]) -> None:
    tmp = ALERTS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(alerts, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    tmp.replace(ALERTS_FILE)


def write_alert(payload: Dict[str, Any], max_items: int = 500) -> Dict[str, Any]:
    """
    Compatível com tracker.py

    Espera um payload já consolidado pelo tracker e grava no topo de alerts.json.

    Campos aceitos de entrada:
      timestamp, signal, strength, dominant_pct, long_pct, short_pct,
      total_long, total_short, active_whales, btc_price,
      assets, whale_positions, entry_clusters, top_consistent,
      metadata, excluded_mm, sem_posicao

    Importante:
    - preserva 'address' dentro de whale_positions
    - adiciona schema_version
    """
    ts = payload.get("timestamp") or _utc_now_iso()

    whale_positions = []
    for w in _safe_list(payload.get("whale_positions")):
        whale_positions.append({
            "display": w.get("display", ""),
            "address": w.get("address", ""),
            "quality_score": round(_to_float(w.get("quality_score")), 3),
            "consistency_score": round(_to_float(w.get("consistency_score")), 2),
            "bias": w.get("bias", ""),
            "total_notional": round(_to_float(w.get("total_notional")), 2),
            "total_upnl": round(_to_float(w.get("total_upnl")), 2),
            "positions": _safe_list(w.get("positions")),
        })

    top_consistent = []
    for w in _safe_list(payload.get("top_consistent")):
        top_consistent.append({
            "display": w.get("display", ""),
            "address": w.get("address", ""),
            "quality_score": round(_to_float(w.get("quality_score")), 3),
            "consistency_score": round(_to_float(w.get("consistency_score")), 2),
            "total_notional": round(_to_float(w.get("total_notional")), 2),
            "bias": w.get("bias", ""),
        })

    alert = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": ts,
        "signal": payload.get("signal", "NEUTRO"),
        "strength": payload.get("strength", "fraco"),
        "dominant_pct": round(_to_float(payload.get("dominant_pct")), 2),
        "long_pct": round(_to_float(payload.get("long_pct")), 2),
        "short_pct": round(_to_float(payload.get("short_pct")), 2),
        "total_long": round(_to_float(payload.get("total_long")), 2),
        "total_short": round(_to_float(payload.get("total_short")), 2),
        "active_whales": int(_to_float(payload.get("active_whales"), 0)),
        "btc_price": round(_to_float(payload.get("btc_price")), 2),
        "assets": _safe_list(payload.get("assets")),
        "whale_positions": whale_positions,
        "entry_clusters": _safe_list(payload.get("entry_clusters")),
        "top_consistent": top_consistent,
        "excluded_mm": int(_to_float(payload.get("excluded_mm"), 0)),
        "sem_posicao": int(_to_float(payload.get("sem_posicao"), 0)),
        "metadata": payload.get("metadata", {}),
    }

    existing = _load_existing_alerts()

    # deduplicação simples por timestamp
    existing = [a for a in existing if a.get("timestamp") != ts]
    alerts = [alert] + existing
    alerts = alerts[:max_items]

    _atomic_write_alerts(alerts)
    return alert
