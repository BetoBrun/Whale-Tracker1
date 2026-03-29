# ╔══════════════════════════════════════════════════════════════╗
# ║             🐋 WHALE TRACKER — MÓDULO DE SINAL              ║
# ╚══════════════════════════════════════════════════════════════╝

import pandas as pd
from config import (
    MIN_SIGNAL_PCT, MIN_ACTIVE_WHALES,
    MAX_POSITIONS_PER_WHALE, MIN_DIRECTIONAL_RATIO,
)


def is_directional_trader(pos_df: pd.DataFrame) -> tuple[bool, float]:
    """
    Detecta market makers / fundos delta-neutro.
    Retorna (é_direcional, ratio_dominante).
    """
    if pos_df.empty:
        return False, 0.0
    if len(pos_df) <= MAX_POSITIONS_PER_WHALE:
        return True, 1.0

    total_long  = pos_df[pos_df["side"] == "LONG"]["notional"].sum()
    total_short = pos_df[pos_df["side"] == "SHORT"]["notional"].sum()
    total       = total_long + total_short or 1
    dominant    = max(total_long, total_short) / total

    if dominant < MIN_DIRECTIONAL_RATIO:
        return False, dominant
    return True, dominant


def compute_per_asset_signals(coin_map: dict) -> list[dict]:
    """Sinal direcional por ativo com indicador de convicção."""
    signals = []
    for coin, sides in coin_map.items():
        total = sides["L"] + sides["S"]
        if total < 500_000:
            continue
        long_pct  = sides["L"] / total * 100
        short_pct = sides["S"] / total * 100
        dominant  = max(long_pct, short_pct)
        direction = "LONG" if long_pct > short_pct else "SHORT"

        conviction = ("🔥 FORTE" if dominant >= 75 else
                      "🟡 MODERADO" if dominant >= 62 else
                      "⚪ FRACO")

        signals.append({
            "coin":       coin,
            "direction":  direction,
            "long_pct":   round(long_pct,  1),
            "short_pct":  round(short_pct, 1),
            "total_usd":  total,
            "dominant":   round(dominant,  1),
            "conviction": conviction,
        })

    return sorted(signals, key=lambda x: x["total_usd"], reverse=True)


def compute_signal(all_pos: list[dict], lb: pd.DataFrame) -> dict:
    """
    Sinal agregado ponderado por quality_score.
    Exclui market makers / fundos delta-neutro automaticamente.
    """
    quality_map = dict(zip(lb["address"], lb["quality_score"]))

    total_long_w  = total_short_w  = 0.0
    total_long_n  = total_short_n  = 0.0
    coin_map: dict[str, dict]      = {}
    active_count  = 0
    excluded_mm   = []

    for entry in all_pos:
        df      = entry["positions"]
        quality = quality_map.get(entry["address"], 0.5)

        if df.empty:
            continue

        direcional, ratio = is_directional_trader(df)
        if not direcional:
            excluded_mm.append({
                "display": entry["display"],
                "ratio":   round(ratio * 100, 1),
                "n_pos":   len(df),
            })
            continue

        active_count += 1

        for _, r in df.iterrows():
            n    = r["notional"]
            coin = r["coin"]
            w    = n * quality

            if r["side"] == "LONG":
                total_long_w  += w
                total_long_n  += n
                coin_map.setdefault(coin, {"L": 0, "S": 0})["L"] += n
            else:
                total_short_w += w
                total_short_n += n
                coin_map.setdefault(coin, {"L": 0, "S": 0})["S"] += n

    total_w   = total_long_w + total_short_w or 1
    long_pct  = total_long_w  / total_w * 100
    short_pct = total_short_w / total_w * 100

    if active_count < MIN_ACTIVE_WHALES:
        signal = "SEM_DADOS"
    elif long_pct  >= MIN_SIGNAL_PCT:
        signal = "BULLISH"
    elif short_pct >= MIN_SIGNAL_PCT:
        signal = "BEARISH"
    else:
        signal = "NEUTRO"

    top_coins     = sorted(coin_map.items(),
                           key=lambda x: x[1]["L"] + x[1]["S"],
                           reverse=True)[:5]
    asset_signals = compute_per_asset_signals(coin_map)

    return {
        "signal":        signal,
        "long_pct":      round(long_pct,  3),
        "short_pct":     round(short_pct, 3),
        "total_long":    round(total_long_n,  2),
        "total_short":   round(total_short_n, 2),
        "active_whales": active_count,
        "excluded_mm":   excluded_mm,
        "coin_map":      coin_map,
        "top_coins":     top_coins,
        "asset_signals": asset_signals,
    }
