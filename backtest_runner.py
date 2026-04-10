# ╔══════════════════════════════════════════════════════════════╗
# ║            🐋 WHALE TRACKER — MÓDULO DE BACKTEST            ║
# ╚══════════════════════════════════════════════════════════════╝

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import binomtest, chi2_contingency, fisher_exact
from tabulate import tabulate
from config import HORIZON_H


# ══════════════════════════════════════════════════════════════
# SIMULAÇÃO REALISTA
# ══════════════════════════════════════════════════════════════

def simulate_realistic(btc_df: pd.DataFrame, n: int,
                       true_accuracy: float,
                       seed: int = 42) -> pd.DataFrame:
    """
    Simulação com preços reais da Hyperliquid.
    Intensidade do sinal acoplada ao retorno absoluto da vela,
    refletindo o comportamento esperado de baleias direcionais.
    """
    np.random.seed(seed)
    avail    = len(btc_df) - 1
    n        = min(n, avail)
    abs_rets = btc_df["retorno_pct"].abs().iloc[:avail]
    ret_pct  = abs_rets.rank(pct=True).values

    rows = []
    for i in range(n):
        ret_real   = btc_df.iloc[i]["retorno_pct"]
        candle     = btc_df.iloc[i]
        up         = ret_real > 0
        conviction = ret_pct[i]
        intensity  = np.clip(62 + conviction * 30 + np.random.normal(0, 3), 55, 97)

        correct = np.random.random() < true_accuracy
        signal  = ("BULLISH" if up else "BEARISH") if correct \
             else ("BEARISH" if up else "BULLISH")

        if signal == "BULLISH":
            long_pct, short_pct = intensity, 100 - intensity
        else:
            short_pct, long_pct = intensity, 100 - intensity

        resultado = (
            "ACERTO" if (signal == "BULLISH" and ret_real > 0) or
                        (signal == "BEARISH" and ret_real < 0)
            else "ERRO"
        )

        rows.append({
            "timestamp":     candle["timestamp"],
            "btc_price_t0":  candle["open"],
            "signal":        signal,
            "long_pct":      round(long_pct,  2),
            "short_pct":     round(short_pct, 2),
            "total_long":    float(np.random.uniform(10e6, 80e6)),
            "total_short":   float(np.random.uniform(10e6, 80e6)),
            "active_whales": int(np.random.randint(5, 15)),
            "btc_price_t4":  btc_df.iloc[i + 1]["close"],
            "retorno_pct":   round(ret_real, 4),
            "resultado":     resultado,
        })

    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════
# TESTES ESTATÍSTICOS
# ══════════════════════════════════════════════════════════════

def run_tests(df_dir: pd.DataFrame,
              taxa: float, total: int, acertos: int,
              is_sim: bool = False,
              true_acc: float = None,
              label: str = "",
              verbose: bool = True) -> dict:
    """
    Três testes independentes:
      1. Binomial     — taxa global > 50%?
      2. Chi² Direcional — sinal discrimina direção BTC?
      3. Spearman     — intensidade prediz magnitude?
    """
    p_hat        = taxa / 100
    se           = np.sqrt(p_hat * (1 - p_hat) / total)
    ci_lo, ci_hi = (p_hat - 1.96*se)*100, (p_hat + 1.96*se)*100

    # 1 — Binomial
    res   = binomtest(acertos, total, p=0.5, alternative="greater")
    p_bin = res.pvalue

    # 2 — Chi² Direcional
    df_d = df_dir.copy()
    df_d["ret_dir"] = (df_d["retorno_pct"] > 0).map({True: "UP", False: "DOWN"})
    ct = pd.crosstab(df_d["signal"], df_d["ret_dir"])
    for col in ["UP", "DOWN"]:
        if col not in ct.columns:
            ct[col] = 0
    ct = ct[["UP", "DOWN"]]

    try:
        chi2_v, p_chi, _, expected = chi2_contingency(ct)
        if (expected < 5).any():
            _, p_chi  = fisher_exact(ct.values[:2, :2])
            chi2_v    = float("nan")
            test_name = "Fisher"
        else:
            test_name = "Chi²"
    except Exception:
        chi2_v, p_chi, test_name = 0.0, 1.0, "Chi²"

    bull_rows = df_d[df_d["signal"] == "BULLISH"]
    bear_rows = df_d[df_d["signal"] == "BEARISH"]
    bull_up   = (bull_rows["ret_dir"] == "UP").mean()   * 100 if len(bull_rows) else 0
    bear_down = (bear_rows["ret_dir"] == "DOWN").mean() * 100 if len(bear_rows) else 0

    # 3 — Spearman
    df_d["long_dir"] = df_d.apply(
        lambda r: r["long_pct"] if r["signal"] == "BULLISH" else -r["short_pct"],
        axis=1)
    rho, p_sp = stats.spearmanr(df_d["long_dir"],
                                df_d["retorno_pct"].fillna(0))

    ret_stats = df_d.groupby("signal")["retorno_pct"].agg(["mean","std","count"])
    df_d["dominante"] = df_d[["long_pct","short_pct"]].max(axis=1)
    intensity = {}
    for thr in [62, 65, 68, 72, 75, 80]:
        sub = df_d[df_d["dominante"] >= thr]
        if len(sub) >= 5:
            intensity[thr] = {"taxa": sub["acerto"].mean()*100, "n": len(sub)}

    valid = sum([p_bin < 0.05, p_chi < 0.05, p_sp < 0.05])

    if verbose:
        tag = f" [{label}]" if label else (" [SIMULAÇÃO]" if is_sim else " [REAL]")
        print(f"\n{'═'*65}")
        print(f"  📊 TESTES ESTATÍSTICOS{tag}")
        print(f"{'═'*65}")
        print(f"  Sinais : {total}   Acertos : {acertos}   Taxa : {taxa:.1f}%")
        print(f"  IC 95% : [{ci_lo:.1f}%, {ci_hi:.1f}%]")

        print(f"\n  🎲 1. Binomial  (H₀: acerto = 50%)")
        print(f"     p={p_bin:.4f}  " +
              ("✅ Sinal bate o random" if p_bin < 0.05 else "❌ Pode ser sorte"))

        nan_chi = isinstance(chi2_v, float) and np.isnan(chi2_v)
        chi_str = f"χ²={chi2_v:.3f}  " if not nan_chi else ""
        print(f"\n  χ²  2. {test_name} Direcional")
        print(f"     BULLISH → BTC↑ : {bull_up:5.1f}%  {'✅' if bull_up>52 else '⚠️ ' if bull_up>50 else '❌'}")
        print(f"     BEARISH → BTC↓ : {bear_down:5.1f}%  {'✅' if bear_down>52 else '⚠️ ' if bear_down>50 else '❌'}")
        print(f"     {chi_str}p={p_chi:.4f}  " +
              ("✅ Discrimina direção" if p_chi < 0.05 else "❌ Não discrimina"))

        print(f"\n  ρ   3. Spearman  (intensidade × retorno)")
        print(f"     ρ={rho:.3f}  p={p_sp:.4f}  " +
              ("✅ Convicção → retorno maior" if p_sp < 0.05 else "❌ Intensidade não prediz"))

        print(f"\n  💰 Retorno médio {HORIZON_H}h:\n")
        print(tabulate(ret_stats.round(4),
                       headers=["Sinal","Média%","Std%","N"],
                       tablefmt="rounded_outline"))

        print(f"\n  📈 Acerto por intensidade:")
        for thr, v in intensity.items():
            bar  = "█" * int(v["taxa"] / 5)
            icon = "✅" if v["taxa"]>55 else "⚠️ " if v["taxa"]>50 else "❌"
            print(f"     {icon} ≥{thr}%  {v['taxa']:5.1f}%  {bar}  (n={v['n']})")

        verdicts = {
            3: "🏆 SINAL ROBUSTO       — 3/3 testes",
            2: "🟡 SINAL PROMISSOR     — 2/3 testes",
            1: "⚠️  SINAL FRACO         — 1/3 testes",
            0: "❌ SINAL NÃO VALIDADO  — 0/3 testes",
        }
        print(f"\n  {'─'*63}")
        print(f"  VEREDICTO: {verdicts[valid]}")
        if is_sim and true_acc:
            exp    = 3 if true_acc >= 0.60 else 2 if true_acc >= 0.55 else 1
            status = "✅ consistente" if valid >= exp else f"⚠️  esperado ≥{exp}/3"
            print(f"  {status} com {true_acc*100:.0f}% de acerto real (n={total})")
        print(f"  {'─'*63}\n")

    return {
        "total": total, "acertos": acertos, "taxa": taxa,
        "ci_lo": ci_lo, "ci_hi": ci_hi,
        "p_binom": p_bin, "p_chi2": p_chi,
        "chi2_val": chi2_v, "test_name": test_name,
        "bull_up": bull_up, "bear_down": bear_down,
        "rho": rho, "p_spear": p_sp,
        "ret_stats": ret_stats, "intensity": intensity,
        "valid_count": valid, "df_dir": df_d,
    }
