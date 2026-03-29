from pathlib import Path
import os

BASE_DIR = Path(__file__).parent

DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

CSV_FILE   = DATA_DIR / "whale_snapshots.csv"
STATE_FILE = DATA_DIR / "last_telegram_signal.json"

INFO_URL        = "https://api.hyperliquid.xyz/info"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

# ── Pool de coleta ────────────────────────────────────────────────────────────
# Aumentado de 25 → 100 para garantir ≥15 baleias direcionais ativas após filtros
TOP_N            = int(os.getenv("TOP_N",            "100"))

# Reduzido de $1.000.000 → $250.000 para incluir traders consistentes menores
MIN_PNL_ALL_TIME = float(os.getenv("MIN_PNL_ALL_TIME", "250000"))

# ── Filtros de posição ────────────────────────────────────────────────────────
# Reduzido de $50.000 → $25.000 para capturar mais sinais relevantes
MIN_NOTIONAL_POS = float(os.getenv("MIN_NOTIONAL_POS", "25000"))

# Reduzido de 20 → 15 para ser mais seletivo na classificação direcional
MAX_POSITIONS_PER_WHALE = int(os.getenv("MAX_POSITIONS_PER_WHALE", "15"))

MIN_DIRECTIONAL_RATIO = float(os.getenv("MIN_DIRECTIONAL_RATIO", "0.60"))

# ── Threshold de sinal ────────────────────────────────────────────────────────
# Aumentado de 5 → 15 para garantir validade estatística mínima
MIN_ACTIVE_WHALES = int(os.getenv("MIN_ACTIVE_WHALES", "15"))

HORIZON_H = int(os.getenv("HORIZON_H", "4"))

# Aumentado de 62.0 → 65.0 para reduzir falsos positivos com pool maior
MIN_SIGNAL_PCT = float(os.getenv("MIN_SIGNAL_PCT", "65.0"))

COLLECT_INTERVAL_H = int(os.getenv("COLLECT_INTERVAL_H", "4"))

# ── Famílias de ativos ────────────────────────────────────────────────────────
BTC_FAMILY = {"BTC", "WBTC", "UBTC", "TBTC"}
ETH_FAMILY = {"ETH", "WETH", "stETH", "rETH"}

# ── Paleta do dashboard ───────────────────────────────────────────────────────
BG, PANEL        = "#0a0a0f", "#12121c"
GREEN, RED       = "#00e676", "#ff1744"
GOLD, BLUE       = "#ffd600", "#2979ff"
PURPLE           = "#d500f9"
WHITE            = "#e8e8f0"

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN          = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID            = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED            = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_ONLY_DIRECTIONAL   = os.getenv("TELEGRAM_ONLY_DIRECTIONAL", "true").lower() == "true"
TELEGRAM_DEDUP_WINDOW_H     = int(os.getenv("TELEGRAM_DEDUP_WINDOW_H", "3"))"3"))
