"""
data/fetcher.py
───────────────
Responsabilidad única: obtener datos OHLCV desde Binance o fallback simulado.
No contiene lógica de estrategia ni de trading.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import exchange as cfg
from utils.logger import get_logger

logger = get_logger(__name__)

# Directorio de caché local
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Mapeo de timeframes a intervalos de Binance
TF_TO_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15,
    "30m": 30, "1h": 60, "2h": 120, "4h": 240,
}


# ══════════════════════════════════════════════════════════
#  CLIENTE BINANCE
# ══════════════════════════════════════════════════════════

def crear_cliente(api_key: str = "", api_secret: str = "", testnet: bool = True):
    """
    Crea el cliente de Binance.
    Si no se pasan credenciales, crea uno de solo lectura (para datos históricos).
    """
    try:
        from binance.client import Client
        client = Client(api_key, api_secret, testnet=testnet)
        logger.info("Cliente Binance creado. Testnet=%s", testnet)
        return client
    except ImportError:
        logger.warning("python-binance no instalado. Solo datos simulados disponibles.")
        return None
    except Exception as e:
        logger.error("Error al crear cliente Binance: %s", e)
        return None


def crear_cliente_futures(api_key: str, api_secret: str, testnet: bool = True):
    """Cliente con credenciales completas para operar futuros."""
    from binance.client import Client
    from binance.exceptions import BinanceAPIException
    try:
        client = Client(api_key, api_secret, testnet=testnet)
        client.futures_change_leverage(symbol=cfg.SYMBOL, leverage=cfg.LEVERAGE)
        logger.info("Cliente Futures conectado. Symbol=%s Leverage=%sx", cfg.SYMBOL, cfg.LEVERAGE)
        return client
    except BinanceAPIException as e:
        logger.error("Error Binance API: %s", e)
        raise


# ══════════════════════════════════════════════════════════
#  DESCARGA DE VELAS
# ══════════════════════════════════════════════════════════

def obtener_velas(
    client,
    symbol: str = None,
    timeframe: str = None,
    limit: int = 200,
    usar_cache: bool = False,
) -> pd.DataFrame:
    """
    Descarga velas OHLCV desde Binance Futures.
    Fallback automático a datos simulados si no hay cliente.

    Args:
        client:     Cliente de Binance (puede ser None → simulado)
        symbol:     Par de trading (default: de config)
        timeframe:  Temporalidad (default: de config)
        limit:      Cantidad de velas
        usar_cache: Si True, guarda/lee desde caché local

    Returns:
        DataFrame con columnas [open, high, low, close, volume]
        indexado por timestamp UTC.
    """
    symbol    = symbol    or cfg.SYMBOL
    timeframe = timeframe or cfg.TIMEFRAME

    if usar_cache:
        cached = _leer_cache(symbol, timeframe, limit)
        if cached is not None:
            return cached

    if client is None:
        logger.warning("Sin cliente Binance — usando datos simulados.")
        return _datos_simulados(limit, timeframe)

    try:
        from binance.client import Client as BClient
        tf_map = {
            "1m":  BClient.KLINE_INTERVAL_1MINUTE,
            "5m":  BClient.KLINE_INTERVAL_5MINUTE,
            "15m": BClient.KLINE_INTERVAL_15MINUTE,
            "1h":  BClient.KLINE_INTERVAL_1HOUR,
            "4h":  BClient.KLINE_INTERVAL_4HOUR,
        }
        klines = client.futures_klines(
            symbol=symbol,
            interval=tf_map.get(timeframe, timeframe),
            limit=limit,
        )
        df = _klines_a_dataframe(klines)
        if usar_cache:
            _escribir_cache(df, symbol, timeframe, limit)
        logger.debug("Velas descargadas: %d | %s %s", len(df), symbol, timeframe)
        return df

    except Exception as e:
        logger.error("Error al descargar velas: %s. Fallback a simulados.", e)
        return _datos_simulados(limit, timeframe)


def obtener_historico_backtest(
    symbol: str = None,
    timeframe: str = None,
    dias: int = 90,
) -> pd.DataFrame:
    """
    Descarga datos históricos para backtesting.
    Usa endpoint público de Binance (sin API key).
    """
    symbol    = symbol    or cfg.SYMBOL
    timeframe = timeframe or cfg.TIMEFRAME

    try:
        from binance.client import Client as BClient
        client = BClient("", "")   # Solo lectura, sin autenticación
        tf_map = {
            "1m": BClient.KLINE_INTERVAL_1MINUTE,
            "5m": BClient.KLINE_INTERVAL_5MINUTE,
            "15m": BClient.KLINE_INTERVAL_15MINUTE,
            "1h": BClient.KLINE_INTERVAL_1HOUR,
            "4h": BClient.KLINE_INTERVAL_4HOUR,
        }
        start = f"{dias} days ago UTC"
        logger.info("Descargando histórico %s %s (%d días)...", symbol, timeframe, dias)
        klines = client.get_historical_klines(symbol, tf_map[timeframe], start)
        df = _klines_a_dataframe(klines)
        logger.info("Histórico cargado: %d velas (%s → %s)",
                    len(df), str(df.index[0])[:10], str(df.index[-1])[:10])
        return df

    except Exception as e:
        logger.warning("No se pudo descargar histórico de Binance: %s. Usando simulados.", e)
        return _datos_simulados_largo(dias, timeframe)


# ══════════════════════════════════════════════════════════
#  PRECIO EN TIEMPO REAL
# ══════════════════════════════════════════════════════════

def obtener_precio_actual(client, symbol: str = None) -> Optional[float]:
    """Retorna el último precio del par."""
    symbol = symbol or cfg.SYMBOL
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except Exception as e:
        logger.error("Error al obtener precio: %s", e)
        return None


def obtener_balance_usdt(client) -> float:
    """Retorna el balance disponible en USDT (Futures)."""
    try:
        balance = client.futures_account_balance()
        for asset in balance:
            if asset["asset"] == "USDT":
                return float(asset["availableBalance"])
    except Exception as e:
        logger.error("Error al obtener balance: %s", e)
    return 0.0


# ══════════════════════════════════════════════════════════
#  HELPERS INTERNOS
# ══════════════════════════════════════════════════════════

def _klines_a_dataframe(klines: list) -> pd.DataFrame:
    """Convierte lista de klines de Binance a DataFrame limpio."""
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df.set_index("timestamp", inplace=True)
    return df


def _datos_simulados(limit: int = 200, timeframe: str = "15m") -> pd.DataFrame:
    """Genera velas OHLCV simuladas para desarrollo/testing."""
    freq_map = {"1m":"1min","5m":"5min","15m":"15min","1h":"1h","4h":"4h"}
    freq  = freq_map.get(timeframe, "15min")
    end   = datetime.utcnow()
    start = end - timedelta(minutes=limit * TF_TO_MINUTES.get(timeframe, 15))
    return _generar_ohlcv(pd.date_range(start=start, periods=limit, freq=freq))


def _datos_simulados_largo(dias: int = 90, timeframe: str = "15m") -> pd.DataFrame:
    """Genera datos históricos simulados para backtesting."""
    freq_map = {"1m":"1min","5m":"5min","15m":"15min","1h":"1h","4h":"4h"}
    freq   = freq_map.get(timeframe, "15min")
    end    = datetime.utcnow()
    start  = end - timedelta(days=dias)
    fechas = pd.date_range(start=start, end=end, freq=freq)
    df = _generar_ohlcv(fechas, seed=2024)
    logger.info("Datos simulados generados: %d velas", len(df))
    return df


def _generar_ohlcv(fechas: pd.DatetimeIndex, precio_base: float = 42000.0,
                   seed: int = 42) -> pd.DataFrame:
    """Genera OHLCV realista usando random walk con ciclos de tendencia."""
    np.random.seed(seed)
    precio = precio_base
    rows   = []
    drift  = 0.00005

    for i, _ in enumerate(fechas):
        if i % 200 == 0:
            drift = float(np.random.choice([-0.0003, 0.0001, 0.0003]))
        vol_inst = np.random.uniform(0.002, 0.006)
        cambio   = np.random.normal(drift, vol_inst)
        open_    = precio
        close_   = precio * (1 + cambio)
        wick_u   = abs(np.random.normal(0, vol_inst * 0.4))
        wick_d   = abs(np.random.normal(0, vol_inst * 0.4))
        high_    = max(open_, close_) * (1 + wick_u)
        low_     = min(open_, close_) * (1 - wick_d)
        vol      = float(np.random.lognormal(mean=5.5, sigma=0.4))
        rows.append([open_, high_, low_, close_, vol])
        precio = close_

    return pd.DataFrame(rows, columns=["open","high","low","close","volume"],
                        index=fechas)


# ══════════════════════════════════════════════════════════
#  CACHÉ LOCAL
# ══════════════════════════════════════════════════════════

def _cache_path(symbol: str, timeframe: str, limit: int) -> Path:
    return CACHE_DIR / f"{symbol}_{timeframe}_{limit}.parquet"


def _leer_cache(symbol: str, timeframe: str, limit: int,
                max_age_min: int = 5) -> Optional[pd.DataFrame]:
    """Lee caché si existe y es reciente (< max_age_min minutos)."""
    path = _cache_path(symbol, timeframe, limit)
    if not path.exists():
        return None
    age = (datetime.utcnow().timestamp() - path.stat().st_mtime) / 60
    if age > max_age_min:
        return None
    try:
        df = pd.read_parquet(path)
        logger.debug("Cache hit: %s", path.name)
        return df
    except Exception:
        return None


def _escribir_cache(df: pd.DataFrame, symbol: str, timeframe: str, limit: int):
    try:
        df.to_parquet(_cache_path(symbol, timeframe, limit))
    except Exception as e:
        logger.warning("No se pudo escribir caché: %s", e)
