#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║          🐋 WHALE TRACKER — SCRIPT PRINCIPAL                ║
# ║          Execute: python tracker.py                         ║
# ╚══════════════════════════════════════════════════════════════╝

import sys
import time
import logging
import threading
from datetime import datetime
from pathlib import Path
from tabulate import tabulate

# Setup de path para importar módulos locais
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    COLLECT_INTERVAL_H, HORIZON_H, MIN_SIGNAL_PCT,
    LOG_DIR, DATA_DIR,
)
from api import get_leaderboard, get_positions, get_btc_price, fetch_hl_candles
from whale_signal import compute_signal
from storage import load_csv, save_snapshot, enrich_snapshots, snapshot_stats
from dashboard import plot_snapshot
from telegram_notifier import notify_if_needed, telegram_configured

# ── Logging ───────────────────────────────────────────────────
log_file = LOG_DIR / f"tracker_{datetime.utcnow():%Y%m%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("whale")


# ══════════════════════════════════════════════════════════════
# COLETA DE SNAPSHOT
# ══════════════════════════════════════════════════════════════

def take_snapshot() -> tuple:
    ts    = datetime.utcnow()
    price = get_btc_price()
    lb    = get_leaderboard()

    all_pos = []
    log.info(f"  Coletando posições de {len(lb)} baleias…")
    for _, row in lb.iterrows():
        try:
            pos_df = get_positions(row["address"])
            time.sleep(0.15)
        except Exception as e:
            log.warning(f"    ⚠️  {row['display']}: {e}")
            import pandas as pd
            pos_df = pd.DataFrame()
        all_pos.append({
            "display":       row["display"],
            "address":       row["address"],
            "quality_score": row["quality_score"],
            "positions":     pos_df,
        })

    sig  = compute_signal(all_pos, lb)
    snap = {
        "timestamp":     ts.strftime("%Y-%m-%d %H:%M:%S"),
        "btc_price_t0":  price,
        "signal":        sig["signal"],
        "long_pct":      sig["long_pct"],
        "short_pct":     sig["short_pct"],
        "total_long":    sig["total_long"],
        "total_short":   sig["total_short"],
        "active_whales": sig["active_whales"],
        "btc_price_t4":  None,
        "retorno_pct":   None,
        "resultado":     None,
    }
    return snap, sig, all_pos, lb


# ══════════════════════════════════════════════════════════════
# PRINT RESUMO
# ══════════════════════════════════════════════════════════════

def print_summary(snap: dict, sig: dict, all_pos: list, lb):
    emoji    = {"BULLISH":"🟢","BEARISH":"🔴","NEUTRO":"🟡",
                "SEM_DADOS":"⚫"}.get(sig["signal"], "❓")
    dominant = max(sig["long_pct"], sig["short_pct"])

    print("\n" + "═"*65)
    print(f"  🐋 WHALE TRACKER  |  {snap['timestamp']} UTC")
    print("═"*65)

    lb_show = lb[["display","pnl_all","pnl_month","quality_score"]].copy()
    lb_show.index += 1
    lb_show.columns = ["Trader","PnL All-Time","PnL 30d","Q-Score"]
    lb_show["PnL All-Time"] = lb_show["PnL All-Time"].apply(lambda x: f"${x:>12,.0f}")
    lb_show["PnL 30d"]      = lb_show["PnL 30d"].apply(lambda x: f"${x:>10,.0f}")
    lb_show["Q-Score"]      = lb_show["Q-Score"].apply(lambda x: f"{x:.3f}")
    print(f"\n🏆 TOP {len(lb)} BALEIAS\n")
    print(tabulate(lb_show, headers="keys", tablefmt="rounded_outline"))

    if sig.get("excluded_mm"):
        print(f"\n⚠️  EXCLUÍDOS (delta-neutro / market maker):")
        for mm in sig["excluded_mm"]:
            print(f"   • {mm['display']:<30}  {mm['n_pos']} posições  "
                  f"dominância {mm['ratio']:.1f}%")

    excluded_displays = {mm["display"] for mm in sig.get("excluded_mm", [])}
    print("\n📋 POSIÇÕES ABERTAS — TRADERS DIRECIONAIS\n")
    has_pos = False
    for entry in all_pos:
        if entry["positions"].empty: continue
        if entry["display"] in excluded_displays: continue
        has_pos = True
        qs = entry.get("quality_score", 0)
        print(f"  👤 {entry['display']}  [Q={qs:.3f}]")
        print(tabulate(entry["positions"], headers="keys",
                       tablefmt="simple", showindex=False, floatfmt=".2f"))
        print()
    if not has_pos:
        print("  ⚠️  Nenhuma posição direcional aberta.")

    asset_sigs = sig.get("asset_signals", [])
    if asset_sigs:
        print("\n🎯 SINAL POR ATIVO (≥ $500k)\n")
        print(f"  {'Ativo':<8} {'Dir':<7} {'Long%':>7} {'Short%':>7} "
              f"{'Total USD':>14}  Convicção")
        print(f"  {'─'*8} {'─'*7} {'─'*7} {'─'*7} {'─'*14}  {'─'*12}")
        for s in asset_sigs[:12]:
            arrow = "⬆" if s["direction"] == "LONG" else "⬇"
            print(f"  {s['coin']:<8} {arrow} {s['direction']:<5} "
                  f"{s['long_pct']:>6.1f}% {s['short_pct']:>6.1f}%  "
                  f"${s['total_usd']:>13,.0f}  {s['conviction']}")

    print("\n" + "═"*65)
    print(f"  {emoji} SINAL: {sig['signal']}")
    print(f"     Long  : {sig['long_pct']:6.2f}%  →  ${sig['total_long']:>15,.0f}")
    print(f"     Short : {sig['short_pct']:6.2f}%  →  ${sig['total_short']:>15,.0f}")
    print(f"     Ativos: {sig['active_whales']}/{len(lb)} direcionais")
    print(f"     BTC   : ${snap['btc_price_t0']:>,.2f}")

    if sig["signal"] in ("BULLISH", "BEARISH"):
        conf = (f"💪 FORTE ({dominant:.1f}%)"     if dominant >= 75 else
                f"🟡 MODERADO ({dominant:.1f}%)"   if dominant >= 68 else
                f"⚠️  FRACO ({dominant:.1f}%)")
        print(f"     Força : {conf}")

    stats = snapshot_stats()
    n     = stats["com_resultado"]
    need  = 300
    pct   = min(100, n / need * 100)
    bar   = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    eta   = max(0, need - n) * HORIZON_H / 24
    print(f"\n  📐 Amostra: [{bar}] {n}/{need} ({pct:.1f}%)")
    if stats["taxa_acerto"] > 0:
        print(f"     Taxa atual: {stats['taxa_acerto']:.1f}% acerto")
    if n < need:
        print(f"     ⏳ ETA ~{eta:.1f} dias")
    else:
        print("     ✅ Execute: python backtest.py")
    print("═"*65 + "\n")


# ══════════════════════════════════════════════════════════════
# JOB — coleta + enriquecimento + plot
# ══════════════════════════════════════════════════════════════

def run_job(save_plot: bool = True):
    log.info("━"*50)
    log.info("🐋 Iniciando coleta…")

    try:
        # Enriquece snapshots passados com preços reais da HL
        try:
            btc_df = fetch_hl_candles(coin="BTC", days=10, interval="4h")
            count  = enrich_snapshots(btc_df)
            if count:
                log.info(f"  🔄 {count} snapshot(s) enriquecidos")
        except Exception as e:
            log.warning(f"  ⚠️  Enriquecimento falhou: {e}")

        snap, sig, all_pos, lb = take_snapshot()
        save_snapshot(snap)
        print_summary(snap, sig, all_pos, lb)

        if save_plot:
            excluded = {mm["display"] for mm in sig.get("excluded_mm", [])}
            ts_str   = snap["timestamp"].replace(":", "").replace(" ", "_")
            img_path = str(DATA_DIR / f"snapshot_{ts_str}.png")
            plot_snapshot(sig, all_pos, lb, excluded, save_path=img_path)

        sent = False
        try:
            sent = notify_if_needed(snap, sig)
            if telegram_configured():
                log.info(f"  📣 Telegram: {'enviado' if sent else 'suprimido'}")
        except Exception as e:
            log.warning(f"  ⚠️  Falha no Telegram: {e}")

        log.info(f"  ✅ Sinal: {sig['signal']}  "
                 f"({sig['long_pct']:.1f}% L / {sig['short_pct']:.1f}% S)  "
                 f"BTC ${snap['btc_price_t0']:,.0f}")

    except Exception as e:
        log.error(f"  ❌ Erro no job: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════

def start_scheduler(interval_hours: int = COLLECT_INTERVAL_H,
                    run_now: bool = True):
    """
    Inicia coleta automática em background.
    Retorna a thread para controle externo.
    """
    if run_now:
        run_job()

    interval_sec = interval_hours * 3600
    log.info(f"⏰ Scheduler ativo — coleta a cada {interval_hours}h")

    def _loop():
        while True:
            time.sleep(interval_sec)
            log.info(f"⏰ Rodando coleta agendada…")
            run_job()

    t = threading.Thread(target=_loop, daemon=True, name="whale-scheduler")
    t.start()
    return t


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="🐋 Hyperliquid Whale Tracker")
    parser.add_argument("--once",      action="store_true", help="Roda um snapshot e sai")
    parser.add_argument("--interval",  type=int, default=COLLECT_INTERVAL_H,
                        help=f"Intervalo em horas (padrão: {COLLECT_INTERVAL_H})")
    parser.add_argument("--no-plot",   action="store_true", help="Não salva gráfico")
    args = parser.parse_args()

    if args.once:
        log.info("Modo: snapshot único")
        run_job(save_plot=not args.no_plot)
    else:
        log.info(f"Modo: scheduler a cada {args.interval}h")
        t = start_scheduler(interval_hours=args.interval, run_now=True)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            log.info("🛑 Interrompido pelo usuário")
