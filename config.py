from pathlib import Path
import os

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

CSV_FILE = DATA_DIR / "whale_snapshots.csv"
STATE_FILE = DATA_DIR / "last_telegram_signal.json"

INFO_URL        = "https://api.hyperliquid.xyz/info"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

TOP_N                   = int(os.getenv("TOP_N", "25"))
MIN_PNL_ALL_TIME        = float(os.getenv("MIN_PNL_ALL_TIME", "1000000"))
MIN_NOTIONAL_POS        = float(os.getenv("MIN_NOTIONAL_POS", "50000"))
MIN_ACTIVE_WHALES       = int(os.getenv("MIN_ACTIVE_WHALES", "5"))
MAX_POSITIONS_PER_WHALE = int(os.getenv("MAX_POSITIONS_PER_WHALE", "20"))
MIN_DIRECTIONAL_RATIO   = float(os.getenv("MIN_DIRECTIONAL_RATIO", "0.60"))

HORIZON_H      = int(os.getenv("HORIZON_H", "4"))
MIN_SIGNAL_PCT = float(os.getenv("MIN_SIGNAL_PCT", "62.0"))

COLLECT_INTERVAL_H = int(os.getenv("COLLECT_INTERVAL_H", "4"))

BTC_FAMILY = {"BTC", "WBTC", "UBTC", "TBTC"}
ETH_FAMILY = {"ETH", "WETH", "stETH", "rETH"}

BG, PANEL  = "#0a0a0f", "#12121c"
GREEN, RED = "#00e676", "#ff1744"
GOLD, BLUE = "#ffd600", "#2979ff"
PURPLE     = "#d500f9"
WHITE      = "#e8e8f0"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_ONLY_DIRECTIONAL = os.getenv("TELEGRAM_ONLY_DIRECTIONAL", "true").lower() == "true"
TELEGRAM_DEDUP_WINDOW_H = int(os.getenv("TELEGRAM_DEDUP_WINDOW_H", "3"))
