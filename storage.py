# ╔══════════════════════════════════════════════════════════════╗
# ║            🐋 WHALE TRACKER — MÓDULO DE STORAGE             ║
# ╚══════════════════════════════════════════════════════════════╝

import pandas as pd
from datetime import datetime, timedelta
from config import CSV_FILE, HORIZON_H

COLS = [
    "timestamp", "btc_price_t0", "signal", "long_pct", "short_pct",
    "total_long", "total_short", "active_whales",
    "btc_price_t4", "retorno_pct", "resultado",
]


def load_csv() -> pd.DataFrame:
    if CSV_FILE.exists():
        return pd.read_csv(CSV_FILE, parse_dates=["timestamp"])
    return pd.DataFrame(columns=COLS)


def save_snapshot(snap: dict) -> None:
    df = load_csv()
    pd.concat([df, pd.DataFrame([snap])], ignore_index=True).to_csv(CSV_FILE, index=False)


def enrich_snapshots(btc_df: pd.DataFrame) -> int:
    """
    Preenche btc_price_t4 / resultado para snapshots que já
    ultrapassaram HORIZON_H horas usando candles da Hyperliquid.
    Retorna o número de snapshots enriquecidos.
    """
    df = load_csv()
    if df.empty:
        return 0

    pending = df[
        df["btc_price_t4"].isna() &
        (pd.to_datetime(df["timestamp"]) <
         datetime.utcnow() - timedelta(hours=HORIZON_H))
    ]
    if pending.empty:
        return 0

    idx   = btc_df.set_index("timestamp").sort_index()
    count = 0

    for i in pending.index:
        ts  = pd.to_datetime(df.at[i, "timestamp"]) + timedelta(hours=HORIZON_H)
        pos = min(idx.index.searchsorted(ts), len(idx) - 1)
        p4  = idx.iloc[pos]["close"]
        p0  = df.at[i, "btc_price_t0"]
        ret = (p4 - p0) / p0 * 100

        df.at[i, "btc_price_t4"] = round(p4, 2)
        df.at[i, "retorno_pct"]  = round(ret, 4)

        sig = df.at[i, "signal"]
        if sig in ("NEUTRO", "SEM_DADOS"):
            df.at[i, "resultado"] = "NEUTRO"
        elif sig == "BULLISH" and ret > 0:
            df.at[i, "resultado"] = "ACERTO"
        elif sig == "BEARISH" and ret < 0:
            df.at[i, "resultado"] = "ACERTO"
        else:
            df.at[i, "resultado"] = "ERRO"
        count += 1

    if count:
        df.to_csv(CSV_FILE, index=False)
    return count


def snapshot_stats() -> dict:
    """Retorna estatísticas rápidas do CSV atual."""
    df = load_csv()
    if df.empty:
        return {"total": 0, "direcionais": 0, "com_resultado": 0, "acertos": 0}

    dir_df  = df[df["signal"].isin(["BULLISH", "BEARISH"])]
    res_df  = dir_df[dir_df["resultado"].notna()]
    acertos = int((res_df["resultado"] == "ACERTO").sum())

    return {
        "total":          len(df),
        "direcionais":    len(dir_df),
        "com_resultado":  len(res_df),
        "acertos":        acertos,
        "taxa_acerto":    round(acertos / len(res_df) * 100, 1) if len(res_df) else 0,
    }
