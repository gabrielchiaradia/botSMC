"""
config/settings.py
──────────────────
Centraliza TODA la configuración del bot.
Las credenciales se cargan desde variables de entorno (.env),
NUNCA se hardcodean aquí.

Multi-bot: si BOT_NUMBER está seteado (ej: 2), carga .env2.
           Si es 1 o no está seteado, carga .env (default).
"""

import os
from dotenv import load_dotenv
from pathlib import Path

# ── Determinar qué .env cargar ──────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BOT_NUMBER = int(os.getenv("BOT_NUMBER", "1"))
_env_file  = ROOT_DIR / (f".env{BOT_NUMBER}" if BOT_NUMBER > 1 else ".env")

if not _env_file.exists() and BOT_NUMBER > 1:
    raise FileNotFoundError(
        f"No se encontró {_env_file}. Creá el archivo .env{BOT_NUMBER} "
        f"con la configuración para el bot #{BOT_NUMBER}."
    )

load_dotenv(_env_file)

# Tag identificador del bot (usado en logs y Telegram)
BOT_TAG = os.getenv("BOT_TAG", f"Bot{BOT_NUMBER}")


# ══════════════════════════════════════════════════════════
#  CREDENCIALES (solo desde entorno, nunca en código)
# ══════════════════════════════════════════════════════════
class Credentials:
    BINANCE_API_KEY:    str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
    TELEGRAM_TOKEN:     str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID:   str = os.getenv("TELEGRAM_CHAT_ID", "")

    @classmethod
    def validate(cls):
        """Lanza error si faltan credenciales críticas."""
        if not cls.BINANCE_API_KEY or not cls.BINANCE_API_SECRET:
            raise EnvironmentError(
                "Faltan BINANCE_API_KEY / BINANCE_API_SECRET en .env\n"
                "Copia .env.example a .env y completa tus credenciales."
            )


# ══════════════════════════════════════════════════════════
#  EXCHANGE
# ══════════════════════════════════════════════════════════
class ExchangeConfig:
    TESTNET:   bool = os.getenv("TESTNET", "true").lower() == "true"
    SYMBOL:    str  = os.getenv("SYMBOL", "BTCUSDT")
    TIMEFRAME: str  = os.getenv("TIMEFRAME", "15m")
    LEVERAGE:  int  = int(os.getenv("LEVERAGE", "2"))


# ══════════════════════════════════════════════════════════
#  GESTIÓN DE RIESGO
# ══════════════════════════════════════════════════════════
class RiskConfig:
    RISK_PER_TRADE:  float = float(os.getenv("RISK_PER_TRADE", "0.01"))   # 1%
    TP_RR_RATIO:     float = float(os.getenv("TP_RR_RATIO", "2.0"))       # 1:2
    ATR_MULTIPLIER:  float = float(os.getenv("ATR_MULTIPLIER", "1.5"))
    MAX_OPEN_TRADES: int   = int(os.getenv("MAX_OPEN_TRADES", "1"))
    MAX_DAILY_LOSS:  float = float(os.getenv("MAX_DAILY_LOSS", "0.03"))   # 3% diario


# ══════════════════════════════════════════════════════════
#  ESTRATEGIA SMC
# ══════════════════════════════════════════════════════════
class StrategyConfig:
    SCORE_MINIMO:      int   = int(os.getenv("SCORE_MINIMO", "50"))
    SWING_LENGTH:      int   = int(os.getenv("SWING_LENGTH", "5"))
    FVG_LOOKBACK:      int   = int(os.getenv("FVG_LOOKBACK", "30"))
    OB_LOOKBACK:       int   = int(os.getenv("OB_LOOKBACK", "30"))
    OB_MOVE_THRESHOLD: float = float(os.getenv("OB_MOVE_THRESHOLD", "0.004"))
    ATR_PERIOD:        int   = int(os.getenv("ATR_PERIOD", "14"))
    CANDLES_LIMIT:     int   = int(os.getenv("CANDLES_LIMIT", "200"))
    FILTRO_SESION:     bool  = os.getenv("FILTRO_SESION", "true").lower() == "true"
    SESIONES_ACTIVAS:  list  = os.getenv("SESIONES_ACTIVAS", "london,new_york").split(",")

    # Ventanas horarias de trading (UTC).
    # Formato: "HH-HH,HH-HH" → ej: "8-11,13-18"
    # Solo se abren trades dentro de estas ventanas.
    # Vacío = sin filtro horario (opera 24h).
    TRADING_WINDOWS_RAW: str = os.getenv("TRADING_WINDOWS", "8-11,13-18")


def parse_trading_windows(raw: str = None) -> list:
    """Parsea TRADING_WINDOWS a lista de tuplas [(inicio, fin), ...]"""
    if raw is None:
        raw = StrategyConfig.TRADING_WINDOWS_RAW
    raw = raw.strip()
    if not raw:
        return []
    windows = []
    for w in raw.split(","):
        w = w.strip()
        if "-" in w:
            parts = w.split("-")
            try:
                windows.append((int(parts[0]), int(parts[1])))
            except ValueError:
                pass
    return windows


# ══════════════════════════════════════════════════════════
#  BACKTEST
# ══════════════════════════════════════════════════════════
class BacktestConfig:
    DIAS:           int   = int(os.getenv("BACKTEST_DIAS", "90"))
    CAPITAL_INICIO: float = float(os.getenv("BACKTEST_CAPITAL", "1000.0"))
    COMISION_PCT:   float = float(os.getenv("COMISION_PCT", "0.0004"))   # 0.04%
    OUTPUT_DIR:     Path  = ROOT_DIR / "backtest" / "results"


# ══════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════
class LogConfig:
    LEVEL:    str  = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR:  Path = ROOT_DIR / "logs"
    LOG_FILE: str  = f"smc_bot_{BOT_NUMBER}.log" if BOT_NUMBER > 1 else "smc_bot.log"
    LOG_PATH: Path = LOG_DIR / LOG_FILE


# ── Instancias globales listas para importar ──────────────────────────────
creds    = Credentials()
exchange = ExchangeConfig()
risk     = RiskConfig()
strategy = StrategyConfig()
backtest = BacktestConfig()
logs     = LogConfig()


# ══════════════════════════════════════════════════════════
#  MULTI-TIMEFRAME  (Fase 3)
# ══════════════════════════════════════════════════════════
class MTFConfig:
    ENABLED:         bool = os.getenv("MTF_ENABLED", "true").lower() == "true"
    HTF:             str  = os.getenv("MTF_HTF", "1h")       # Contexto estructural
    LTF:             str  = os.getenv("MTF_LTF", "5m")       # Entrada precisa
    HTF_CANDLES:     int  = int(os.getenv("MTF_HTF_CANDLES", "200"))
    LTF_CANDLES:     int  = int(os.getenv("MTF_LTF_CANDLES", "100"))
    BONUS_ALINEADO:  int  = int(os.getenv("MTF_BONUS_ALINEADO", "20"))
    PENALTI_OPUESTO: int  = int(os.getenv("MTF_PENALTI_OPUESTO", "30"))


# ══════════════════════════════════════════════════════════
#  WEBSOCKET  (Fase 3)
# ══════════════════════════════════════════════════════════
class WebSocketConfig:
    ENABLED:        bool = os.getenv("WS_ENABLED", "true").lower() == "true"
    RECONNECT_SECS: int  = int(os.getenv("WS_RECONNECT_SECS", "5"))
    MAX_RECONNECTS: int  = int(os.getenv("WS_MAX_RECONNECTS", "10"))


# ── Agregar instancias a las globales ─────────────────────────────────────
mtf = MTFConfig()
ws  = WebSocketConfig()
