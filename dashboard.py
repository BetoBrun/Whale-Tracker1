# ╔══════════════════════════════════════════════════════════════╗
# ║            🐋 WHALE TRACKER — MÓDULO DE DASHBOARD           ║
# ╚══════════════════════════════════════════════════════════════╝

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime, timedelta
from config import HORIZON_H, BG, PANEL, GREEN, RED, GOLD, BLUE, PURPLE, WHITE


def _style(ax, title=""):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=WHITE, labelsize=8)
    ax.title.set_color(WHITE)
    ax.title.set_fontsize(10)
    ax.title.set_fontweight("bold")
    for sp in ax.spines.values():
        sp.set_edgecolor("#2a2a3a")
    if title:
        ax.set_title(title)


def _price_at(btc_df: pd.DataFrame, timestamps) -> list:
    idx = btc_df.set_index("timestamp").sort_index()
    out = []
    for ts in timestamps:
        pos = min(idx.index.searchsorted(ts), len(idx) - 1)
        out.append(idx.iloc[pos]["close"])
    return out


# ══════════════════════════════════════════════════════════════
# SNAPSHOT — gráfico de posições atuais
# ══════════════════════════════════════════════════════════════

def plot_snapshot(sig: dict, all_pos: list, lb: pd.DataFrame,
                  excluded_displays: set = None,
                  save_path: str = "whale_snapshot.png"):
    excluded_displays = excluded_displays or set()
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), facecolor=BG)

    # 1 — Donut Long vs Short
    ax = axes[0]
    _style(ax, "🐋 Long vs Short (Direcionais)")
    sizes = [sig["long_pct"], sig["short_pct"]]
    if sum(sizes) == 0: sizes = [50, 50]
    wedges, _ = ax.pie(sizes, colors=[GREEN, RED], startangle=90,
                       wedgeprops=dict(width=0.6, edgecolor=BG, linewidth=2))
    color_c = (GREEN if "BULL" in sig["signal"] else
               RED   if "BEAR" in sig["signal"] else GOLD)
    ax.text(0,  0.15, sig["signal"],
            ha="center", va="center", fontsize=11, fontweight="bold", color=color_c)
    ax.text(0, -0.20, f"{sig['active_whales']} direcionais",
            ha="center", va="center", fontsize=8, color=WHITE)
    ax.legend(wedges,
              [f"LONG {sig['long_pct']:.1f}%", f"SHORT {sig['short_pct']:.1f}%"],
              loc="lower center", facecolor=PANEL, labelcolor=WHITE,
              bbox_to_anchor=(0.5, -0.05), ncol=2)

    # 2 — Convicção por ativo
    ax = axes[1]
    _style(ax, "🎯 Convicção por Ativo")
    asset_sigs = sig.get("asset_signals", [])[:10]
    if asset_sigs:
        coins    = [s["coin"] for s in asset_sigs]
        net_pcts = [s["long_pct"] - s["short_pct"] for s in asset_sigs]
        bcolors  = [GREEN if v > 0 else RED for v in net_pcts]
        y_pos    = list(range(len(coins)))
        ax.barh(y_pos, net_pcts, color=bcolors, edgecolor=BG, alpha=0.85)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(coins, color=WHITE, fontsize=9)
        ax.axvline(0, color=WHITE, linewidth=0.8, linestyle="--", alpha=0.5)
        ax.set_xlabel("Net Long% − Short%", color=WHITE)
        for i, s in enumerate(asset_sigs):
            icon = "🔥" if s["dominant"] >= 75 else "🟡" if s["dominant"] >= 62 else "⚪"
            ax.text(net_pcts[i], i, f"  {icon}{s['dominant']:.0f}%",
                    va="center", color=WHITE, fontsize=7)
    else:
        ax.text(0.5, 0.5, "Sem dados", ha="center", va="center",
                color=WHITE, fontsize=13, transform=ax.transAxes)

    # 3 — Exposição bruta
    ax = axes[2]
    _style(ax, "📍 Exposição Bruta (MUSD)")
    cm = sig["coin_map"]
    if cm:
        coins = sorted(cm, key=lambda c: cm[c]["L"]+cm[c]["S"], reverse=True)[:10]
        x     = list(range(len(coins)))
        lv    = [cm[c]["L"] / 1e6 for c in coins]
        sv    = [-cm[c]["S"] / 1e6 for c in coins]
        ax.bar(x, lv, color=GREEN, label="LONG",  alpha=0.85, edgecolor=BG)
        ax.bar(x, sv, color=RED,   label="SHORT", alpha=0.85, edgecolor=BG)
        ax.axhline(0, color=WHITE, linewidth=0.8, linestyle="--")
        ax.set_xticks(x)
        ax.set_xticklabels(coins, rotation=45, ha="right", color=WHITE)
        ax.set_ylabel("Notional (M USD)", color=WHITE)
        ax.legend(facecolor=PANEL, labelcolor=WHITE)
    else:
        ax.text(0.5, 0.5, "Sem posições", ha="center", va="center",
                color=WHITE, fontsize=14, transform=ax.transAxes)

    n_mm = len(excluded_displays)
    mm_note = f"  ({n_mm} MMs excluídos)" if n_mm else ""
    plt.suptitle(
        f"🐋 Whale Tracker{mm_note}  |  {datetime.utcnow():%Y-%m-%d %H:%M UTC}",
        color=WHITE, fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()
    plt.savefig(save_path, dpi=140, bbox_inches="tight", facecolor=BG)
    plt.close()
    print(f"  📊 Gráfico salvo: {save_path}")


# ══════════════════════════════════════════════════════════════
# BACKTEST — dashboard completo
# ══════════════════════════════════════════════════════════════

def plot_backtest(df: pd.DataFrame, df_dir: pd.DataFrame,
                  btc_df: pd.DataFrame, result: dict,
                  is_sim: bool = False, true_acc: float = None,
                  save_path: str = "whale_backtest.png"):

    fig = plt.figure(figsize=(22, 20), facecolor=BG)
    gs  = gridspec.GridSpec(4, 3, figure=fig, hspace=0.55, wspace=0.38)
    tag = " [SIMULAÇÃO]" if is_sim else " [REAL]"

    # 1 — Acerto × Intensidade
    ax = fig.add_subplot(gs[0, 0])
    _style(ax, f"🎯 Acerto × Intensidade{tag}")
    it = result.get("intensity", {})
    if it:
        thrs  = list(it.keys())
        taxas = [it[t]["taxa"] for t in thrs]
        ns    = [it[t]["n"]    for t in thrs]
        cols  = [GREEN if t > 55 else GOLD if t > 50 else RED for t in taxas]
        bars  = ax.bar([f"≥{t}%" for t in thrs], taxas,
                       color=cols, edgecolor=BG, width=0.6)
        for bar, n in zip(bars, ns):
            ax.text(bar.get_x() + bar.get_width()/2,
                    bar.get_height() + 0.8,
                    f"n={n}", ha="center", color=WHITE, fontsize=7)
        ax.axhline(50, color=RED,  linestyle="--", alpha=0.6, label="50%")
        ax.axhline(55, color=GOLD, linestyle=":",  alpha=0.5, label="55%")
        ax.set_ylim(0, 100)
        ax.set_ylabel("% Acerto", color=WHITE)
        ax.legend(facecolor=PANEL, labelcolor=WHITE, fontsize=7)

    # 2 — P-values
    ax = fig.add_subplot(gs[0, 1])
    _style(ax, "🔬 Significância dos 3 Testes")
    tnames = ["Binomial", f"{result['test_name']}\nDirecional", "Spearman ρ"]
    pvals  = [result["p_binom"], result["p_chi2"], abs(result["p_spear"])]
    pcols  = [GREEN if p < 0.05 else RED for p in pvals]
    ax.barh(tnames, pvals, color=pcols, edgecolor=BG, height=0.45)
    ax.axvline(0.05, color=GOLD, linestyle="--", linewidth=1.5, label="α=0.05")
    ax.set_xlabel("p-value", color=WHITE)
    ax.legend(facecolor=PANEL, labelcolor=WHITE, fontsize=8)
    vc = result["valid_count"]
    ax.text(0.97, 0.05, f"{vc}/3 válidos",
            transform=ax.transAxes, ha="right", va="bottom",
            color=GREEN if vc == 3 else GOLD if vc >= 2 else RED,
            fontsize=12, fontweight="bold")

    # 3 — Chi² Direcional visualizado
    ax = fig.add_subplot(gs[0, 2])
    b_up = result["bull_up"]
    bd   = result["bear_down"]
    col2 = [GREEN if v > 52 else GOLD if v > 50 else RED for v in [b_up, bd]]
    bars2 = ax.bar(["BULLISH\n→ BTC↑", "BEARISH\n→ BTC↓"],
                   [b_up, bd], color=col2, edgecolor=BG, width=0.5)
    for bar, v in zip(bars2, [b_up, bd]):
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + 0.5,
                f"{v:.1f}%", ha="center", color=WHITE,
                fontsize=10, fontweight="bold")
    ax.axhline(50, color=RED,  linestyle="--", alpha=0.6, label="50%")
    ax.axhline(55, color=GOLD, linestyle=":",  alpha=0.5, label="55%")
    ax.set_ylim(0, 100)
    ax.set_ylabel("% BTC confirmou", color=WHITE)
    ax.legend(facecolor=PANEL, labelcolor=WHITE, fontsize=7)
    p_txt = f"p={result['p_chi2']:.4f} {'✅' if result['p_chi2']<0.05 else '❌'}"
    _style(ax, f"📊 Chi² Direcional ({p_txt})")

    # 4 — Histórico BTC + sinais
    ax = fig.add_subplot(gs[1, :])
    _style(ax, f"📈 BTC (Hyperliquid) + Sinais das Baleias{tag}")
    ax.plot(btc_df["timestamp"], btc_df["close"],
            color=GOLD, linewidth=1.0, label="BTC/USD", zorder=2, alpha=0.9)
    df_plot = df.copy()
    df_plot["timestamp"] = pd.to_datetime(df_plot["timestamp"])
    for _, row in df_plot.sort_values("timestamp").iterrows():
        c = {"BULLISH": GREEN, "BEARISH": RED, "NEUTRO": GOLD}.get(row["signal"], "grey")
        ax.axvspan(row["timestamp"],
                   row["timestamp"] + timedelta(hours=HORIZON_H),
                   alpha=0.05, color=c, zorder=1)
    ac = df_plot[df_plot["resultado"] == "ACERTO"]
    er = df_plot[df_plot["resultado"] == "ERRO"]
    if not ac.empty:
        ax.scatter(ac["timestamp"], _price_at(btc_df, ac["timestamp"]),
                   color=GREEN, s=25, marker="^", zorder=5, label="Acerto", alpha=0.8)
    if not er.empty:
        ax.scatter(er["timestamp"], _price_at(btc_df, er["timestamp"]),
                   color=RED, s=25, marker="v", zorder=5, label="Erro", alpha=0.8)
    ax.set_ylabel("USD", color=WHITE)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(facecolor=PANEL, labelcolor=WHITE, fontsize=8, ncol=3)

    # 5 — Distribuição de retornos
    ax = fig.add_subplot(gs[2, 0])
    _style(ax, f"📊 Distribuição Retornos {HORIZON_H}h")
    for s_name, col in [("BULLISH", GREEN), ("BEARISH", RED)]:
        sub = df_dir[df_dir["signal"] == s_name]["retorno_pct"].dropna()
        if not sub.empty:
            ax.hist(sub, bins=25, alpha=0.6, color=col, edgecolor=BG,
                    label=f"{s_name} μ={sub.mean():.2f}%")
    ax.axvline(0, color=WHITE, linewidth=1.5, linestyle="--", alpha=0.7)
    ax.set_xlabel("Retorno %", color=WHITE)
    ax.set_ylabel("Freq.", color=WHITE)
    ax.legend(facecolor=PANEL, labelcolor=WHITE, fontsize=8)

    # 6 — Retorno médio
    ax = fig.add_subplot(gs[2, 1])
    _style(ax, "💰 Retorno Médio por Sinal")
    rs = result.get("ret_stats")
    if rs is not None and not rs.empty:
        ms = rs["mean"].tolist()
        ax.bar(rs.index, ms,
               color=[GREEN if m > 0 else RED for m in ms],
               edgecolor=BG, width=0.5,
               yerr=rs["std"].tolist(), capsize=6,
               error_kw={"color": WHITE, "linewidth": 1.5})
        ax.axhline(0, color=WHITE, linewidth=0.8)
        ax.set_ylabel("Retorno médio %", color=WHITE)
        for i, (idx, row) in enumerate(rs.iterrows()):
            ax.text(i, row["mean"] + (0.05 if row["mean"] >= 0 else -0.12),
                    f"n={int(row['count'])}", ha="center", color=WHITE, fontsize=8)

    # 7 — Spearman scatter
    ax = fig.add_subplot(gs[2, 2])
    rho = result["rho"]
    _style(ax, f"📈 Intensidade × Retorno (ρ={rho:.3f})")
    sx = df_dir["long_pct"]
    sy = df_dir["retorno_pct"].fillna(0)
    ax.scatter(sx, sy, c=[GREEN if v > 0 else RED for v in sy],
               alpha=0.4, s=20, edgecolors="none")
    if len(sx) > 5:
        m, b = np.polyfit(sx, sy, 1)
        xs   = np.linspace(sx.min(), sx.max(), 100)
        ax.plot(xs, m*xs+b, color=BLUE, linewidth=2)
    ax.axhline(0, color=WHITE, linewidth=0.7, linestyle="--", alpha=0.4)
    ax.set_xlabel("Long %", color=WHITE)
    ax.set_ylabel(f"Retorno {HORIZON_H}h %", color=WHITE)

    # 8 — Taxa acumulada + IC deslizante
    ax = fig.add_subplot(gs[3, :])
    _style(ax, f"🏆 Taxa Acumulada + IC 95% Deslizante{tag}")
    df_s = df_dir.sort_values("timestamp").reset_index(drop=True)
    df_s["acum"] = df_s["acerto"].expanding().mean() * 100
    ax.plot(range(len(df_s)), df_s["acum"],
            color=BLUE, linewidth=2, label="Acumulado", zorder=3)
    ax.fill_between(range(len(df_s)), df_s["acum"], 50,
                    where=df_s["acum"] > 50, alpha=0.10, color=GREEN)
    ax.fill_between(range(len(df_s)), df_s["acum"], 50,
                    where=df_s["acum"] < 50, alpha=0.10, color=RED)
    cx, cl, ch = [], [], []
    for s in range(0, len(df_s) - 50, 5):
        sub  = df_s.iloc[s:s+50]
        ph   = sub["acerto"].mean()
        se_w = np.sqrt(ph*(1-ph)/len(sub))
        cx.append(s + 25)
        cl.append((ph - 1.96*se_w)*100)
        ch.append((ph + 1.96*se_w)*100)
    if cx:
        ax.fill_between(cx, cl, ch, alpha=0.18, color=BLUE, label="IC 95% (janela 50)")
    ax.axhline(50, color=RED,  linestyle="--", alpha=0.7, label="50%")
    ax.axhline(55, color=GOLD, linestyle=":",  alpha=0.6, label="55%")
    if is_sim and true_acc:
        ax.axhline(true_acc*100, color=PURPLE, linestyle=":",
                   linewidth=1.5, label=f"Alvo {true_acc*100:.0f}%")
    ax.set_ylim(15, 90)
    ax.set_xlabel("N snapshots", color=WHITE)
    ax.set_ylabel("% Acerto", color=WHITE)
    ax.legend(facecolor=PANEL, labelcolor=WHITE, fontsize=8, ncol=5)

    taxa  = result["taxa"]
    ci_lo = result["ci_lo"]
    ci_hi = result["ci_hi"]
    acc_note = f" | Sim {true_acc*100:.0f}%" if is_sim and true_acc else ""
    fig.suptitle(
        f"🐋 WHALE BACKTEST{tag}  |  Taxa: {taxa:.1f}% "
        f"[{ci_lo:.1f}%–{ci_hi:.1f}%]  |  "
        f"{result['valid_count']}/3 testes{acc_note}",
        color=WHITE, fontsize=12, fontweight="bold", y=1.005)

    plt.savefig(save_path, dpi=140, bbox_inches="tight", facecolor=BG)
    plt.close()
    print(f"  📊 Dashboard salvo: {save_path}")
