"""
strategy/risk.py
────────────────
Gestión de riesgo y cálculo de tamaño de posición.
Separado de la estrategia para poder modificar
el money management sin tocar las señales.
"""

from config import risk as cfg
from utils.logger import get_logger

logger = get_logger(__name__)

# Mapa de precisión por símbolo (stepSize de Binance Futures)
_STEP_SIZE = {
    "ETHUSDT": 3,
    "BTCUSDT": 3,
    "SOLUSDT": 1,  # ejemplo
}
_DEFAULT_DECIMALS = 3

def _decimales_cantidad(symbol: str) -> int:
    return _STEP_SIZE.get(symbol.upper(), _DEFAULT_DECIMALS)

def calcular_tamaño(capital: float, precio: float,
                    stop_loss: float, symbol: str = "ETHUSDT") -> float:
    """
    Calcula el tamaño de posición en unidades base (BTC, ETH, etc.)
    usando riesgo fijo por operación.

    Fórmula:
        riesgo_usd = capital × RISK_PER_TRADE
        distancia  = |precio − stop_loss|
        tamaño     = riesgo_usd / distancia

    Args:
        capital:   Balance disponible en USDT
        precio:    Precio de entrada
        stop_loss: Precio del stop loss

    Returns:
        Tamaño en unidades base (4 decimales)
    """
    distancia = abs(precio - stop_loss)
    if distancia == 0:
        logger.warning("Distancia SL = 0, no se puede calcular tamaño.")
        return 0.0

    riesgo_usd = capital * cfg.RISK_PER_TRADE
    tamaño     = riesgo_usd / distancia

    logger.debug(
        "Tamaño calculado: %.6f | Riesgo: $%.2f | Dist SL: $%.2f",
        tamaño, riesgo_usd, distancia
    )
    return round(tamaño, _decimales_cantidad(symbol))


def nocional(tamaño: float, precio: float) -> float:
    """Valor nocional de la posición en USDT."""
    return round(tamaño * precio, 2)


def validar_tamaño(tamaño: float, precio: float,
                   min_nocional: float = 5.0) -> bool:
    """
    Verifica que el tamaño sea válido para operar.
    Binance Futures requiere un nocional mínimo de ~$5.
    """
    if tamaño <= 0:
        return False
    if nocional(tamaño, precio) < min_nocional:
        logger.warning("Nocional $%.2f menor al mínimo $%.2f",
                       nocional(tamaño, precio), min_nocional)
        return False
    return True


def verificar_limite_diario(perdida_dia: float, capital: float) -> bool:
    """
    Retorna False si la pérdida del día supera MAX_DAILY_LOSS.
    Detiene el trading preventivamente.
    """
    if capital <= 0:
        return False
    perdida_pct = perdida_dia / capital
    if abs(perdida_pct) >= cfg.MAX_DAILY_LOSS:
        logger.warning(
            "Límite diario alcanzado: %.2f%% (máx %.2f%%)",
            abs(perdida_pct) * 100, cfg.MAX_DAILY_LOSS * 100
        )
        return False
    return True


def resumen_riesgo(capital: float, precio: float,
                   stop_loss: float, take_profit: float,
                   symbol: str = "ETHUSDT") -> dict:
    """Retorna un dict con el resumen de riesgo de una operación."""
    tamaño    = calcular_tamaño(capital, precio, stop_loss)
    riesgo    = abs(precio - stop_loss) * tamaño
    ganancia  = abs(precio - take_profit) * tamaño
    rr        = ganancia / riesgo if riesgo > 0 else 0

    return {
        "tamaño":       tamaño,
        "nocional_usd": nocional(tamaño, precio),
        "riesgo_usd":   round(riesgo, 2),
        "ganancia_usd": round(ganancia, 2),
        "rr_ratio":     round(rr, 2),
        "riesgo_pct":   round(cfg.RISK_PER_TRADE * 100, 2),
    }