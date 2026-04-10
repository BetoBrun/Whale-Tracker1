#!/usr/bin/env python3
import os
from pathlib import Path

# Diretórios
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# Arquivos principais
CSV_FILE = DATA_DIR / "whale_snapshots.csv"
ALERTS_FILE = DATA_DIR / "alerts.json"
OPTIONS_FILE = DATA_DIR / "options_data.json"
MARKET_CONTEXT_FILE = DATA_DIR / "market_context.json"
MARKET_CONTEXT_HISTORY_FILE = DATA_DIR / "market_context_history.json"
SPIKE_ALERTS_FILE = DATA_DIR / "spike_alerts.json"
SPIKE_STATE_FILE = DATA_DIR / "spike_state.json"

# Horizonte / coleta
HORIZON_H = int(os.getenv("HORIZON_H", "4"))
COLLECT_INTERVAL_H = int(os.getenv("COLLECT_INTERVAL_H", "4"))

# URLs
INFO_URL = "https://api.hyperliquid.xyz/info"

# Robustez
HEALTH_TIMEOUT = int(os.getenv("HEALTH_TIMEOUT", "30"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2.0"))

# Thresholds
MAX_ALERT_AGE_MINUTES = int(os.getenv("MAX_ALERT_AGE_MINUTES", "65"))
MAX_CONTEXT_AGE_MINUTES = int(os.getenv("MAX_CONTEXT_AGE_MINUTES", "65"))
MAX_SPIKE_AGE_MINUTES = int(os.getenv("MAX_SPIKE_AGE_MINUTES", "15"))

MIN_ADDRESSES_FOR_SPIKES = int(os.getenv("MIN_ADDRESSES_FOR_SPIKES", "5"))
MIN_WHALES_FOR_SIGNAL = int(os.getenv("MIN_WHALES_FOR_SIGNAL", "8"))

# Feature flags
ENABLE_SPIKES = os.getenv("ENABLE_SPIKES", "true").lower() == "true"
ENABLE_OPTIONS = os.getenv("ENABLE_OPTIONS", "true").lower() == "true"
ENABLE_MARKET_CONTEXT = os.getenv("ENABLE_MARKET_CONTEXT", "true").lower() == "true"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"

# Schema
SCHEMA_VERSION = "2.0.0"
