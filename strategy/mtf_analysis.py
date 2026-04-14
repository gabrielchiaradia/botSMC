"""
strategy/mtf_analysis.py
─────────────────────────
Análisis Multi-Timeframe (MTF).

Concepto:
  HTF (High TimeFrame, ej. 1h/4h) → define el contexto y la tendencia macro
  LTF (Low TimeFrame,  ej. 5m/15m) → da la entrada precisa

Regla principal:
  Solo se entra en LTF cuando la dirección COINCIDE con la tendencia HTF.
  Si HTF dice ALCISTA y LTF detecta señal ALCISTA → bonus de confirmación.
  Si HTF dice BAJISTA y LTF detecta señal ALCISTA → señal CANCELADA.

Esto filtra la mayoría de las entradas en contra de tendencia que
son la principal causa de pérdidas en SMC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from config import mtf as mcfg, strategy as scfg
from strategy.indicators import (
    calcular_atr,
    detectar_swings,
    detectar_tendencia,
    sesion_activa,
    en_sesion_activa,
    obtener_swing_highs,
    obtener_swing_lows,
)
from strategy.smc_signals import (
    Señal,
    FVG,
    OrderBlock,
    detectar_fvg,
    detectar_order_blocks,
    fvg_en_precio,
    ob_en_precio,
    detectar_bos,
    evaluar_señal,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#  ESTRUCTURAS
# ══════════════════════════════════════════════════════════

@dataclass
class ContextoHTF:
    """Resultado del análisis del timeframe alto."""
    timeframe:      str
    tendencia:      str   = "LATERAL"  # ALCISTA | BAJISTA | LATERAL
    ultimo_high:    Optional[tuple] = None   # (timestamp, precio)
    ultimo_low:     Optional[tuple] = None
    fvgs:           list = field(default_factory=list)
    obs:            list = field(default_factory=list)
    atr:            float = 0.0
    precio_actual:  float = 0.0
    # Zona premium/discount
    rango_alto:     float = 0.0   # 50% superior del último swing
    rango_bajo:     float = 0.0   # 50% inferior del último swing
    en_descuento:   bool  = False  # Precio en zona de descuento (para longs)
    en_premium:     bool  = False  # Precio en zona premium (para shorts)


@dataclass
class SeñalMTF:
    """Señal enriquecida con contexto multi-timeframe."""
    # Señal LTF base
    senal_ltf:       Señal = field(default_factory=Señal)
    # Contexto HTF
    contexto_htf:    Optional[ContextoHTF] = None
    # Resultado final
    tiene_señal:     bool  = False
    direccion:       str   = ""
    score_final:     int   = 0
    score_ltf:       int   = 0
    score_htf_bonus: int   = 0
    motivos_mtf:     list  = field(default_factory=list)
    alineacion:      str   = ""   # "ALINEADO" | "OPUESTO" | "NEUTRAL"
    # Niveles (heredados del LTF con contexto HTF)
    precio_entrada:  float = 0.0
    stop_loss:       float = 0.0
    take_profit:     float = 0.0
    sesion:          str   = ""
    atr_ltf:         float = 0.0
    atr_htf:         float = 0.0


# ══════════════════════════════════════════════════════════
#  ANÁLISIS HTF
# ══════════════════════════════════════════════════════════

def analizar_htf(df_htf: pd.DataFrame, timeframe: str) -> ContextoHTF:
    """
    Analiza el timeframe alto y devuelve el contexto estructural.

    Incluye:
    - Tendencia macro (HH/HL o LH/LL)
    - FVGs y OBs del HTF como zonas de interés
    - Zonas premium y descuento basadas en el último swing range
    """
    ctx = ContextoHTF(timeframe=timeframe)

    if df_htf.empty or len(df_htf) < 20:
        ctx.tendencia = "LATERAL"
        return ctx

    atr    = calcular_atr(df_htf)
    swings = detectar_swings(df_htf)
    ctx.atr           = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
    ctx.precio_actual = float(df_htf["close"].iloc[-1])
    ctx.tendencia     = detectar_tendencia(swings, df_htf, len(df_htf) - 1)

    highs = obtener_swing_highs(df_htf, swings)
    lows  = obtener_swing_lows(df_htf,  swings)

    ctx.ultimo_high = highs[-1] if highs else None
    ctx.ultimo_low  = lows[-1]  if lows  else None

    # Zonas premium / descuento
    # Divide el último swing range en 50/50
    # - Premium (>50%): zonas para vender / buscar shorts
    # - Descuento (<50%): zonas para comprar / buscar longs
    if highs and lows:
        swing_high = highs[-1][1]
        swing_low  = lows[-1][1]
        midpoint   = (swing_high + swing_low) / 2
        ctx.rango_alto    = swing_high
        ctx.rango_bajo    = swing_low
        ctx.en_descuento  = ctx.precio_actual <= midpoint
        ctx.en_premium    = ctx.precio_actual >= midpoint

    # FVGs y OBs relevantes del HTF
    ctx.fvgs = detectar_fvg(df_htf, lookback=50)
    ctx.obs  = detectar_order_blocks(df_htf, lookback=50)

    logger.debug("HTF %s | Tendencia: %s | Premium: %s | Descuento: %s",
                 timeframe, ctx.tendencia, ctx.en_premium, ctx.en_descuento)
    return ctx


# ══════════════════════════════════════════════════════════
#  EVALUADOR MTF
# ══════════════════════════════════════════════════════════

def evaluar_senal_mtf(
    df_ltf:  pd.DataFrame,
    df_htf:  pd.DataFrame,
    ltf:     str = None,
    htf:     str = None,
) -> SeñalMTF:
    """
    Evalúa señal con confluencia multi-timeframe.

    Lógica de scoring:
      1. Evalúa señal en LTF normalmente (score base 0-100)
      2. Analiza contexto HTF
      3. Aplica bonus/penalti según alineación:
         - HTF ALCISTA + LTF ALCISTA  → +BONUS_ALINEADO
         - HTF BAJISTA + LTF BAJISTA  → +BONUS_ALINEADO
         - HTF opuesto a LTF          → -PENALTI_OPUESTO (puede cancelar señal)
         - Precio en zona correcta HTF → bonus adicional +10
         - FVG/OB HTF cerca del precio → +15 extra

    Returns:
        SeñalMTF con señal enriquecida y contexto completo
    """
    ltf = ltf or mcfg.LTF
    htf = htf or mcfg.HTF

    resultado = SeñalMTF()

    # ── 1. Señal LTF base ─────────────────────────────────
    atr_ltf    = calcular_atr(df_ltf)
    swings_ltf = detectar_swings(df_ltf)
    senal_ltf  = evaluar_señal(df_ltf, swings_ltf, atr_ltf, len(df_ltf) - 1)

    resultado.senal_ltf      = senal_ltf
    resultado.score_ltf      = senal_ltf.score
    resultado.precio_entrada = senal_ltf.precio_entrada
    resultado.stop_loss      = senal_ltf.stop_loss
    resultado.take_profit    = senal_ltf.take_profit
    resultado.sesion         = senal_ltf.sesion
    resultado.atr_ltf        = senal_ltf.atr
    resultado.motivos_mtf    = list(senal_ltf.motivos)

    # Sin señal LTF → no hay nada que evaluar
    if not senal_ltf.tiene_señal:
        resultado.score_final = senal_ltf.score
        resultado.alineacion  = "NEUTRAL"
        return resultado

    # ── 2. Contexto HTF ───────────────────────────────────
    ctx = analizar_htf(df_htf, htf)
    resultado.contexto_htf = ctx
    resultado.atr_htf      = ctx.atr

    score_adj = senal_ltf.score
    bonus_htf = 0

    # ── 3. Alineación tendencia HTF ↔ LTF ─────────────────
    if ctx.tendencia == "LATERAL":
        resultado.alineacion = "NEUTRAL"
        resultado.motivos_mtf.append(f"HTF {htf}: lateral (sin filtro)")

    elif ctx.tendencia == senal_ltf.direccion:
        resultado.alineacion = "ALINEADO"
        bonus_htf += mcfg.BONUS_ALINEADO
        resultado.motivos_mtf.append(
            f"✅ HTF {htf} alineado ({ctx.tendencia}) +{mcfg.BONUS_ALINEADO}pts"
        )

    else:
        resultado.alineacion = "OPUESTO"
        bonus_htf -= mcfg.PENALTI_OPUESTO
        resultado.motivos_mtf.append(
            f"❌ HTF {htf} opuesto ({ctx.tendencia} vs {senal_ltf.direccion}) -{mcfg.PENALTI_OPUESTO}pts"
        )

    # ── 4. Zona premium / descuento HTF ───────────────────
    if senal_ltf.direccion == "ALCISTA" and ctx.en_descuento:
        bonus_htf += 10
        resultado.motivos_mtf.append(f"✅ Precio en zona descuento HTF +10pts")
    elif senal_ltf.direccion == "BAJISTA" and ctx.en_premium:
        bonus_htf += 10
        resultado.motivos_mtf.append(f"✅ Precio en zona premium HTF +10pts")

    # ── 5. FVG/OB del HTF como confluencia adicional ──────
    precio = senal_ltf.precio_entrada
    tipo_buscar = "ALCISTA" if senal_ltf.direccion == "ALCISTA" else "BAJISTA"

    fvg_htf = fvg_en_precio(ctx.fvgs, precio, tipo_buscar, margen_pct=0.005)
    if fvg_htf:
        bonus_htf += 15
        resultado.motivos_mtf.append(
            f"✅ FVG HTF {htf} [{fvg_htf.bottom:.0f}–{fvg_htf.top:.0f}] +15pts"
        )

    ob_htf = ob_en_precio(ctx.obs, precio, tipo_buscar, margen_pct=0.006)
    if ob_htf:
        bonus_htf += 15
        resultado.motivos_mtf.append(
            f"✅ OB HTF {htf} [{ob_htf.bottom:.0f}–{ob_htf.top:.0f}] +15pts"
        )

    # ── 6. Score final ────────────────────────────────────
    score_final = score_adj + bonus_htf
    score_final = max(0, min(score_final, 100))  # clamp 0-100

    resultado.score_final     = score_final
    resultado.score_htf_bonus = bonus_htf

    # ── 7. Decisión final ─────────────────────────────────
    # La señal es válida si:
    #   a) Score final >= mínimo
    #   b) No está OPUESTO al HTF (penalti ya rebaja el score)
    if score_final >= scfg.SCORE_MINIMO and resultado.alineacion != "OPUESTO":
        resultado.tiene_señal = True
        resultado.direccion   = senal_ltf.direccion

        # Ajustar TP si hay swing HTF como objetivo
        # IMPORTANTE: validar que el nuevo TP sea favorable al entry
        # (mayor que entry en LONG, menor que entry en SHORT)
        if senal_ltf.direccion == "ALCISTA" and ctx.ultimo_high:
            tp_htf = ctx.ultimo_high[1]
            tp_candidato = round(tp_htf * 0.995, 2)  # ligeramente antes del swing
            if tp_htf > precio and tp_candidato > resultado.precio_entrada:
                resultado.take_profit = tp_candidato
                resultado.motivos_mtf.append(f"TP ajustado a swing HTF ${tp_htf:.0f}")
        elif senal_ltf.direccion == "BAJISTA" and ctx.ultimo_low:
            tp_htf = ctx.ultimo_low[1]
            tp_candidato = round(tp_htf * 1.005, 2)
            if tp_htf < precio and tp_candidato < resultado.precio_entrada:
                resultado.take_profit = tp_candidato
                resultado.motivos_mtf.append(f"TP ajustado a swing HTF ${tp_htf:.0f}")
    else:
        if resultado.alineacion == "OPUESTO":
            resultado.motivos_mtf.append("Señal cancelada: HTF opuesto a dirección LTF")
        elif score_final < scfg.SCORE_MINIMO:
            resultado.motivos_mtf.append(
                f"Score insuficiente tras ajuste MTF: {score_final}/100"
            )

    logger.info(
        "MTF | LTF %s: %s score=%d | HTF %s: %s | Alineación: %s | "
        "Score final: %d | Señal: %s",
        ltf, senal_ltf.direccion, senal_ltf.score,
        htf, ctx.tendencia, resultado.alineacion,
        score_final, "SI" if resultado.tiene_señal else "NO"
    )

    return resultado


# ══════════════════════════════════════════════════════════
#  ANÁLISIS COMPLETO MTF (para el bot en vivo)
# ══════════════════════════════════════════════════════════

def analizar_mercado_mtf(df_ltf: pd.DataFrame,
                          df_htf: pd.DataFrame) -> dict:
    """
    Análisis completo MTF. Reemplaza analizar_mercado() del bot.
    Retorna dict compatible con el journal y el ciclo del bot.
    """
    senal_mtf = evaluar_senal_mtf(df_ltf, df_htf)
    ctx       = senal_mtf.contexto_htf

    # Construir objeto Señal compatible con el resto del sistema
    # usando los datos enriquecidos del MTF
    senal_final = senal_mtf.senal_ltf
    senal_final.tiene_señal    = senal_mtf.tiene_señal
    senal_final.direccion      = senal_mtf.direccion
    senal_final.score          = senal_mtf.score_final
    senal_final.motivos        = senal_mtf.motivos_mtf
    senal_final.precio_entrada = senal_mtf.precio_entrada
    senal_final.stop_loss      = senal_mtf.stop_loss
    senal_final.take_profit    = senal_mtf.take_profit

    return {
        "señal":       senal_final,
        "senal_mtf":   senal_mtf,
        "contexto_htf": ctx,
        "alineacion":  senal_mtf.alineacion,
        "score_ltf":   senal_mtf.score_ltf,
        "score_htf_bonus": senal_mtf.score_htf_bonus,
        "score_final": senal_mtf.score_final,
        "tendencia_htf": ctx.tendencia if ctx else "N/A",
        "tendencia_ltf": senal_mtf.senal_ltf.tendencia,
        "atr_ltf":     senal_mtf.atr_ltf,
        "atr_htf":     senal_mtf.atr_htf,
        "fvgs_htf":    ctx.fvgs if ctx else [],
        "obs_htf":     ctx.obs  if ctx else [],
    }
