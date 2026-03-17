"""
strategy/indicators.py
──────────────────────
Indicadores técnicos puros: ATR, swing highs/lows, sesiones.
Funciones sin estado, solo transforman DataFrames.
"""

import pandas as pd
import numpy as np
from config import strategy as cfg
from config.settings import parse_trading_windows


# ══════════════════════════════════════════════════════════
#  ATR
# ══════════════════════════════════════════════════════════

def calcular_atr(df: pd.DataFrame, periodo: int = None) -> pd.Series:
    """Average True Range."""
    periodo = periodo or cfg.ATR_PERIOD
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()


# ══════════════════════════════════════════════════════════
#  SWING HIGHS / LOWS
# ══════════════════════════════════════════════════════════

def detectar_swings(df: pd.DataFrame, length: int = None) -> pd.Series:
    """
    Detecta puntos de swing estructurales.
    Returns:
        Series con valores:  1 = SwingHigh | -1 = SwingLow | 0 = ninguno
    """
    length = length or cfg.SWING_LENGTH
    result = pd.Series(0, index=df.index, dtype=int)
    for i in range(length, len(df) - length):
        ventana_h = df["high"].iloc[i - length : i + length + 1]
        ventana_l = df["low"].iloc[i - length  : i + length + 1]
        if df["high"].iloc[i] == ventana_h.max():
            result.iloc[i] = 1
        elif df["low"].iloc[i] == ventana_l.min():
            result.iloc[i] = -1
    return result


def obtener_swing_highs(df: pd.DataFrame, swings: pd.Series) -> list[tuple]:
    """Retorna lista de (timestamp, precio) de todos los swing highs."""
    return [(swings.index[i], df["high"].iloc[i])
            for i in range(len(swings)) if swings.iloc[i] == 1]


def obtener_swing_lows(df: pd.DataFrame, swings: pd.Series) -> list[tuple]:
    """Retorna lista de (timestamp, precio) de todos los swing lows."""
    return [(swings.index[i], df["low"].iloc[i])
            for i in range(len(swings)) if swings.iloc[i] == -1]


# ══════════════════════════════════════════════════════════
#  TENDENCIA DE MERCADO
# ══════════════════════════════════════════════════════════

def detectar_tendencia(swings: pd.Series, df: pd.DataFrame,
                       hasta_idx: int) -> str:
    """
    Determina tendencia mirando los últimos swings hasta `hasta_idx`.
    Returns: 'ALCISTA' | 'BAJISTA' | 'LATERAL'

    Optimización: busca los últimos 2 swing highs y 2 swing lows
    desde atrás en vez de construir listas completas.
    """
    # Buscar últimos 2 swing highs
    highs = []
    for i in range(hasta_idx - 1, -1, -1):
        if swings.iloc[i] == 1:
            highs.append(df["high"].iloc[i])
            if len(highs) == 2:
                break
    highs.reverse()

    # Buscar últimos 2 swing lows
    lows = []
    for i in range(hasta_idx - 1, -1, -1):
        if swings.iloc[i] == -1:
            lows.append(df["low"].iloc[i])
            if len(lows) == 2:
                break
    lows.reverse()

    if len(highs) < 2 or len(lows) < 2:
        return "LATERAL"

    hh = highs[-1] > highs[-2]   # Higher High
    hl = lows[-1]  > lows[-2]    # Higher Low
    lh = highs[-1] < highs[-2]   # Lower High
    ll = lows[-1]  < lows[-2]    # Lower Low

    if hh and hl:
        return "ALCISTA"
    if lh and ll:
        return "BAJISTA"
    return "LATERAL"


# ══════════════════════════════════════════════════════════
#  SESIONES DE TRADING
# ══════════════════════════════════════════════════════════

SESIONES_HORARIAS = {
    "asia":     (2,  10),
    "london":   (7,  16),
    "new_york": (12, 21),
}

def sesion_activa(timestamp: pd.Timestamp) -> str:
    """
    Determina la sesión de trading para un timestamp UTC.
    Prioriza new_york > london > asia cuando hay solapamiento.
    """
    hora = timestamp.hour if hasattr(timestamp, 'hour') else pd.Timestamp(timestamp).hour
    # Orden de prioridad en solapamientos
    for nombre in ["new_york", "london", "asia"]:
        inicio, fin = SESIONES_HORARIAS[nombre]
        if inicio <= hora < fin:
            return nombre
    return "off"


def en_sesion_activa(timestamp, sesiones: list = None) -> bool:
    """True si el timestamp está dentro de alguna sesión permitida."""
    sesiones = sesiones or cfg.SESIONES_ACTIVAS
    if not cfg.FILTRO_SESION:
        return True
    return sesion_activa(timestamp) in sesiones


def en_ventana_horaria(timestamp) -> bool:
    """
    True si la hora del timestamp cae dentro de alguna ventana
    de trading configurada en TRADING_WINDOWS.
    Si no hay ventanas configuradas, retorna True (sin filtro).
    """
    windows = parse_trading_windows(cfg.TRADING_WINDOWS_RAW)
    if not windows:
        return True

    hora = timestamp.hour if hasattr(timestamp, 'hour') else pd.Timestamp(timestamp).hour

    for inicio, fin in windows:
        if inicio <= hora < fin:
            return True
    return False


def descripcion_ventana_horaria(timestamp) -> str:
    """Retorna descripción legible de la ventana actual o 'fuera de horario'."""
    windows = parse_trading_windows(cfg.TRADING_WINDOWS_RAW)
    if not windows:
        return "24h"

    hora = timestamp.hour if hasattr(timestamp, 'hour') else pd.Timestamp(timestamp).hour

    for inicio, fin in windows:
        if inicio <= hora < fin:
            return f"{inicio:02d}-{fin:02d}UTC"
    return "fuera_horario"
