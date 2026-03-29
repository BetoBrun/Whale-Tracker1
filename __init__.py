# 🐋 Whale Tracker — pacote Python
# Permite importar módulos diretamente:
#   from whale_tracker.api import get_btc_price
#   from whale_tracker.storage import snapshot_stats

from pathlib import Path
import sys

# Adiciona o diretório do pacote ao path automaticamente
sys.path.insert(0, str(Path(__file__).parent))
