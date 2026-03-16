"""
strategy/profiles/profile_ema_filter.py
─────────────────────────────────────────
Perfil EMA — filtros basados en medias móviles.

Filtros implementados (activables individualmente desde .env):
  1. EMA 50 y EMA 200 en HTF (1H) como filtro de tendencia macro
  2. Distancia precio < N% de la EMA 200 (evita entrar muy extendido)
  3. Vela de confirmación en LTF (engulfing o martillo) como gatillo

TODO: Este perfil está preparado pero los filtros están en modo
      PASS_THROUGH hasta que se active desde .env.
      Activar cada filtro individualmente:
        EMA_FILTER_ENABLED=true
        EMA_DISTANCE_MAX_PCT=3.0
        EMA_CONFIRM_CANDLE=true

Para combinarlo con ob_bos, ver ProfileCombined más abajo.
"""

from __future__ import annotations
import os
import pandas as pd
import numpy as np

from strategy.profiles.base_profile import BaseProfile, FilterContext, FilterResult
from utils.logger import get_logger

logger = get_logger(__name__)


def _calcular_ema(serie: pd.Series, periodo: int) -> pd.Series:
    return serie.ewm(span=periodo, adjust=False).mean()


class ProfileEmaFilter(BaseProfile):

    @property
    def nombre(self) -> str:
        return "ema_filter"

    @property
    def descripcion(self) -> str:
        return "EMA 50/200 en HTF + distancia precio + vela de confirmación"

    def necesita_htf(self) -> bool:
        return True

    def filtros(self) -> list:
        filtros = []
        if os.getenv("EMA_FILTER_ENABLED", "false").lower() == "true":
            filtros.append(self.filtro_ema_tendencia_htf)
        if os.getenv("EMA_DISTANCE_MAX_PCT", ""):
            filtros.append(self.filtro_distancia_ema200)
        if os.getenv("EMA_CONFIRM_CANDLE", "false").lower() == "true":
            filtros.append(self.filtro_vela_confirmacion)
        return filtros

    # ══════════════════════════════════════════════════════
    #  FILTRO 1: EMA 50/200 en HTF
    # ══════════════════════════════════════════════════════

    def filtro_ema_tendencia_htf(self, ctx: FilterContext) -> FilterResult:
        """
        Verifica alineación con EMA 50 y EMA 200 en el HTF.
          - Precio > EMA200 HTF → contexto alcista
          - Precio < EMA200 HTF → contexto bajista
          - Precio entre EMA50 y EMA200 → zona de incertidumbre (filtrar)
        """
        if ctx.df_htf.empty or len(ctx.df_htf) < 200:
            return FilterResult(pasa=True, motivo="HTF insuficiente para EMA — filtro omitido")

        close_htf = ctx.df_htf["close"]
        ema50  = _calcular_ema(close_htf, 50).iloc[-1]
        ema200 = _calcular_ema(close_htf, 200).iloc[-1]
        precio = ctx.df_htf["close"].iloc[-1]

        direccion = ctx.señal.direccion

        if direccion == "ALCISTA":
            if precio > ema200 and precio > ema50:
                return FilterResult(
                    pasa=True,
                    motivo=f"EMA HTF alcista: precio ({precio:.0f}) > EMA200 ({ema200:.0f})",
                    bonus=5
                )
            elif precio < ema200:
                return FilterResult(
                    pasa=False,
                    motivo=f"EMA HTF rechaza LONG: precio ({precio:.0f}) < EMA200 ({ema200:.0f})"
                )
            else:
                return FilterResult(
                    pasa=False,
                    motivo=f"EMA HTF: precio entre EMA50 ({ema50:.0f}) y EMA200 ({ema200:.0f}) — zona incierta"
                )

        else:  # BAJISTA
            if precio < ema200 and precio < ema50:
                return FilterResult(
                    pasa=True,
                    motivo=f"EMA HTF bajista: precio ({precio:.0f}) < EMA200 ({ema200:.0f})",
                    bonus=5
                )
            elif precio > ema200:
                return FilterResult(
                    pasa=False,
                    motivo=f"EMA HTF rechaza SHORT: precio ({precio:.0f}) > EMA200 ({ema200:.0f})"
                )
            else:
                return FilterResult(
                    pasa=False,
                    motivo=f"EMA HTF: precio entre EMA50 y EMA200 — zona incierta"
                )

    # ══════════════════════════════════════════════════════
    #  FILTRO 2: Distancia precio < N% de EMA 200
    # ══════════════════════════════════════════════════════

    def filtro_distancia_ema200(self, ctx: FilterContext) -> FilterResult:
        """
        Evita entrar cuando el precio está muy extendido respecto
        a la EMA 200 del HTF. Por defecto máximo 3%.
        """
        max_pct = float(os.getenv("EMA_DISTANCE_MAX_PCT", "3.0"))

        if ctx.df_htf.empty or len(ctx.df_htf) < 200:
            return FilterResult(pasa=True, motivo="HTF insuficiente — filtro distancia omitido")

        close_htf = ctx.df_htf["close"]
        ema200    = _calcular_ema(close_htf, 200).iloc[-1]
        precio    = ctx.precio
        distancia = abs(precio - ema200) / ema200 * 100

        if distancia <= max_pct:
            return FilterResult(
                pasa=True,
                motivo=f"Distancia EMA200: {distancia:.1f}% ≤ {max_pct}%"
            )
        else:
            return FilterResult(
                pasa=False,
                motivo=f"Precio muy extendido: {distancia:.1f}% de EMA200 (máx {max_pct}%)"
            )

    # ══════════════════════════════════════════════════════
    #  FILTRO 3: Vela de confirmación en LTF
    # ══════════════════════════════════════════════════════

    def filtro_vela_confirmacion(self, ctx: FilterContext) -> FilterResult:
        """
        Verifica que la última vela cerrada sea una vela de confirmación:
          - Engulfing: cuerpo de la vela actual envuelve al de la anterior
          - Martillo/Pin bar: mecha inferior larga (alcista) o superior (bajista)
        """
        df  = ctx.df
        idx = ctx.idx

        if idx < 1:
            return FilterResult(pasa=True, motivo="Sin vela previa — confirmación omitida")

        # Vela actual y anterior
        o_cur  = df["open"].iloc[idx]
        c_cur  = df["close"].iloc[idx]
        h_cur  = df["high"].iloc[idx]
        l_cur  = df["low"].iloc[idx]
        o_prev = df["open"].iloc[idx - 1]
        c_prev = df["close"].iloc[idx - 1]

        cuerpo_cur  = abs(c_cur - o_cur)
        rango_cur   = h_cur - l_cur
        cuerpo_prev = abs(c_prev - o_prev)

        direccion = ctx.señal.direccion

        # ── Engulfing ────────────────────────────────────
        if direccion == "ALCISTA":
            # Engulfing alcista: vela actual alcista envuelve a la bajista anterior
            es_engulfing = (
                c_cur > o_cur and          # Vela actual alcista
                c_prev < o_prev and        # Vela anterior bajista
                c_cur > o_prev and         # Cierre por encima del open previo
                o_cur < c_prev             # Open por debajo del close previo
            )
            # Martillo: mecha inferior > 2x el cuerpo
            mecha_inf   = min(o_cur, c_cur) - l_cur
            es_martillo = (
                c_cur > o_cur and          # Vela alcista
                rango_cur > 0 and
                mecha_inf > cuerpo_cur * 2 and
                cuerpo_cur > rango_cur * 0.15  # Cuerpo no trivial
            )
        else:  # BAJISTA
            # Engulfing bajista
            es_engulfing = (
                c_cur < o_cur and
                c_prev > o_prev and
                c_cur < o_prev and
                o_cur > c_prev
            )
            # Pin bar: mecha superior > 2x el cuerpo
            mecha_sup   = h_cur - max(o_cur, c_cur)
            es_martillo = (
                c_cur < o_cur and
                rango_cur > 0 and
                mecha_sup > cuerpo_cur * 2 and
                cuerpo_cur > rango_cur * 0.15
            )

        if es_engulfing:
            return FilterResult(
                pasa=True,
                motivo="Vela engulfing de confirmación",
                bonus=8
            )
        elif es_martillo:
            tipo = "martillo" if direccion == "ALCISTA" else "pin bar"
            return FilterResult(
                pasa=True,
                motivo=f"Vela {tipo} de confirmación",
                bonus=5
            )
        else:
            return FilterResult(
                pasa=False,
                motivo="Sin vela de confirmación (requiere engulfing o martillo/pin bar)"
            )


# ══════════════════════════════════════════════════════════
#  PERFIL COMBINADO: OB+BOS + EMA
# ══════════════════════════════════════════════════════════

class ProfileObBosEma(BaseProfile):
    """
    Combina el filtro OB+BOS con los filtros EMA.
    Útil para testear ambos juntos sin crear un archivo nuevo.
    """

    def __init__(self):
        from strategy.profiles.profile_ob_bos import ProfileObBos
        self._ob_bos = ProfileObBos()
        self._ema    = ProfileEmaFilter()

    @property
    def nombre(self) -> str:
        return "ob_bos_ema"

    @property
    def descripcion(self) -> str:
        return "OB validado por BOS + filtros EMA 50/200 + distancia + confirmación"

    def necesita_htf(self) -> bool:
        return True

    def filtros(self) -> list:
        return self._ob_bos.filtros() + self._ema.filtros()
