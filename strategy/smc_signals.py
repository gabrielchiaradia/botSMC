"""
strategy/smc_signals.py
────────────────────────
Lógica SMC pura: detección de FVG, Order Blocks, BOS/CHoCH
y evaluación de señales de entrada.
Sin dependencias de exchange ni de ejecución de órdenes.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from config import strategy as cfg, risk as rcfg
from strategy.indicators import (
    calcular_atr,
    detectar_swings,
    detectar_tendencia,
    sesion_activa,
    en_sesion_activa,
    en_ventana_horaria,
    descripcion_ventana_horaria,
)


# ══════════════════════════════════════════════════════════
#  ESTRUCTURAS
# ══════════════════════════════════════════════════════════

@dataclass
class FVG:
    timestamp: pd.Timestamp
    tipo:      str        # ALCISTA | BAJISTA
    top:       float
    bottom:    float
    mitigado:  bool = False

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def size(self) -> float:
        return self.top - self.bottom


@dataclass
class OrderBlock:
    timestamp: pd.Timestamp
    tipo:      str        # ALCISTA | BAJISTA
    top:       float
    bottom:    float
    fuerza:    float = 0.0   # Porcentaje del movimiento detonador

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Señal:
    tiene_señal:    bool  = False
    direccion:      str   = ""      # ALCISTA | BAJISTA
    precio_entrada: float = 0.0
    stop_loss:      float = 0.0
    take_profit:    float = 0.0
    score:          int   = 0       # 0–100
    motivos:        list  = field(default_factory=list)
    sesion:         str   = ""
    ventana:        str   = ""      # "08-11UTC" | "13-18UTC" | "fuera_horario"
    fuera_de_horario: bool = False  # True = señal válida pero fuera de ventana
    atr:            float = 0.0
    tendencia:      str   = ""

    @property
    def riesgo_por_unidad(self) -> float:
        return abs(self.precio_entrada - self.stop_loss)

    @property
    def recompensa_por_unidad(self) -> float:
        return abs(self.precio_entrada - self.take_profit)

    @property
    def rr_ratio(self) -> float:
        if self.riesgo_por_unidad == 0:
            return 0.0
        return self.recompensa_por_unidad / self.riesgo_por_unidad


# ══════════════════════════════════════════════════════════
#  DETECCIÓN DE FVG
# ══════════════════════════════════════════════════════════

def detectar_fvg(df: pd.DataFrame,
                 lookback: int = None) -> list[FVG]:
    """
    Detecta Fair Value Gaps en las últimas `lookback` velas.

    FVG Alcista: low[i] > high[i-2]  → imbalance alcista
    FVG Bajista: high[i] < low[i-2]  → imbalance bajista
    """
    lookback = lookback or cfg.FVG_LOOKBACK
    fvgs: list[FVG] = []
    inicio = max(2, len(df) - lookback)

    for i in range(inicio, len(df)):
        low_i   = df["low"].iloc[i]
        high_i  = df["high"].iloc[i]
        high_i2 = df["high"].iloc[i - 2]
        low_i2  = df["low"].iloc[i - 2]

        if low_i > high_i2:
            fvgs.append(FVG(
                timestamp = df.index[i],
                tipo      = "ALCISTA",
                top       = low_i,
                bottom    = high_i2,
            ))
        elif high_i < low_i2:
            fvgs.append(FVG(
                timestamp = df.index[i],
                tipo      = "BAJISTA",
                top       = low_i2,
                bottom    = high_i,
            ))

    return fvgs


def fvg_en_precio(fvgs: list[FVG], precio: float,
                  tipo: str, margen_pct: float = 0.002) -> Optional[FVG]:
    """
    Retorna el FVG más reciente del tipo dado si el precio está dentro (±margen).
    Ignora FVGs ya mitigados.
    """
    margen = precio * margen_pct
    for fvg in reversed(fvgs):
        if fvg.tipo != tipo or fvg.mitigado:
            continue
        if fvg.bottom - margen <= precio <= fvg.top + margen:
            return fvg
    return None


def mitigar_fvgs(fvgs: list[FVG], df: pd.DataFrame, desde_idx: int):
    """
    Marca como mitigados los FVGs que el precio ya atravesó.
    Un FVG alcista se mitiga si el precio baja hasta su zona.
    Un FVG bajista se mitiga si el precio sube hasta su zona.
    Esto evita reusar zonas ya tocadas como confluencia válida.

    Optimización: solo chequea la última vela de la ventana.
    Como se llama en cada iteración del backtest, cada vela
    se evalúa exactamente una vez (no recorre toda la historia).
    """
    if desde_idx >= len(df):
        return
    # Solo mirar la última vela — el backtest llama esto cada barra
    last = len(df) - 1
    low_last  = df["low"].iloc[last]
    high_last = df["high"].iloc[last]

    for fvg in fvgs:
        if fvg.mitigado:
            continue
        if fvg.tipo == "ALCISTA" and low_last <= fvg.midpoint:
            fvg.mitigado = True
        elif fvg.tipo == "BAJISTA" and high_last >= fvg.midpoint:
            fvg.mitigado = True


# ══════════════════════════════════════════════════════════
#  DETECCIÓN DE ORDER BLOCKS
# ══════════════════════════════════════════════════════════

def detectar_order_blocks(df: pd.DataFrame,
                          lookback: int = None,
                          umbral: float = None) -> list[OrderBlock]:
    """
    Detecta Order Blocks: última vela contra-tendencial antes de
    un movimiento impulsivo fuerte.

    OB Alcista: vela bajista seguida de vela muy alcista (>umbral%)
    OB Bajista: vela alcista seguida de vela muy bajista (<-umbral%)
    """
    lookback = lookback or cfg.OB_LOOKBACK
    umbral   = umbral   or cfg.OB_MOVE_THRESHOLD
    obs: list[OrderBlock] = []
    inicio = max(1, len(df) - lookback)

    for i in range(inicio, len(df) - 1):
        o_i   = df["open"].iloc[i]
        c_i   = df["close"].iloc[i]
        o_i1  = df["open"].iloc[i + 1]
        c_i1  = df["close"].iloc[i + 1]
        mov   = (c_i1 - o_i1) / o_i1 if o_i1 != 0 else 0

        # OB Alcista: vela bajista + siguiente impulso alcista
        if c_i < o_i and mov > umbral:
            obs.append(OrderBlock(
                timestamp = df.index[i],
                tipo      = "ALCISTA",
                top       = o_i,
                bottom    = c_i,
                fuerza    = round(mov * 100, 3),
            ))
        # OB Bajista: vela alcista + siguiente impulso bajista
        elif c_i > o_i and mov < -umbral:
            obs.append(OrderBlock(
                timestamp = df.index[i],
                tipo      = "BAJISTA",
                top       = c_i,
                bottom    = o_i,
                fuerza    = round(abs(mov) * 100, 3),
            ))

    return obs


def ob_en_precio(obs: list[OrderBlock], precio: float,
                 tipo: str, margen_pct: float = 0.003) -> Optional[OrderBlock]:
    """Retorna el OB más reciente del tipo dado si precio está dentro (±margen)."""
    margen = precio * margen_pct
    for ob in reversed(obs):
        if ob.tipo != tipo:
            continue
        if ob.bottom - margen <= precio <= ob.top + margen:
            return ob
    return None


# ══════════════════════════════════════════════════════════
#  BOS / CHoCH
# ══════════════════════════════════════════════════════════

def detectar_bos(df: pd.DataFrame, swings: pd.Series,
                 hasta_idx: int, tendencia: str) -> bool:
    """
    Break of Structure: el precio rompe el último swing en
    dirección de la tendencia confirmada.

    Optimización: busca el último swing relevante desde atrás
    en vez de construir la lista completa de todos los swings.
    """
    precio = df["close"].iloc[hasta_idx]

    if tendencia == "ALCISTA":
        # Buscar el último swing high antes de hasta_idx
        for pos in range(hasta_idx - 1, -1, -1):
            if swings.iloc[pos] == 1:
                return precio > df["high"].iloc[pos]
        return False

    if tendencia == "BAJISTA":
        for pos in range(hasta_idx - 1, -1, -1):
            if swings.iloc[pos] == -1:
                return precio < df["low"].iloc[pos]
        return False

    return False


# ══════════════════════════════════════════════════════════
#  EVALUADOR DE SEÑAL PRINCIPAL
# ══════════════════════════════════════════════════════════

def evaluar_señal(df: pd.DataFrame,
                  swings: pd.Series,
                  atr: pd.Series,
                  idx: int) -> Señal:
    """
    Evalúa condiciones SMC en la vela `idx` y devuelve una Señal.

    Scoring:
      FVG en dirección de tendencia  → +35 pts
      OB  en dirección de tendencia  → +40 pts
      BOS confirmado                 → +25 pts
      Total ≥ SCORE_MINIMO           → señal válida
    """
    precio    = df["close"].iloc[idx]
    atr_val   = float(atr.iloc[idx])
    timestamp = df.index[idx]
    sesion    = sesion_activa(timestamp)
    tendencia = detectar_tendencia(swings, df, idx)

    señal = Señal(
        precio_entrada = precio,
        sesion         = sesion,
        ventana        = descripcion_ventana_horaria(timestamp),
        atr            = atr_val,
        tendencia      = tendencia,
    )

    # ── Filtros previos ────────────────────────────────────
    if tendencia == "LATERAL":
        señal.motivos.append("Mercado lateral")
        return señal

    if cfg.FILTRO_SESION and not en_sesion_activa(timestamp):
        señal.motivos.append(f"Fuera de sesión ({sesion})")
        return señal

    if pd.isna(atr_val) or atr_val == 0:
        señal.motivos.append("ATR no disponible")
        return señal

    # ── Filtro volatilidad mínima ──────────────────────────
    volatilidad_pct = atr_val / precio
    if volatilidad_pct < 0.001:
        señal.motivos.append(
            f"Volatilidad insuficiente: ATR={atr_val:.2f} "
            f"({volatilidad_pct*100:.3f}% del precio)"
        )
        return señal

    # ── Detección de confluencias ──────────────────────────
    ventana_df = df.iloc[max(0, idx - cfg.FVG_LOOKBACK) : idx + 1]
    fvgs    = detectar_fvg(ventana_df)
    obs     = detectar_order_blocks(ventana_df)

    # Mitigar FVGs ya tocados para no reusar zonas agotadas
    mitigar_fvgs(fvgs, ventana_df, 0)

    tipo_buscar = "ALCISTA" if tendencia == "ALCISTA" else "BAJISTA"

    fvg_hit = fvg_en_precio(fvgs, precio, tipo_buscar)
    if fvg_hit:
        señal.score += 35
        señal.motivos.append(
            f"FVG {fvg_hit.tipo.lower()} [{fvg_hit.bottom:.0f}–{fvg_hit.top:.0f}]"
        )

    ob_hit = ob_en_precio(obs, precio, tipo_buscar)
    if ob_hit:
        señal.score += 40
        señal.motivos.append(
            f"OB {ob_hit.tipo.lower()} [{ob_hit.bottom:.0f}–{ob_hit.top:.0f}] "
            f"fuerza={ob_hit.fuerza}%"
        )

    bos = detectar_bos(df, swings, idx, tendencia)
    if bos:
        señal.score += 25
        señal.motivos.append("BOS confirmado")

    # ── Construir niveles si hay señal ────────────────────
    if señal.score >= cfg.SCORE_MINIMO:
        señal.tiene_señal = True
        señal.direccion   = tendencia
        dist_sl = atr_val * rcfg.ATR_MULTIPLIER
        dist_tp = dist_sl * rcfg.TP_RR_RATIO

        if tendencia == "ALCISTA":
            señal.stop_loss   = round(precio - dist_sl, 2)
            señal.take_profit = round(precio + dist_tp, 2)
        else:
            señal.stop_loss   = round(precio + dist_sl, 2)
            señal.take_profit = round(precio - dist_tp, 2)

        # ── Filtro ventana horaria ─────────────────────────
        # La señal se evalúa completa (con niveles) pero se marca
        # como fuera_de_horario si no cae en las ventanas configuradas.
        # En el bot en vivo se registra y notifica pero no se opera.
        # En backtest se descarta.
        if not en_ventana_horaria(timestamp):
            señal.fuera_de_horario = True
            señal.motivos.append(
                f"⏰ Fuera de horario ({timestamp.hour:02d}:00 UTC, "
                f"ventana: {señal.ventana})"
            )

    return señal


# ══════════════════════════════════════════════════════════
#  ANÁLISIS COMPLETO (para el bot en vivo)
# ══════════════════════════════════════════════════════════

def analizar_mercado(df: pd.DataFrame) -> dict:
    """
    Ejecuta análisis SMC completo sobre el DataFrame.
    Retorna dict con todos los artefactos para display y decisión.
    """
    atr    = calcular_atr(df)
    swings = detectar_swings(df)
    señal  = evaluar_señal(df, swings, atr, len(df) - 1)
    fvgs   = detectar_fvg(df)
    obs    = detectar_order_blocks(df)

    highs = [(swings.index[i], df["high"].iloc[i]) for i in range(len(swings)) if swings.iloc[i] == 1]
    lows  = [(swings.index[i], df["low"].iloc[i])  for i in range(len(swings)) if swings.iloc[i] == -1]

    return {
        "señal":     señal,
        "fvgs":      fvgs,
        "obs":       obs,
        "atr":       float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0,
        "tendencia": señal.tendencia,
        "swings": {
            "highs": highs[-3:] if highs else [],
            "lows":  lows[-3:]  if lows  else [],
        },
    }
