#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║          🐋 WHALE TRACKER — BACKTEST                        ║
# ║          Execute: python backtest_runner.py                 ║
# ╚══════════════════════════════════════════════════════════════╝

import sys
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).parent))

from config import CSV_FILE, HORIZON_H, DATA_DIR
from api import fetch_hl_candles
from storage import load_csv, enrich_snapshots, snapshot_stats
from backtest import run_tests, simulate_realistic
from dashboard import plot_backtest


# ══════════════════════════════════════════════════════════════
# BACKTEST REAL
# ══════════════════════════════════════════════════════════════

def run_real(show_plots: bool = True) -> dict | None:
    print(f"\n🐋 BACKTEST REAL — {datetime.utcnow():%Y-%m-%d %H:%M UTC}")

    df = load_csv()
    if df.empty:
        print("❌ Nenhum dado. Execute tracker.py primeiro.")
        return None

    btc_df = fetch_hl_candles(coin="BTC", days=90, interval="4h")
    count  = enrich_snapshots(btc_df)
    if count:
        print(f"  🔄 {count} snapshot(s) enriquecidos")
        df = load_csv()

    df_dir = df[df["signal"].isin(["BULLISH","BEARISH"]) &
                df["resultado"].notna()].copy()
    df_dir["acerto"] = (df_dir["resultado"] == "ACERTO").astype(int)

    total   = len(df_dir)
    acertos = int(df_dir["acerto"].sum())
    taxa    = acertos / total * 100 if total else 0

    if total < 30:
        print(f"\n  ⚠️  {total} sinais reais (mínimo 30 — faltam {30-total})")
        _print_progress(total)
        return None

    result = run_tests(df_dir, taxa, total, acertos)

    if show_plots:
        save_path = str(DATA_DIR / f"backtest_real_{datetime.utcnow():%Y%m%d_%H%M}.png")
        plot_backtest(df, df_dir, btc_df, result, save_path=save_path)

    return result


# ══════════════════════════════════════════════════════════════
# BACKTEST SIMULADO
# ══════════════════════════════════════════════════════════════

def run_simulated(n: int = 400, acc: float = 0.60,
                  show_plots: bool = True) -> dict:
    print(f"\n🔬 BACKTEST SIMULADO — {datetime.utcnow():%Y-%m-%d %H:%M UTC}")
    print(f"   {n} snapshots · {acc*100:.0f}% acerto · preços reais Hyperliquid\n")

    btc_df = fetch_hl_candles(coin="BTC", days=90, interval="4h")
    df     = simulate_realistic(btc_df, n, acc)
    df_dir = df[df["signal"].isin(["BULLISH","BEARISH"])].copy()
    df_dir["acerto"] = (df_dir["resultado"] == "ACERTO").astype(int)

    total   = len(df_dir)
    acertos = int(df_dir["acerto"].sum())
    taxa    = acertos / total * 100

    result = run_tests(df_dir, taxa, total, acertos, is_sim=True, true_acc=acc)

    if show_plots:
        save_path = str(DATA_DIR / f"backtest_sim_{acc*100:.0f}pct_{datetime.utcnow():%Y%m%d_%H%M}.png")
        plot_backtest(df, df_dir, btc_df, result,
                      is_sim=True, true_acc=acc, save_path=save_path)

    _print_sample_requirements(taxa, total)
    return result


# ══════════════════════════════════════════════════════════════
# COMPARAÇÃO DE CENÁRIOS
# ══════════════════════════════════════════════════════════════

def compare(n: int = 400, show_plots: bool = True):
    print(f"\n📊 COMPARAÇÃO — {datetime.utcnow():%Y-%m-%d %H:%M UTC}\n")
    btc_df   = fetch_hl_candles(coin="BTC", days=90, interval="4h")
    cenarios = [(0.55,"Conservador"), (0.60,"Base"), (0.65,"Otimista")]
    summary  = []

    for acc, nome in cenarios:
        print(f"\n{'─'*65}")
        print(f"  Cenário: {nome} ({acc*100:.0f}%)")
        print(f"{'─'*65}")
        df     = simulate_realistic(btc_df, n, acc, seed=int(acc*100))
        df_dir = df[df["signal"].isin(["BULLISH","BEARISH"])].copy()
        df_dir["acerto"] = (df_dir["resultado"] == "ACERTO").astype(int)
        total   = len(df_dir)
        acertos = int(df_dir["acerto"].sum())
        taxa    = acertos / total * 100
        r       = run_tests(df_dir, taxa, total, acertos,
                            is_sim=True, true_acc=acc, label=nome)
        summary.append({
            "Cenário":    nome,
            "Real":       f"{acc*100:.0f}%",
            "Obs.":       f"{taxa:.1f}%",
            "IC 95%":     f"[{r['ci_lo']:.1f}%,{r['ci_hi']:.1f}%]",
            "Binomial":   f"{'✅' if r['p_binom']<0.05 else '❌'} p={r['p_binom']:.3f}",
            "Chi²":       f"{'✅' if r['p_chi2']<0.05 else '❌'} p={r['p_chi2']:.3f}",
            "Bull↑":      f"{r['bull_up']:.1f}%",
            "Bear↓":      f"{r['bear_down']:.1f}%",
            "Spearman":   f"{'✅' if r['p_spear']<0.05 else '❌'} ρ={r['rho']:.3f}",
            "Válidos":    f"{r['valid_count']}/3",
        })

    print(f"\n{'═'*75}")
    print("  📋 RESUMO COMPARATIVO")
    print(f"{'═'*75}\n")
    print(tabulate(pd.DataFrame(summary), headers="keys",
                   tablefmt="rounded_outline", showindex=False))


# ══════════════════════════════════════════════════════════════
# QUICK VALIDATE
# ══════════════════════════════════════════════════════════════

def quick_validate() -> None:
    df     = load_csv()
    df_dir = df[df["signal"].isin(["BULLISH","BEARISH"]) &
                df["resultado"].notna()].copy()
    df_dir["acerto"] = (df_dir["resultado"] == "ACERTO").astype(int)

    total   = len(df_dir)
    acertos = int(df_dir["acerto"].sum())
    taxa    = acertos / total * 100 if total else 0

    print(f"\n🐋 Quick Validate — {datetime.utcnow():%Y-%m-%d %H:%M UTC}")
    stats = snapshot_stats()
    print(f"   Total snapshots : {stats['total']}")
    print(f"   Direcionais     : {stats['direcionais']}")
    print(f"   Com resultado   : {stats['com_resultado']}")
    print(f"   Taxa de acerto  : {stats['taxa_acerto']:.1f}%\n")

    if total < 10:
        print(f"  ⚠️  Amostra muito pequena ({total}/30).")
        _print_progress(total)
        return

    run_tests(df_dir, taxa, total, acertos, label="REAL")
    _print_progress(total)


# ══════════════════════════════════════════════════════════════
# SENSIBILIDADE
# ══════════════════════════════════════════════════════════════

def sensitivity():
    print(f"\n{'═'*65}")
    print("  📐 SENSIBILIDADE — Amostra Necessária por Acerto Real")
    print(f"{'═'*65}")
    print(f"  {'Acerto':<10} {'N (IC±5%)':<18} {'Dias 4h':<12} Status")
    print(f"  {'─'*10} {'─'*18} {'─'*12} {'─'*13}")
    for acc in [0.51, 0.52, 0.55, 0.58, 0.60, 0.63, 0.65, 0.70]:
        n    = int((1.96**2 * acc*(1-acc)) / 0.05**2)
        dias = n * HORIZON_H / 24
        st   = ("✅ validável" if acc >= 0.55 else
                "⚠️  marginal"  if acc >= 0.52 else "❌ ruído")
        print(f"  {acc*100:.0f}%        {n:<18} {dias:<12.0f} {st}")
    print(f"{'═'*65}\n")


def _print_progress(n: int):
    print(f"\n  📐 Progresso:")
    for t in [30, 100, 200, 300]:
        pct = min(100, n/t*100)
        bar = "█"*int(pct/5) + "░"*(20-int(pct/5))
        ok  = "✅" if n >= t else "⏳"
        print(f"     {ok} {t:>4}  [{bar}] {pct:.0f}%")
    print(f"     ⏳ Para 300: ~{max(0,300-n)*HORIZON_H/24:.1f} dias")


def _print_sample_requirements(taxa: float, total: int):
    p = taxa / 100
    n = int((1.96**2 * p*(1-p)) / 0.05**2)
    print(f"  📐 IC ±5%: {n} sinais  (~{n*HORIZON_H/24:.0f} dias)\n")


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

def main():
    stats  = snapshot_stats()
    n_real = stats["com_resultado"]

    if n_real >= 30:
        print(f"  ✅ {n_real} sinais reais → backtest real")
        run_real()
    else:
        print(f"  ℹ️  {n_real}/30 sinais — rodando simulação")
        sensitivity()
        run_simulated(n=400, acc=0.60)
        compare(n=400)
        _print_progress(n_real)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="🐋 Whale Tracker — Backtest")
    sub    = parser.add_subparsers(dest="cmd")

    sub.add_parser("real",      help="Backtest com dados reais do CSV")
    sub.add_parser("validate",  help="Validação rápida sem gráfico")
    sub.add_parser("simulate",  help="Simulação com preços reais HL")
    sub.add_parser("compare",   help="Compara 3 cenários de edge")
    sub.add_parser("sensitivity", help="Tabela de amostra necessária")

    p_sim = sub.add_parser("sim")
    p_sim.add_argument("--acc", type=float, default=0.60)
    p_sim.add_argument("--n",   type=int,   default=400)

    args = parser.parse_args()

    if args.cmd == "real":       run_real()
    elif args.cmd == "validate": quick_validate()
    elif args.cmd == "simulate": run_simulated()
    elif args.cmd == "sim":      run_simulated(n=args.n, acc=args.acc)
    elif args.cmd == "compare":  compare()
    elif args.cmd == "sensitivity": sensitivity()
    else:                        main()
