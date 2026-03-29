# ╔══════════════════════════════════════════════════════════════╗
# ║ 🐋 WHALE TRACKER — MÓDULO DE API                           ║
# ╚══════════════════════════════════════════════════════════════╝

import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from config import (
    INFO_URL, LEADERBOARD_URL,
    MIN_PNL_ALL_TIME, MIN_NOTIONAL_POS,
    BTC_FAMILY, ETH_FAMILY, TOP_N,
)

# ══════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ══════════════════════════════════════════════════════════════

def _post(payload: dict, retries: int = 3) -> dict:
    headers = {"Content-Type": "application/json"}
    for attempt in range(retries):
        try:
            r = requests.post(INFO_URL, json=payload, headers=headers, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))

def _get_raw_leaderboard() -> list:
    r = requests.get(LEADERBOARD_URL, timeout=30)
    r.raise_for_status()
    data = r.json()
    rows = data.get("leaderboardRows")
    if rows is None:
        for key in ("leaderboard", "data", "result"):
            rows = data.get(key)
            if rows is not None:
                break
    if rows is None:
        raise ValueError(f"Chave de leaderboard não encontrada. Keys: {list(data.keys())}")
    return rows

def _f(d: dict, k: str) -> float:
    try:
        return float(d.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0

def _parse_entry(entry: dict) -> dict | None:
    if not isinstance(entry, dict):
        return None
    addr = entry.get("ethAddress", "")
    if not addr or len(addr) < 10:
        return None

    wp_map: dict[str, dict] = {}
    for item in entry.get("windowPerformances", []):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            name, perf = item
            if isinstance(perf, dict):
                wp_map[name] = perf

    pnl_all     = _f(wp_map.get("allTime", {}), "pnl")
    pnl_month   = _f(wp_map.get("month",   {}), "pnl")
    pnl_week    = _f(wp_map.get("week",    {}), "pnl")
    pnl_day     = _f(wp_map.get("day",     {}), "pnl")
    vol_day     = _f(wp_map.get("day",     {}), "vlm")
    roi_all     = _f(wp_map.get("allTime", {}), "roi")
    roi_month   = _f(wp_map.get("month",   {}), "roi")
    roi_week    = _f(wp_map.get("week",    {}), "roi")

    # "quarter" aparece no leaderboard da HL quando disponível
    # senão estimamos como 3× pnl_month (proxy conservador)
    pnl_quarter_raw = _f(wp_map.get("quarter", {}), "pnl")
    roi_quarter     = _f(wp_map.get("quarter", {}), "roi")
    if pnl_quarter_raw == 0 and pnl_month != 0:
        pnl_quarter_raw = pnl_month * 3  # proxy — marcado abaixo como estimado
        quarter_estimated = True
    else:
        quarter_estimated = False

    # ── Score de consistência (0–100) ──────────────────────────────────────
    # Pontuação baseada em quantos períodos a baleia foi lucrativa e
    # quanto ela ganhou proporcionalmente em cada janela.
    # Objetivo: identificar traders que lucram de forma CONSISTENTE,
    # não só picos isolados.
    score = 0.0
    # 1. Lucratividade por período (0 ou 1 cada)
    score += 25 if pnl_month   > 0 else 0   # lucrou no mês
    score += 15 if pnl_week    > 0 else 0   # lucrou na semana
    score += 10 if pnl_day     > 0 else 0   # lucrou hoje
    # 2. ROI mensal positivo e expressivo
    if roi_month > 0.10:   score += 20
    elif roi_month > 0.05: score += 12
    elif roi_month > 0:    score +=  5
    # 3. ROI semanal
    if roi_week > 0.05:    score += 15
    elif roi_week > 0.02:  score +=  8
    elif roi_week > 0:     score +=  4
    # 4. PnL trimestral positivo
    if pnl_quarter_raw > 0:
        score += 15
    # cap em 100
    consistency_score = min(round(score, 1), 100.0)

    try:
        acct_val = float(entry.get("accountValue") or 0)
    except (TypeError, ValueError):
        acct_val = 0.0

    display = entry.get("displayName") or (addr[:8] + "…")

    return {
        "address":            addr,
        "display":            display,
        "pnl_all":            pnl_all,
        "pnl_month":          pnl_month,
        "pnl_week":           pnl_week,
        "pnl_day":            pnl_day,
        "pnl_quarter":        round(pnl_quarter_raw, 0),
        "quarter_estimated":  quarter_estimated,
        "roi_all":            roi_all,
        "roi_month":          round(roi_month, 4),
        "roi_week":           round(roi_week, 4),
        "roi_quarter":        round(roi_quarter, 4),
        "vol_day":            vol_day,
        "acct_val":           acct_val,
        "consistency_score":  consistency_score,
    }

def _quality_score(df: pd.DataFrame) -> pd.Series:
    def rk(s): return s.rank(pct=True, ascending=True).fillna(0.5)
    return (0.35 * rk(df["pnl_all"])   +
            0.35 * rk(df["pnl_month"]) +
            0.20 * rk(df["roi_all"])   +
            0.10 * rk(df["vol_day"]))

def _normalize_coin(coin: str) -> str:
    if coin in BTC_FAMILY: return "BTC"
    if coin in ETH_FAMILY: return "ETH"
    return coin

# ══════════════════════════════════════════════════════════════
# FUNÇÕES PÚBLICAS
# ══════════════════════════════════════════════════════════════

def get_leaderboard(top_n: int = TOP_N) -> pd.DataFrame:
    print(" 📥 Baixando leaderboard…", end=" ", flush=True)
    raw = _get_raw_leaderboard()
    print(f"✅ {len(raw):,} traders")

    parsed = []
    for entry in raw:
        p = _parse_entry(entry)
        if p and p["pnl_all"] >= MIN_PNL_ALL_TIME:
            parsed.append(p)

    if not parsed:
        parsed = [p for p in (_parse_entry(e) for e in raw) if p]

    print(f" 🔍 Após filtro PnL ≥ ${MIN_PNL_ALL_TIME:,.0f}: {len(parsed)} baleias")

    df = pd.DataFrame(parsed)
    df["quality_score"] = _quality_score(df)
    return (df.sort_values("quality_score", ascending=False)
              .drop_duplicates("address")
              .head(top_n)
              .reset_index(drop=True))

def get_positions(address: str) -> pd.DataFrame:
    data = _post({"type": "clearinghouseState", "user": address})
    rows = []
    for pos in data.get("assetPositions", []):
        p = pos.get("position", {})
        size     = float(p.get("szi", 0) or 0)
        entry_px = float(p.get("entryPx", 0) or 0)
        upnl     = float(p.get("unrealizedPnl", 0) or 0)
        lev      = float((p.get("leverage") or {}).get("value", 1) or 1)

        if size == 0 or entry_px <= 0:
            continue
        notional = abs(size) * entry_px
        if notional < MIN_NOTIONAL_POS:
            continue

        rows.append({
            "coin":     _normalize_coin(p.get("coin", "")),
            "side":     "LONG" if size > 0 else "SHORT",
            "size":     abs(size),
            "entry_px": entry_px,
            "notional": notional,
            "upnl":     upnl,
            "leverage": lev,
            "upnl_pct": (upnl / notional * 100) if notional > 0 else 0,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = (df.groupby(["coin", "side"])
                .agg(notional=("notional", "sum"),
                     size=("size", "sum"),
                     entry_px=("entry_px", "mean"),
                     upnl=("upnl", "sum"),
                     leverage=("leverage", "mean"))
                .reset_index()
                .assign(upnl_pct=lambda d: d["upnl"] / d["notional"] * 100))
    return df

def get_btc_price() -> float:
    try:
        data = _post({"type": "allMids"})
        if isinstance(data, dict):
            for key in ("BTC", "UBTC", "BTC/USDC"):
                if key in data:
                    return float(data[key])
    except Exception:
        pass
    r = requests.get(
        "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
        timeout=10)
    return float(r.json()["price"])

def fetch_hl_candles(coin: str = "BTC", days: int = 90,
                     interval: str = "4h") -> pd.DataFrame:
    ms_map = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
              "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    ms_per = ms_map.get(interval, 14_400_000)
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    st_ms  = end_ms - days * 86_400_000

    print(f" 📥 Candles {coin}/{interval} Hyperliquid ({days}d)…",
          end=" ", flush=True)

    all_c, cur = [], st_ms
    chunk = 500 * ms_per
    while cur < end_ms:
        end_c = min(cur + chunk, end_ms)
        try:
            r = requests.post(
                INFO_URL,
                json={"type": "candleSnapshot",
                      "req": {"coin": coin, "interval": interval,
                              "startTime": cur, "endTime": end_c}},
                headers={"Content-Type": "application/json"},
                timeout=15)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                all_c.extend(data)
        except Exception as e:
            print(f"\n ⚠️ chunk erro: {e}")
        cur = end_c + 1
        time.sleep(0.08)

    print(f"✅ {len(all_c)} candles")
    if not all_c:
        raise ValueError(f"Nenhum candle para {coin}/{interval}")

    rows = [{"timestamp": pd.to_datetime(c["t"], unit="ms"),
             "open":  float(c.get("o", 0)),
             "high":  float(c.get("h", 0)),
             "low":   float(c.get("l", 0)),
             "close": float(c.get("c", 0)),
             "volume":float(c.get("v", 0))}
            for c in all_c if isinstance(c, dict)]

    df = (pd.DataFrame(rows)
          .drop_duplicates("timestamp")
          .sort_values("timestamp")
          .reset_index(drop=True))
    df["retorno_pct"] = df["close"].pct_change(1).shift(-1) * 100
    return df.dropna(subset=["retorno_pct"])
