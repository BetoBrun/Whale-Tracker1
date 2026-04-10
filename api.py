#!/usr/bin/env python3
"""
api.py
Cliente leve para a API pública da Hyperliquid.

Funções principais:
- get_leaderboard()
- get_positions(address)
- get_btc_price()
- fetch_hl_candles(coin="BTC", days=90, interval="4h")
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

INFO_URL = "https://api.hyperliquid.xyz/info"
TIMEOUT = 20

SESSION = requests.Session()
SESSION.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
})


# ============================================================
# HELPERS
# ============================================================

def _post_info(payload: Dict[str, Any], timeout: int = TIMEOUT) -> Any:
    r = SESSION.post(INFO_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def _normalize_side(size: float) -> str:
    return "LONG" if size > 0 else "SHORT"


# ============================================================
# LEADERBOARD
# ============================================================

def get_leaderboard() -> List[Dict[str, Any]]:
    """
    Busca leaderboard público da Hyperliquid.

    Retorna lista bruta da API.
    """
    try:
        data = _post_info({"type": "leaderboard"})
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[api.get_leaderboard] erro: {e}")
        return []


# ============================================================
# META / MARKET
# ============================================================

def get_meta_and_asset_ctxs() -> Dict[str, Dict[str, Any]]:
    """
    Retorna contexto de mercado por ativo a partir de metaAndAssetCtxs.
    Saída:
    {
      "BTC": {...},
      "ETH": {...}
    }
    """
    try:
        data = _post_info({"type": "metaAndAssetCtxs"})
        if not isinstance(data, list) or len(data) < 2:
            return {}

        meta = data[0] or {}
        ctxs = data[1] or []
        universe = meta.get("universe", [])

        out = {}
        for u, c in zip(universe, ctxs):
            name = u.get("name")
            if not name:
                continue
            out[name.upper()] = {
                "markPx": _safe_float(c.get("markPx")),
                "midPx": _safe_float(c.get("midPx")),
                "oraclePx": _safe_float(c.get("oraclePx")),
                "premium": _safe_float(c.get("premium")),
                "funding": _safe_float(c.get("funding")),
                "openInterest": _safe_float(c.get("openInterest")),
                "dayNtlVlm": _safe_float(c.get("dayNtlVlm")),
                "prevDayPx": _safe_float(c.get("prevDayPx")),
            }
        return out
    except Exception as e:
        print(f"[api.get_meta_and_asset_ctxs] erro: {e}")
        return {}


def get_btc_price() -> float:
    """
    Retorna preço atual de BTC a partir do contexto da Hyperliquid.
    """
    try:
        ctxs = get_meta_and_asset_ctxs()
        btc = ctxs.get("BTC", {})
        px = btc.get("markPx") or btc.get("midPx") or btc.get("oraclePx") or 0.0
        return _safe_float(px, 0.0)
    except Exception as e:
        print(f"[api.get_btc_price] erro: {e}")
        return 0.0


def get_coin_price(coin: str) -> float:
    """
    Retorna preço atual do ativo informado.
    """
    coin = coin.upper().strip()
    try:
        ctxs = get_meta_and_asset_ctxs()
        d = ctxs.get(coin, {})
        px = d.get("markPx") or d.get("midPx") or d.get("oraclePx") or 0.0
        return _safe_float(px, 0.0)
    except Exception as e:
        print(f"[api.get_coin_price] erro {coin}: {e}")
        return 0.0


# ============================================================
# USER / POSITIONS
# ============================================================

def get_clearinghouse_state(address: str) -> Dict[str, Any]:
    """
    Retorna clearinghouseState bruto do usuário.
    """
    try:
        data = _post_info({
            "type": "clearinghouseState",
            "user": address
        })
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[api.get_clearinghouse_state] erro {address}: {e}")
        return {}


def get_positions(address: str) -> pd.DataFrame:
    """
    Extrai posições abertas de um address e retorna DataFrame.

    Colunas:
    - address
    - coin
    - side
    - size
    - notional
    - entry_px
    - mark_px
    - liq_px
    - leverage
    - margin_mode
    - upnl
    - funding
    - account_value
    - total_margin_used
    - create_time
    - update_time
    """
    state = get_clearinghouse_state(address)
    if not state:
        return pd.DataFrame(columns=[
            "address", "coin", "side", "size", "notional", "entry_px", "mark_px",
            "liq_px", "leverage", "margin_mode", "upnl", "funding",
            "account_value", "total_margin_used", "create_time", "update_time"
        ])

    margin_summary = state.get("marginSummary", {}) or {}
    account_value = _safe_float(margin_summary.get("accountValue"))
    total_margin_used = _safe_float(margin_summary.get("totalMarginUsed"))

    positions = state.get("assetPositions", []) or []
    rows: List[Dict[str, Any]] = []

    for item in positions:
        pos = item.get("position", {}) if isinstance(item, dict) else {}
        coin = str(pos.get("coin", "")).upper().strip()
        if not coin:
            continue

        size = _safe_float(pos.get("szi"))
        if size == 0:
            continue

        entry_px = _safe_float(pos.get("entryPx"))
        mark_px = get_coin_price(coin)
        liq_px = _safe_float(pos.get("liquidationPx"))
        leverage_obj = pos.get("leverage", {}) or {}
        leverage = _safe_float(leverage_obj.get("value"))
        margin_mode = leverage_obj.get("type", "")
        upnl = _safe_float(pos.get("unrealizedPnl"))
        position_value = _safe_float(pos.get("positionValue"))
        if position_value <= 0 and mark_px > 0:
            position_value = abs(size) * mark_px

        funding_obj = pos.get("cumFunding", {}) or {}
        funding = _safe_float(funding_obj.get("sinceOpen"))

        rows.append({
            "address": address,
            "coin": coin,
            "side": _normalize_side(size),
            "size": size,
            "notional": round(position_value, 2),
            "entry_px": entry_px,
            "mark_px": mark_px,
            "liq_px": liq_px,
            "leverage": leverage,
            "margin_mode": margin_mode,
            "upnl": upnl,
            "funding": funding,
            "account_value": account_value,
            "total_margin_used": total_margin_used,
            "create_time": None,
            "update_time": None,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("notional", ascending=False).reset_index(drop=True)
    return df


# ============================================================
# CANDLES
# ============================================================

def fetch_hl_candles(
    coin: str = "BTC",
    days: int = 90,
    interval: str = "4h",
    sleep_s: float = 0.08,
) -> pd.DataFrame:
    """
    Baixa candles históricos da Hyperliquid.

    interval suportado:
    "1m","5m","15m","1h","4h","1d"

    Retorna DataFrame com:
    - timestamp
    - open
    - high
    - low
    - close
    - volume
    - retorno_pct
    """
    ms_map = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }
    if interval not in ms_map:
        raise ValueError(f"interval inválido: {interval}")

    coin = coin.upper().strip()
    ms_per = ms_map[interval]
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000

    rows: List[Dict[str, Any]] = []
    cur = start_ms
    chunk = 500 * ms_per

    while cur < end_ms:
        end_chunk = min(cur + chunk, end_ms)
        try:
            data = _post_info({
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": cur,
                    "endTime": end_chunk,
                }
            })
            if isinstance(data, list):
                for c in data:
                    if not isinstance(c, dict):
                        continue
                    rows.append({
                        "timestamp": pd.to_datetime(c["t"], unit="ms", utc=True),
                        "open": _safe_float(c.get("o")),
                        "high": _safe_float(c.get("h")),
                        "low": _safe_float(c.get("l")),
                        "close": _safe_float(c.get("c")),
                        "volume": _safe_float(c.get("v")),
                    })
        except Exception as e:
            print(f"[api.fetch_hl_candles] erro {coin}/{interval}: {e}")

        cur = end_chunk + 1
        time.sleep(sleep_s)

    if not rows:
        return pd.DataFrame(columns=[
            "timestamp", "open", "high", "low", "close", "volume", "retorno_pct"
        ])

    df = (
        pd.DataFrame(rows)
        .drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    df["retorno_pct"] = df["close"].pct_change() * 100
    return df


# ============================================================
# OPTIONAL HELPERS
# ============================================================

def get_supported_coins() -> List[str]:
    """
    Lista coins disponíveis via metaAndAssetCtxs.
    """
    try:
        return sorted(list(get_meta_and_asset_ctxs().keys()))
    except Exception:
        return []


def get_all_wallet_positions(addresses: List[str]) -> pd.DataFrame:
    """
    Junta posições de vários addresses.
    """
    dfs = []
    for addr in addresses:
        df = get_positions(addr)
        if not df.empty:
            dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    out = pd.concat(dfs, ignore_index=True)
    return out.sort_values("notional", ascending=False).reset_index(drop=True)
